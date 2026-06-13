#!/usr/bin/env python3
"""Record a mailbox-derived application event without storing email bodies."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from tracker import upsert_tracker


EVENT_DEFAULTS = {
    "application_received": ("submitted", "application_received", "Await company response"),
    "interview_invitation": ("interview", "interview_scheduled", "Prepare for interview"),
    "interview_completed": ("interview", "interview_completed", "Await interviewer feedback"),
    "next_round": ("interview", "next_round", "Schedule and prepare for next round"),
    "offer": ("offer", "offer_received", "Review offer and response deadline"),
    "rejected": ("rejected", "rejected", "No action"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record an Outlook job-status event.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--event", choices=sorted(EVENT_DEFAULTS), required=True)
    parser.add_argument("--received-date", default=date.today().isoformat())
    parser.add_argument("--subject", default="")
    parser.add_argument("--message-url", default="")
    parser.add_argument("--contact-name", default="")
    parser.add_argument("--stage", default="")
    parser.add_argument("--next-action", default="")
    parser.add_argument("--follow-up-date", default="")
    parser.add_argument("--follow-up-days", type=int, default=7)
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status, default_stage, default_action = EVENT_DEFAULTS[args.event]
    follow_up = args.follow_up_date
    if not follow_up and args.event in {"interview_completed", "application_received"}:
        follow_up = (
            date.fromisoformat(args.received_date) + timedelta(days=args.follow_up_days)
        ).isoformat()

    values = {
        "company": args.company,
        "role": args.role,
        "url": args.url,
        "status": status,
        "stage": args.stage or default_stage,
        "stage_date": args.received_date,
        "next_action": args.next_action or default_action,
        "follow_up_date": follow_up,
        "contact_name": args.contact_name,
        "last_contact_date": args.received_date,
        "email_status": args.event,
        "email_subject": args.subject,
        "email_url": args.message_url,
        "email_last_checked": date.today().isoformat(),
    }
    if args.notes:
        values["notes"] = args.notes
    upsert_tracker(args.tracker, values)
    print(f"Recorded {args.event} for {args.company} - {args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
