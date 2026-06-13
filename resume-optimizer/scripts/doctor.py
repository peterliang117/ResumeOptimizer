#!/usr/bin/env python3
"""Validate local setup for the job application pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local job-pipeline setup.")
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Only validate files that should exist in a clean public checkout.",
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

    print(f"Python: {sys.version.split()[0]}")
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
