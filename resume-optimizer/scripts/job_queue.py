#!/usr/bin/env python3
"""Manage a simple CSV queue of job opportunities."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


QUEUE_FIELDS = ["company", "role", "source", "url", "status", "priority", "notes"]


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
    add.add_argument("--notes", default="")

    subparsers.add_parser("list", help="List queued jobs.")
    subparsers.add_parser("next", help="Print the next queued job.")

    update = subparsers.add_parser("update", help="Update a queued job status by URL.")
    update.add_argument("--url", required=True)
    update.add_argument("--status", required=True)
    update.add_argument("--notes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_rows(args.queue)

    if args.command == "add":
        rows.append(
            {
                "company": args.company,
                "role": args.role,
                "source": args.source,
                "url": args.url,
                "status": "queued",
                "priority": args.priority,
                "notes": args.notes,
            }
        )
        write_rows(args.queue, rows)
        print(f"Added queued job: {args.company} - {args.role}")
        return 0

    if args.command == "list":
        for index, row in enumerate(rows, start=1):
            print(
                f"{index}. [{row.get('status', '')}] {row.get('company', '')} - "
                f"{row.get('role', '')} ({row.get('source', '')})"
            )
        return 0

    if args.command == "next":
        for row in rows:
            if row.get("status", "queued") == "queued":
                print(
                    f"{row.get('company', '')}\t{row.get('role', '')}\t"
                    f"{row.get('source', '')}\t{row.get('url', '')}"
                )
                return 0
        print("No queued jobs.")
        return 1

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
