#!/usr/bin/env python3
"""Archive stale unsubmitted application work before a fresh search cycle."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from job_queue import read_rows as read_queue_rows
from job_queue import write_rows as write_queue_rows
from tracker import read_rows as read_tracker_rows
from tracker import write_rows as write_tracker_rows


DEFAULT_STATUSES = {
    "queued",
    "analyzed",
    "resume_ready",
    "application_started",
    "blocked_needs_user_input",
    "manual_apply_needed",
    "needs_manual_review",
}


def parse_statuses(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def append_note(existing: str, archive_date: str) -> str:
    note = f"Archived as outdated on {archive_date} before a fresh job-search run; no application submitted."
    return f"{existing.rstrip()} {note}".strip() if existing.strip() else note


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mark stale unsubmitted tracker and queue rows as outdated."
    )
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument(
        "--statuses",
        default=",".join(sorted(DEFAULT_STATUSES)),
        help="Comma-separated statuses to archive. Submitted, interview, offer, and existing terminal states are excluded by default.",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag, print the planned updates only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    statuses = parse_statuses(args.statuses)
    archive_date = date.today().isoformat()
    tracker_rows = read_tracker_rows(args.tracker)
    queue_rows = read_queue_rows(args.queue)

    tracker_matches = [row for row in tracker_rows if row.get("status", "") in statuses]
    queue_matches = [row for row in queue_rows if row.get("status", "") in statuses]
    for row in tracker_matches:
        print(f"tracker\t{row.get('company', '')}\t{row.get('role', '')}\t{row.get('status', '')} -> outdated")
    for row in queue_matches:
        print(f"queue\t{row.get('company', '')}\t{row.get('role', '')}\t{row.get('status', '')} -> outdated")
    print(f"tracker_matches={len(tracker_matches)}")
    print(f"queue_matches={len(queue_matches)}")

    if not args.apply:
        print("Dry run only. Re-run with --apply to write changes.")
        return 0

    for row in tracker_matches:
        row["status"] = "outdated"
        row["stage"] = "outdated"
        row["stage_date"] = archive_date
        row["next_action"] = "No action; stale unsubmitted application archived before fresh search."
        row["notes"] = append_note(row.get("notes", ""), archive_date)
    for row in queue_matches:
        row["status"] = "outdated"
        row["notes"] = append_note(row.get("notes", ""), archive_date)

    write_tracker_rows(args.tracker, tracker_rows)
    write_queue_rows(args.queue, queue_rows)
    print("Archived stale unsubmitted application work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
