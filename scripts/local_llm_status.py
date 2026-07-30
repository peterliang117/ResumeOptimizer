#!/usr/bin/env python3
"""Inspect local LLM routing and optional server availability."""

from __future__ import annotations

import argparse
import json
import sys

from local_llm import (
    LocalLLMError,
    has_local_llm_config,
    list_local_models,
    local_llm_base_url,
    route_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show local LLM routing setup.")
    parser.add_argument(
        "--check-server",
        action="store_true",
        help="Call the local OpenAI-compatible /models endpoint.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when configured route models are not present on the server.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    routes = route_summary()
    print(f"LOCAL_LLM_BASE_URL: {local_llm_base_url()}")
    print(f"LOCAL_LLM_ENABLED/configured: {has_local_llm_config()}")
    print("Routes:")
    print(json.dumps(routes, indent=2, ensure_ascii=False))

    if not args.check_server:
        return 0

    try:
        models = list_local_models()
    except LocalLLMError as exc:
        print(f"Local LLM server check failed: {exc}")
        return 1 if args.strict else 0

    print("Server models:")
    for model in models:
        print(f"- {model}")

    required = sorted({route["model"] for route in routes.values()})
    missing = [model for model in required if model not in models]
    if missing:
        print("Configured route models missing from server:")
        for model in missing:
            print(f"- {model}")
        return 1 if args.strict else 0

    print("Local LLM route models are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
