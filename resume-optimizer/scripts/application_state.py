#!/usr/bin/env python3
"""Update queue and tracker state together for one application."""

from __future__ import annotations

import argparse
from pathlib import Path

from job_queue import read_rows as read_queue_rows
from job_queue import write_rows as write_queue_rows
from tracker import upsert_tracker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update jobs/queue.csv and tracker/applications.csv together.")
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--priority", default="")
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


def update_queue(
    queue_path: Path,
    *,
    company: str,
    role: str,
    status: str,
    source: str,
    url: str,
    priority: str,
    notes: str,
) -> None:
    rows = read_queue_rows(queue_path)
    for row in rows:
        same_url = url and row.get("url", "") == url
        same_identity = (
            row.get("company", "").strip().lower() == company.strip().lower()
            and row.get("role", "").strip().lower() == role.strip().lower()
        )
        if not (same_url or same_identity):
            continue
        row["status"] = status
        if source:
            row["source"] = source
        if url:
            row["url"] = url
        if priority:
            row["priority"] = priority
        if notes:
            row["notes"] = notes
        write_queue_rows(queue_path, rows)
        return

    rows.append(
        {
            "company": company,
            "role": role,
            "source": source,
            "url": url,
            "status": status,
            "priority": priority or "medium",
            "notes": notes,
        }
    )
    write_queue_rows(queue_path, rows)


def main() -> int:
    args = parse_args()
    update_queue(
        args.queue,
        company=args.company,
        role=args.role,
        status=args.status,
        source=args.source,
        url=args.url,
        priority=args.priority,
        notes=args.notes,
    )
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
    )
    print(f"Updated state for {args.company} - {args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
