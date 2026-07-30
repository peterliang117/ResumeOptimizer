#!/usr/bin/env python3
"""Append and update job application tracker rows."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

try:
    from job_store import DEFAULT_DB, DEFAULT_TRACKER, database_enabled, export_legacy_csv, upsert_application
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, DEFAULT_TRACKER, database_enabled, export_legacy_csv, upsert_application


TRACKER_FIELDS = [
    "date",
    "company",
    "role",
    "source",
    "url",
    "status",
    "resume_file",
    "application_folder",
    "submitted",
    "follow_up_date",
    "stage",
    "stage_date",
    "next_action",
    "contact_name",
    "last_contact_date",
    "email_status",
    "email_subject",
    "email_url",
    "email_last_checked",
    "notes",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in TRACKER_FIELDS})
    if path.resolve() == DEFAULT_TRACKER.resolve() and database_enabled():
        from job_store import sync_tracker_csv

        sync_tracker_csv(path)


def find_row(rows: list[dict[str, str]], company: str, role: str, url: str) -> int | None:
    for index, row in enumerate(rows):
        if url and row.get("url") == url:
            return index
        if row.get("company", "").lower() == company.lower() and row.get("role", "").lower() == role.lower():
            return index
    return None


def upsert_tracker(path: Path, values: dict[str, str], db_path: Path | None = None) -> None:
    selected_db = db_path or DEFAULT_DB
    if path.resolve() == DEFAULT_TRACKER.resolve() and database_enabled(selected_db):
        upsert_application(values, path=selected_db)
        export_legacy_csv(tracker_path=path, path=selected_db)
        return
    rows = read_rows(path)
    index = find_row(rows, values.get("company", ""), values.get("role", ""), values.get("url", ""))
    if index is None:
        row = {field: "" for field in TRACKER_FIELDS}
        row["date"] = values.get("date") or date.today().isoformat()
        rows.append(row)
        index = len(rows) - 1

    for field in TRACKER_FIELDS:
        if values.get(field) is not None:
            rows[index][field] = values.get(field, rows[index].get(field, ""))
    if not rows[index].get("date"):
        rows[index]["date"] = date.today().isoformat()

    write_rows(path, rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the application tracker CSV.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--status", required=True)
    parser.add_argument("--resume-file", default="")
    parser.add_argument("--application-folder", default="")
    parser.add_argument("--submitted", default="")
    parser.add_argument("--follow-up-date", default="")
    parser.add_argument("--stage")
    parser.add_argument("--stage-date")
    parser.add_argument("--next-action")
    parser.add_argument("--contact-name")
    parser.add_argument("--last-contact-date")
    parser.add_argument("--email-status")
    parser.add_argument("--email-subject")
    parser.add_argument("--email-url")
    parser.add_argument("--email-last-checked")
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    upsert_tracker(
        args.tracker,
        {
            "company": args.company,
            "role": args.role,
            "source": args.source,
            "url": args.url,
            "status": args.status,
            "resume_file": args.resume_file,
            "application_folder": args.application_folder,
            "submitted": args.submitted,
            "follow_up_date": args.follow_up_date,
            "stage": args.stage,
            "stage_date": args.stage_date,
            "next_action": args.next_action,
            "contact_name": args.contact_name,
            "last_contact_date": args.last_contact_date,
            "email_status": args.email_status,
            "email_subject": args.email_subject,
            "email_url": args.email_url,
            "email_last_checked": args.email_last_checked,
            "notes": args.notes,
        },
        db_path=args.db,
    )
    print(f"Updated tracker: {args.tracker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
