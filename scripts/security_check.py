#!/usr/bin/env python3
"""Scan tracked and unignored local files for common secret patterns."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "azure_openai_key_assignment": re.compile(
        r"\bAZURE_OPENAI_API_KEY\s*=\s*[\"']?(?!\.\.\.|your-key|your[_-]?key)([A-Za-z0-9+/=_-]{20,})[\"']?",
        re.IGNORECASE,
    ),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "github_classic_pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{30,}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

ALLOWLIST_PATTERNS = {
    "env_var_reference": re.compile(r"\bOPENAI_API_KEY\b"),
    "placeholder_key": re.compile(r"your[_-]?(api[_-]?)?key|your_api_key_here", re.IGNORECASE),
}


def git_paths() -> list[Path]:
    commands = [
        ["git", "ls-files"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    paths: list[Path] = []
    for command in commands:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        paths.extend(Path(line) for line in result.stdout.splitlines() if line.strip())
    return sorted(set(paths))


def should_skip(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {".git", ".venv", "__pycache__"}) or path.suffix.lower() in {
        ".docx",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".exe",
        ".dll",
        ".pyd",
    }


def mask(value: str) -> str:
    if len(value) <= 12:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[str] = []
    for name, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(f"{path}: {name}: {mask(match.group(0))}")
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Git-tracked and unignored files for common secrets.")
    parser.add_argument("--fail-on-finding", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    findings: list[str] = []
    for path in git_paths():
        if path.exists() and path.is_file() and not should_skip(path):
            findings.extend(scan_file(path))

    if findings:
        print("Potential secrets found:")
        for finding in findings:
            print(f"- {finding}")
        return 1 if args.fail_on_finding else 0

    print("No common secret patterns found in tracked or unignored files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
