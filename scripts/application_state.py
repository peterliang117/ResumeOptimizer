#!/usr/bin/env python3
"""Update queue and tracker state together for one application."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from job_store import DEFAULT_DB, database_enabled, export_legacy_csv, record_application_state
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, database_enabled, export_legacy_csv, record_application_state
from job_queue import read_rows as read_queue_rows
from job_queue import write_rows as write_queue_rows
from tracker import upsert_tracker
try:
    from workflow_optimizer import finish_attempt, record_attempt
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.workflow_optimizer import finish_attempt, record_attempt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update jobs/queue.csv and tracker/applications.csv together.")
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
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
    parser.add_argument("--attempt-id", type=int, help="Timed workflow attempt to finish with this state change.")
    parser.add_argument("--workflow-stage", help="Optimization stage such as ats_fill or ats_upload.")
    parser.add_argument("--platform", default="unknown", help="ATS or workflow platform used by the attempt.")
    parser.add_argument("--barrier", default="", help="Normalized barrier encountered during this attempt.")
    parser.add_argument(
        "--workflow-outcome",
        choices=["success", "failure", "handoff", "skipped", "timeout"],
        help="Attempt outcome. When omitted, it is derived conservatively from application status.",
    )
    parser.add_argument("--action-taken", default="")
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--interaction-count", type=int)
    parser.add_argument("--token-estimate", type=int)
    parser.add_argument("--workflow-source-ref", default="")
    return parser.parse_args()


def inferred_workflow_outcome(status: str, barrier: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"submitted", "application_started", "resume_ready", "analyzed"} and not barrier:
        return "success"
    if normalized in {"blocked_needs_user_input", "pending_remote_approval", "manual_apply_needed"}:
        return "handoff"
    if normalized in {"expired", "skipped", "rejected", "closed", "outdated"}:
        return "skipped"
    return "failure" if barrier else "success"


def canonical_job_url(url: str) -> str:
    """Keep Ashby posting and application state on one tracker identity."""
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/")
    if parsed.hostname == "jobs.ashbyhq.com" and path.endswith("/application"):
        path = path[: -len("/application")]
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return url.strip()


def record_workflow_result(args: argparse.Namespace) -> None:
    if args.attempt_id is None and not args.barrier:
        return
    outcome = args.workflow_outcome or inferred_workflow_outcome(args.status, args.barrier)
    if args.attempt_id is not None:
        finish_attempt(
            path=args.db,
            attempt_id=args.attempt_id,
            outcome=outcome,
            barrier=args.barrier,
            action_taken=args.action_taken,
            duration_seconds=args.duration_seconds,
            interaction_count=args.interaction_count,
            token_estimate=args.token_estimate,
            notes=args.notes,
        )
        return
    record_attempt(
        path=args.db,
        stage=args.workflow_stage or args.stage or "unknown",
        platform=args.platform,
        outcome=outcome,
        barrier=args.barrier,
        action_taken=args.action_taken,
        duration_seconds=args.duration_seconds,
        interaction_count=args.interaction_count,
        token_estimate=args.token_estimate,
        company=args.company,
        role=args.role,
        notes=args.notes,
        source_ref=args.workflow_source_ref,
    )


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
    updated = False
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
        updated = True

    if updated:
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
    args.url = canonical_job_url(args.url)
    if database_enabled(args.db):
        record_application_state(
            {
                "company": args.company,
                "role": args.role,
                "status": args.status,
                "source": args.source,
                "url": args.url,
                "priority": args.priority,
                "notes": args.notes,
            },
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
                "stage": args.stage or "",
                "stage_date": args.stage_date or "",
                "next_action": args.next_action or "",
                "contact_name": args.contact_name or "",
                "last_contact_date": args.last_contact_date or "",
                "email_status": args.email_status or "",
                "email_subject": args.email_subject or "",
                "email_url": args.email_url or "",
                "email_last_checked": args.email_last_checked or "",
                "notes": args.notes,
            },
            path=args.db,
        )
        export_legacy_csv(queue_path=args.queue, tracker_path=args.tracker, path=args.db)
        record_workflow_result(args)
        print(f"Updated state for {args.company} - {args.role}")
        return 0
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
    record_workflow_result(args)
    print(f"Updated state for {args.company} - {args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
