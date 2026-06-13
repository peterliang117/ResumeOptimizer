#!/usr/bin/env python3
"""Report Azure OpenAI workflow configuration without printing secrets."""

from __future__ import annotations

import os
import sys

from tailor import LLMUnavailableError, has_azure_config, read_azure_keys_file


def present(value: str | None) -> str:
    return "set" if value else "not set"


def main() -> int:
    print(f"AZURE_OPENAI_DEPLOYMENT: {present(os.getenv('AZURE_OPENAI_DEPLOYMENT'))}")
    print(f"AZURE_OPENAI_KEYS_FILE: {present(os.getenv('AZURE_OPENAI_KEYS_FILE'))}")
    print(f"AZURE_OPENAI_ENDPOINT: {present(os.getenv('AZURE_OPENAI_ENDPOINT'))}")
    print(f"AZURE_OPENAI_API_KEY: {present(os.getenv('AZURE_OPENAI_API_KEY'))}")
    print(f"AZURE_OPENAI_API_KEY_PATH: {present(os.getenv('AZURE_OPENAI_API_KEY_PATH'))}")

    try:
        keys_payload = read_azure_keys_file()
    except LLMUnavailableError as exc:
        print(f"keys_file: invalid ({exc})")
        return 1

    if keys_payload:
        host = keys_payload.get("endpoint", "").split("//", 1)[-1].split("/", 1)[0]
        print(f"keys_file_endpoint_host: {host or 'not set'}")
        print(f"keys_file_api_key: {present(keys_payload.get('api_key'))}")
        print(f"keys_file_deployment: {present(keys_payload.get('deployment'))}")
        print(f"keys_file_api_version: {keys_payload.get('api_version') or 'default'}")
    else:
        print("keys_file: not found")

    print(f"workflow_azure_ready: {has_azure_config()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
