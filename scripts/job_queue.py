#!/usr/bin/env python3
"""Manage a simple CSV queue of job opportunities."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


QUEUE_FIELDS = [
    "company",
    "role",
    "source",
    "url",
    "status",
    "priority",
    "batch_id",
    "match_score",
    "notes",
]

TERMINAL_BATCH_STATUSES = {
    "submitted",
    "blocked_needs_user_input",
    "manual_apply_needed",
    "needs_manual_review",
    "skipped",
    "expired",
    "rejected",
    "rejected_low_match",
    "closed",
    "analysis_failed",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in QUEUE_FIELDS})


def latest_batch_id(rows: list[dict[str, str]]) -> str:
    for row in reversed(rows):
        batch_id = row.get("batch_id", "").strip()
        if batch_id:
            return batch_id
    return ""


def score_value(row: dict[str, str]) -> int:
    try:
        return int(row.get("match_score", "") or 0)
    except ValueError:
        return 0


def sorted_queued_rows(rows: list[dict[str, str]], batch_id: str = "") -> list[dict[str, str]]:
    candidates = [
        row
        for row in rows
        if row.get("status", "queued") == "queued"
        and row.get("url")
        and (not batch_id or row.get("batch_id", "") == batch_id)
    ]
    return sorted(
        candidates,
        key=lambda row: (
            PRIORITY_ORDER.get(row.get("priority", "medium").lower(), 1),
            -score_value(row),
        ),
    )


def batch_progress(
    rows: list[dict[str, str]], batch_id: str, target_size: int
) -> tuple[list[dict[str, str]], list[dict[str, str]], int, bool]:
    batch_rows = [row for row in rows if row.get("batch_id", "") == batch_id]
    terminal = [
        row for row in batch_rows if row.get("status", "") in TERMINAL_BATCH_STATUSES
    ]
    open_rows = [
        row for row in batch_rows if row.get("status", "") not in TERMINAL_BATCH_STATUSES
    ]
    slots_remaining = max(target_size - len(batch_rows), 0)
    refill_ready = bool(batch_rows) and not open_rows
    return terminal, open_rows, slots_remaining, refill_ready


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage jobs/queue.csv.")
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add a job to the queue.")
    add.add_argument("--company", required=True)
    add.add_argument("--role", required=True)
    add.add_argument("--source", default="LinkedIn")
    add.add_argument("--url", required=True)
    add.add_argument("--priority", default="medium")
    add.add_argument("--batch-id", default="")
    add.add_argument("--match-score", type=int)
    add.add_argument("--notes", default="")

    list_parser = subparsers.add_parser("list", help="List queued jobs.")
    list_parser.add_argument("--batch-id", default="")

    next_parser = subparsers.add_parser("next", help="Print the next queued job.")
    next_parser.add_argument("--batch-id", default="")

    batch_status = subparsers.add_parser(
        "batch-status",
        help="Show progress for a batch and whether the next batch may be discovered.",
    )
    batch_status.add_argument("--batch-id", default="")
    batch_status.add_argument("--target-size", type=int, default=10)

    subparsers.add_parser(
        "normalize",
        help="Rewrite the queue using the current schema without changing row values.",
    )

    update = subparsers.add_parser("update", help="Update a queued job status by URL.")
    update.add_argument("--url", required=True)
    update.add_argument("--status", required=True)
    update.add_argument("--notes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_rows(args.queue)

    if args.command == "add":
        if args.match_score is not None and not 0 <= args.match_score <= 100:
            raise SystemExit("--match-score must be between 0 and 100.")
        if any(row.get("url", "").rstrip("/") == args.url.rstrip("/") for row in rows):
            raise SystemExit(f"Queue URL already exists: {args.url}")
        rows.append(
            {
                "company": args.company,
                "role": args.role,
                "source": args.source,
                "url": args.url,
                "status": "queued",
                "priority": args.priority,
                "batch_id": args.batch_id,
                "match_score": "" if args.match_score is None else str(args.match_score),
                "notes": args.notes,
            }
        )
        write_rows(args.queue, rows)
        print(f"Added queued job: {args.company} - {args.role}")
        return 0

    if args.command == "list":
        selected = rows
        if args.batch_id:
            selected = [row for row in rows if row.get("batch_id", "") == args.batch_id]
        for index, row in enumerate(selected, start=1):
            batch = f" batch={row.get('batch_id', '')}" if row.get("batch_id") else ""
            score = f" score={row.get('match_score', '')}" if row.get("match_score") else ""
            print(
                f"{index}. [{row.get('status', '')}] {row.get('company', '')} - "
                f"{row.get('role', '')} ({row.get('source', '')}){batch}{score}"
            )
        return 0

    if args.command == "next":
        queued = sorted_queued_rows(rows, args.batch_id)
        if queued:
            row = queued[0]
            print(
                f"{row.get('company', '')}\t{row.get('role', '')}\t"
                f"{row.get('source', '')}\t{row.get('url', '')}\t"
                f"{row.get('batch_id', '')}\t{row.get('match_score', '')}"
            )
            return 0
        print("No queued jobs.")
        return 1

    if args.command == "batch-status":
        batch_id = args.batch_id or latest_batch_id(rows)
        if not batch_id:
            print("No job batches found.")
            return 1
        batch_rows = [row for row in rows if row.get("batch_id", "") == batch_id]
        terminal, open_rows, slots_remaining, refill_ready = batch_progress(
            rows, batch_id, args.target_size
        )
        print(f"batch_id={batch_id}")
        print(f"total={len(batch_rows)}")
        print(f"terminal_or_handoff={len(terminal)}")
        print(f"open={len(open_rows)}")
        print(f"slots_remaining={slots_remaining}")
        print(f"refill_ready={'yes' if refill_ready else 'no'}")
        for row in open_rows:
            print(
                f"open_job={row.get('company', '')} | {row.get('role', '')} | "
                f"{row.get('status', '')} | score={row.get('match_score', '')}"
            )
        return 0

    if args.command == "normalize":
        write_rows(args.queue, rows)
        print(f"Normalized queue schema: {args.queue}")
        return 0

    if args.command == "update":
        for row in rows:
            if row.get("url") == args.url:
                row["status"] = args.status
                if args.notes is not None:
                    row["notes"] = args.notes
                write_rows(args.queue, rows)
                print(f"Updated queue status: {args.status}")
                return 0
        raise SystemExit(f"Queue URL not found: {args.url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
