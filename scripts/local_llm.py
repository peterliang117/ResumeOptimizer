#!/usr/bin/env python3
"""OpenAI-compatible local LLM routing for job-search tasks."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


class LocalLLMError(RuntimeError):
    """Raised when the local LLM backend cannot complete a request."""


class LocalLLMHTTPError(LocalLLMError):
    """Raised when the local LLM backend returns an HTTP error."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Local LLM API error {status_code}: {message}")
        self.status_code = status_code


@dataclass(frozen=True)
class LocalLLMRoute:
    model_env: str
    default_model: str
    temperature: float
    mode_prefix: str
    reasoning_effort: str
    max_tokens: int


MODEL_ROUTES: dict[str, LocalLLMRoute] = {
    "batch_screening": LocalLLMRoute(
        model_env="LOCAL_LLM_SCREENING_MODEL",
        default_model="qwen3:8b",
        temperature=0.2,
        mode_prefix="/no_think",
        reasoning_effort="none",
        max_tokens=1200,
    ),
    "resume_tailoring": LocalLLMRoute(
        model_env="LOCAL_LLM_RESUME_MODEL",
        default_model="qwen3:14b",
        temperature=0.2,
        mode_prefix="/think",
        reasoning_effort="high",
        max_tokens=2200,
    ),
    "application_answer": LocalLLMRoute(
        model_env="LOCAL_LLM_APPLICATION_MODEL",
        default_model="qwen3:14b",
        temperature=0.3,
        mode_prefix="/no_think",
        reasoning_effort="none",
        max_tokens=1600,
    ),
    "tracker_update": LocalLLMRoute(
        model_env="LOCAL_LLM_SCREENING_MODEL",
        default_model="qwen3:8b",
        temperature=0.0,
        mode_prefix="/no_think",
        reasoning_effort="none",
        max_tokens=800,
    ),
}

ROUTE_ALIASES = {
    "default": "resume_tailoring",
    "job_resume_match": "resume_tailoring",
    "screening": "batch_screening",
    "mailbox_reconcile": "tracker_update",
}

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def env_truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def local_llm_base_url() -> str:
    return os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")


def local_llm_api_key() -> str:
    return os.getenv("LOCAL_LLM_API_KEY", "ollama")


def ollama_api_base_url() -> str:
    configured = os.getenv("LOCAL_LLM_OLLAMA_API_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    base_url = local_llm_base_url()
    if base_url.endswith("/v1"):
        return f"{base_url[:-3]}/api"
    return "http://127.0.0.1:11434/api"


def has_local_llm_config() -> bool:
    route_env_names = {route.model_env for route in MODEL_ROUTES.values()}
    return any(
        [
            env_truthy("LOCAL_LLM_ENABLED"),
            bool(os.getenv("LOCAL_LLM_BASE_URL", "").strip()),
            bool(os.getenv("LOCAL_LLM_MODEL", "").strip()),
            any(os.getenv(name, "").strip() for name in route_env_names),
        ]
    )


def normalize_task(task: str | None) -> str:
    name = (task or "default").strip().lower().replace("-", "_")
    name = ROUTE_ALIASES.get(name, name)
    if name not in MODEL_ROUTES:
        valid = ", ".join(sorted([*MODEL_ROUTES.keys(), *ROUTE_ALIASES.keys()]))
        raise LocalLLMError(f"Unknown local LLM task route '{task}'. Valid routes: {valid}")
    return name


def resolve_route(task: str | None, model_override: str | None = None) -> dict[str, Any]:
    route_name = normalize_task(task)
    route = MODEL_ROUTES[route_name]
    model = (
        (model_override or "").strip()
        or os.getenv(route.model_env, "").strip()
        or os.getenv("LOCAL_LLM_MODEL", "").strip()
        or route.default_model
    )
    return {
        "task": route_name,
        "model": model,
        "temperature": route.temperature,
        "mode_prefix": route.mode_prefix,
        "reasoning_effort": route.reasoning_effort,
        "max_tokens": route.max_tokens,
        "model_env": route.model_env,
    }


def route_summary() -> dict[str, dict[str, Any]]:
    return {name: resolve_route(name) for name in sorted(MODEL_ROUTES)}


def _messages_with_mode_prefix(messages: list[dict[str, Any]], mode_prefix: str) -> list[dict[str, Any]]:
    if not mode_prefix:
        return [dict(message) for message in messages]

    routed_messages = [dict(message) for message in messages]
    for index in range(len(routed_messages) - 1, -1, -1):
        message = routed_messages[index]
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        content = str(message["content"]).lstrip()
        if not content.startswith(("/think", "/no_think")):
            message["content"] = f"{mode_prefix}\n{message['content']}"
        break
    return routed_messages


def _request_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = local_llm_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_chat(payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise LocalLLMError("Install requests to use the local LLM backend.") from exc

    url = f"{local_llm_base_url()}/chat/completions"
    try:
        response = requests.post(url, headers=_request_headers(), json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise LocalLLMError(f"Could not reach local LLM at {url}: {exc}") from exc

    if response.status_code >= 400:
        raise LocalLLMHTTPError(response.status_code, response.text)

    try:
        data = response.json()
    except ValueError as exc:
        raise LocalLLMError("Local LLM response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise LocalLLMError("Local LLM response JSON must be an object.")
    return data


def _payload_variants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [payload]
    optional_keys = ["response_format", "reasoning_effort"]
    for key in optional_keys:
        if key in payload:
            variant = dict(payload)
            variant.pop(key, None)
            variants.append(variant)
    if all(key in payload for key in optional_keys):
        variant = dict(payload)
        for key in optional_keys:
            variant.pop(key, None)
        variants.append(variant)
    return variants


def chat_completion(
    task: str,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    route = resolve_route(task, model_override=model)
    payload: dict[str, Any] = {
        "model": route["model"],
        "messages": _messages_with_mode_prefix(messages, route["mode_prefix"]),
        "temperature": route["temperature"] if temperature is None else temperature,
        "max_tokens": route["max_tokens"] if max_tokens is None else max_tokens,
        "stream": False,
    }
    if json_mode and env_truthy("LOCAL_LLM_JSON_MODE", default=True):
        payload["response_format"] = {"type": "json_object"}
    if route["reasoning_effort"] and env_truthy("LOCAL_LLM_SEND_REASONING_EFFORT", default=True):
        payload["reasoning_effort"] = route["reasoning_effort"]

    last_error: LocalLLMError | None = None
    variants = _payload_variants(payload)
    for index, variant in enumerate(variants):
        try:
            data = _post_chat(variant, timeout=timeout)
            data["_local_llm_route"] = route
            return data
        except LocalLLMHTTPError as exc:
            last_error = exc
            if exc.status_code == 400 and index < len(variants) - 1:
                continue
            raise

    if last_error:
        raise last_error
    raise LocalLLMError("Local LLM request failed without an error response.")


def extract_message_content(response_payload: dict[str, Any]) -> str:
    try:
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalLLMError("Local LLM response did not include message content.") from exc
    if not isinstance(content, str) or not content.strip():
        raise LocalLLMError("Local LLM response content was empty.")
    return content


def parse_json_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LocalLLMError(f"Local LLM response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LocalLLMError("Local LLM response JSON must be an object.")
    return payload


def chat_json(
    task: str,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    response_payload = chat_completion(
        task,
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
        timeout=timeout,
    )
    return parse_json_text(extract_message_content(response_payload))


def chat_json_schema(
    task: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise LocalLLMError("Install requests to use native Ollama structured output.") from exc

    route = resolve_route(task, model_override=model)
    payload: dict[str, Any] = {
        "model": route["model"],
        "messages": _messages_with_mode_prefix(messages, "/no_think"),
        "format": schema,
        "stream": False,
        "think": False,
        "options": {
            "temperature": route["temperature"] if temperature is None else temperature,
            "num_predict": route["max_tokens"] if max_tokens is None else max_tokens,
        },
    }
    url = f"{ollama_api_base_url()}/chat"
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise LocalLLMError(f"Could not reach native Ollama API at {url}: {exc}") from exc
    if response.status_code >= 400:
        raise LocalLLMHTTPError(response.status_code, response.text)
    try:
        data = response.json()
    except ValueError as exc:
        raise LocalLLMError("Native Ollama response was not valid JSON.") from exc
    try:
        content = data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LocalLLMError("Native Ollama response did not include message content.") from exc
    if not isinstance(content, str) or not content.strip():
        raise LocalLLMError("Native Ollama response content was empty.")
    return parse_json_text(content)


def list_local_models(timeout: int = 10) -> list[str]:
    try:
        import requests
    except ImportError as exc:
        raise LocalLLMError("Install requests to inspect local LLM models.") from exc

    url = f"{local_llm_base_url()}/models"
    try:
        response = requests.get(url, headers=_request_headers(), timeout=timeout)
    except requests.RequestException as exc:
        raise LocalLLMError(f"Could not reach local LLM at {url}: {exc}") from exc
    if response.status_code >= 400:
        raise LocalLLMHTTPError(response.status_code, response.text)
    try:
        data = response.json()
    except ValueError as exc:
        raise LocalLLMError("Local LLM model list was not valid JSON.") from exc
    models = data.get("data", [])
    if not isinstance(models, list):
        raise LocalLLMError("Local LLM model list must contain a data array.")
    names = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get("id"), str):
            names.append(model["id"])
    return sorted(names)
