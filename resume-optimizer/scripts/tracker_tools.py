#!/usr/bin/env python3
"""Maintain and inspect the local application tracker."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from tracker import read_rows, write_rows
from verify_tracker import CANONICAL_STATUSES


STATUS_ALIASES = {
    "applied": "submitted",
    "application_submitted": "submitted",
    "resume ready": "resume_ready",
    "started": "application_started",
    "blocked": "blocked_needs_user_input",
    "waiting_user": "applying_waiting_user_answers",
    "waiting_upload": "applying_waiting_resume_upload",
    "low_match": "rejected_low_match",
}

FOLLOW_UP_STATUSES = {"submitted", "application_started", "interview", "offer"}


def normalize_status(value: str) -> str:
    clean = value.strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_ALIASES.get(clean, clean)


def command_status(path: Path) -> int:
    rows = read_rows(path)
    if not rows:
        print("No tracker rows.")
        return 1
    for row in rows:
        print(
            f"{row.get('date', '')}\t{row.get('status', '')}\t"
            f"{row.get('company', '')}\t{row.get('role', '')}\t"
            f"{row.get('follow_up_date', '')}"
        )
    return 0


def command_normalize(path: Path, dry_run: bool) -> int:
    rows = read_rows(path)
    changed = 0
    for row in rows:
        original = row.get("status", "")
        normalized = normalize_status(original)
        if original != normalized:
            row["status"] = normalized
            changed += 1
    print(f"Normalized {changed} status value(s).")
    if not dry_run:
        write_rows(path, rows)
    return 0


def command_dedup(path: Path, dry_run: bool) -> int:
    rows = read_rows(path)
    by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("url", "").strip().lower()
            or f"{row.get('company', '').strip().lower()}::{row.get('role', '').strip().lower()}",
            row.get("role", "").strip().lower(),
        )
        by_key[key].append(row)

    deduped: list[dict[str, str]] = []
    removed = 0
    for group in by_key.values():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        group.sort(
            key=lambda row: (
                row.get("submitted", "") != "",
                row.get("resume_file", "") != "",
                row.get("date", ""),
            ),
            reverse=True,
        )
        keeper = group[0]
        removed += len(group) - 1
        notes = [keeper.get("notes", "").strip()]
        for duplicate in group[1:]:
            note = duplicate.get("notes", "").strip()
            if note:
                notes.append(f"Deduped note: {note}")
        keeper["notes"] = " | ".join(note for note in notes if note)
        deduped.append(keeper)

    deduped.sort(key=lambda row: row.get("date", ""))
    print(f"Removed {removed} duplicate tracker row(s).")
    if not dry_run:
        write_rows(path, deduped)
    return 0


def command_followups(path: Path, days: int, dry_run: bool) -> int:
    rows = read_rows(path)
    updated = 0
    today = date.today()
    for row in rows:
        status = normalize_status(row.get("status", ""))
        if status not in FOLLOW_UP_STATUSES:
            continue
        if row.get("follow_up_date"):
            continue
        anchor_text = row.get("submitted") or row.get("date")
        try:
            anchor = date.fromisoformat(anchor_text)
        except ValueError:
            anchor = today
        row["follow_up_date"] = (anchor + timedelta(days=days)).isoformat()
        updated += 1

    print(f"Set follow-up dates on {updated} row(s).")
    if not dry_run:
        write_rows(path, rows)
    return 0


def command_overdue(path: Path) -> int:
    rows = read_rows(path)
    today = date.today()
    found = 0
    for row in rows:
        follow_up = row.get("follow_up_date", "").strip()
        if not follow_up:
            continue
        try:
            due = date.fromisoformat(follow_up)
        except ValueError:
            continue
        if due <= today and normalize_status(row.get("status", "")) in FOLLOW_UP_STATUSES:
            found += 1
            print(f"{follow_up}\t{row.get('company', '')}\t{row.get('role', '')}\t{row.get('url', '')}")
    if found == 0:
        print("No overdue follow-ups.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tracker utility commands.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("normalize")
    subparsers.add_parser("dedup")
    followups = subparsers.add_parser("set-followups")
    followups.add_argument("--days", type=int, default=7)
    subparsers.add_parser("overdue")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "status":
        return command_status(args.tracker)
    if args.command == "normalize":
        return command_normalize(args.tracker, args.dry_run)
    if args.command == "dedup":
        return command_dedup(args.tracker, args.dry_run)
    if args.command == "set-followups":
        return command_followups(args.tracker, args.days, args.dry_run)
    if args.command == "overdue":
        return command_overdue(args.tracker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
