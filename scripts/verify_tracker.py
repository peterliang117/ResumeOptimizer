#!/usr/bin/env python3
"""Validate local tracker rows for duplicates, statuses, links, and files."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

from tracker import TRACKER_FIELDS

try:
    from job_store import DEFAULT_DB, DEFAULT_TRACKER, database_enabled, tracker_rows as sqlite_tracker_rows
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, DEFAULT_TRACKER, database_enabled, tracker_rows as sqlite_tracker_rows


CANONICAL_STATUSES = {
    "queued",
    "analyzed",
    "resume_ready",
    "application_started",
    "applying_waiting_user_answers",
    "applying_waiting_resume_upload",
    "blocked_needs_user_input",
    "pending_remote_approval",
    "manual_apply_needed",
    "submitted",
    "interview",
    "offer",
    "rejected",
    "rejected_low_match",
    "needs_manual_review",
    "analysis_failed",
    "withdrawn",
    "closed",
    "expired",
    "outdated",
    "skipped",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def valid_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate tracker/applications.csv.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_rows(args.tracker)
    errors: list[str] = []
    warnings: list[str] = []

    if not rows:
        warnings.append(f"No tracker rows found: {args.tracker}")
    else:
        headers = set(rows[0].keys()) if rows else set()
        missing_headers = [field for field in TRACKER_FIELDS if field not in headers]
        if missing_headers:
            errors.append(f"Tracker missing headers: {', '.join(missing_headers)}")

    seen_urls: set[str] = set()
    seen_company_roles: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=2):
        company = row.get("company", "").strip()
        role = row.get("role", "").strip()
        url = row.get("url", "").strip()
        status = row.get("status", "").strip()
        resume_file = row.get("resume_file", "").strip()
        application_folder = row.get("application_folder", "").strip()

        if not company:
            errors.append(f"Row {index}: missing company")
        if not role:
            errors.append(f"Row {index}: missing role")
        if status and status not in CANONICAL_STATUSES:
            warnings.append(f"Row {index}: non-canonical status '{status}'")
        if not valid_url(url):
            errors.append(f"Row {index}: invalid URL '{url}'")

        if url:
            if url in seen_urls:
                warnings.append(f"Row {index}: duplicate URL '{url}'")
            seen_urls.add(url)

        key = (company.lower(), role.lower())
        if company and role:
            if key in seen_company_roles:
                warnings.append(f"Row {index}: duplicate company/role '{company} - {role}'")
            seen_company_roles.add(key)

        if resume_file and not Path(resume_file).exists():
            warnings.append(f"Row {index}: resume file missing locally: {resume_file}")
        if application_folder and not Path(application_folder).exists():
            warnings.append(f"Row {index}: application folder missing locally: {application_folder}")

    if args.tracker.resolve() == DEFAULT_TRACKER.resolve() and database_enabled(args.db):
        database_rows = sqlite_tracker_rows(args.db)
        csv_projection = sorted(
            tuple((row.get(field) or "") for field in TRACKER_FIELDS) for row in rows
        )
        database_projection = sorted(
            tuple((row.get(field) or "") for field in TRACKER_FIELDS) for row in database_rows
        )
        if csv_projection != database_projection:
            errors.append(
                "Tracker CSV export differs from SQLite source of truth; run scripts/migrate_to_sqlite.py --export-csv."
            )

    if errors:
        print("Tracker errors:")
        for error in errors:
            print(f"- {error}")
    if warnings:
        print("Tracker warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if not errors and not warnings:
        print(f"Tracker verification passed: {args.tracker}")
    elif not errors:
        print(f"Tracker verification passed with warnings: {args.tracker}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
