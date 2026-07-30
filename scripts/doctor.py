#!/usr/bin/env python3
"""Validate local setup for the job application pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

from local_llm import LocalLLMError, has_local_llm_config, list_local_models, local_llm_base_url, route_summary


REQUIRED_PRIVATE_FILES = [
    Path("resumes/master.docx"),
    Path("profile/facts.md"),
    Path("profile/application_answers.json"),
    Path("profile/search_criteria.md"),
]

REQUIRED_TEMPLATES = [
    Path("profile/facts.example.md"),
    Path("profile/application_answers.example.json"),
    Path("profile/search_criteria.example.md"),
    Path("jobs/queue.example.csv"),
]

REQUIRED_DIRS = [
    Path("applications"),
    Path("jobs"),
    Path("outputs"),
    Path("profile"),
    Path("resumes"),
    Path("tailored_resumes"),
    Path("tracker"),
]
SQLITE_STORE = Path("data/resume_optimizer.db")

PYTHON_MODULES = ["bs4", "docx", "fitz", "requests"]


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_libreoffice() -> bool:
    candidates = [
        shutil.which("soffice"),
        shutil.which("soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
    ]
    return any(candidate and Path(candidate).exists() for candidate in candidates)


def check_sqlite_store() -> str | None:
    if not SQLITE_STORE.exists():
        return "SQLite store is not initialized; run scripts/migrate_to_sqlite.py --import-csv."
    try:
        with sqlite3.connect(SQLITE_STORE) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        return f"SQLite store could not be opened: {exc}"
    return None if result == "ok" else f"SQLite integrity check failed: {result}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local job-pipeline setup.")
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Only validate files that should exist in a clean public checkout.",
    )
    parser.add_argument(
        "--check-local-llm",
        action="store_true",
        help="Also call the local OpenAI-compatible LLM /models endpoint.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    warnings: list[str] = []

    for directory in REQUIRED_DIRS:
        if not directory.exists():
            if args.public_only and directory in {Path("applications"), Path("outputs"), Path("tailored_resumes")}:
                continue
            errors.append(f"Missing directory: {directory}")

    for template in REQUIRED_TEMPLATES:
        if not template.exists():
            errors.append(f"Missing public template: {template}")

    if not args.public_only:
        for private_file in REQUIRED_PRIVATE_FILES:
            if not private_file.exists():
                warnings.append(f"Missing private local file: {private_file}")

    for module in PYTHON_MODULES:
        if not module_exists(module):
            errors.append(f"Missing Python dependency import: {module}")

    if not check_libreoffice():
        warnings.append("LibreOffice/soffice not found; one-page DOCX verification may fail.")

    sqlite_issue = check_sqlite_store()
    if sqlite_issue:
        warnings.append(sqlite_issue)

    local_llm_models: list[str] | None = None
    if args.check_local_llm:
        try:
            local_llm_models = list_local_models()
        except LocalLLMError as exc:
            warnings.append(f"Local LLM server check failed: {exc}")

    print(f"Python: {sys.version.split()[0]}")
    print(f"Local LLM base URL: {local_llm_base_url()}")
    print(f"Local LLM configured: {has_local_llm_config()}")
    print("Local LLM routes:")
    for route_name, route in route_summary().items():
        print(f"- {route_name}: {route['model']} ({route['mode_prefix']}, temp={route['temperature']})")
    if local_llm_models is not None:
        print("Local LLM server models:")
        for model in local_llm_models:
            print(f"- {model}")
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- {error}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if not errors and not warnings:
        print("Doctor check passed.")
    elif not errors:
        print("Doctor check passed with warnings.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
