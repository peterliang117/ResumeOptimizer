#!/usr/bin/env python3
"""Schedule and apply metadata-only Outlook reconciliation events locally."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from job_store import (
        DEFAULT_DB,
        configure_schedule,
        connection,
        export_legacy_csv,
        initialize,
        mark_schedule_run,
        record_application_state,
        tracker_rows,
    )
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import (
        DEFAULT_DB,
        configure_schedule,
        connection,
        export_legacy_csv,
        initialize,
        mark_schedule_run,
        record_application_state,
        tracker_rows,
    )


EVENT_DEFAULTS = {
    "application_received": ("submitted", "application_received", "Await company response"),
    "interview_invitation": ("interview", "interview_scheduled", "Prepare for interview"),
    "interview_completed": ("interview", "interview_completed", "Await interviewer feedback"),
    "next_round": ("interview", "next_round", "Schedule and prepare for next round"),
    "offer": ("offer", "offer_received", "Review offer and response deadline"),
    "rejected": ("rejected", "rejected", "No action"),
}
ALLOWED_EVENT_FIELDS = {
    "company", "role", "url", "source", "event", "received_date", "subject",
    "message_url", "contact_name", "stage", "next_action", "follow_up_date", "notes",
}
ACTIVE_MAILBOX_STATUSES = {"submitted", "interview", "offer"}
OUTCOME_TASK = "outlook_reconciliation"
ALERT_TASK = "linkedin_alert_discovery"
RECRUITER_TASK = "inbound_recruiter_discovery"
PIPELINE_TASK = "full_application_pipeline"
OUTCOME_INTERVAL_MINUTES = 480
ALERT_INTERVAL_MINUTES = 360
RECRUITER_INTERVAL_MINUTES = 240
PIPELINE_INTERVAL_MINUTES = 120
URGENT_DISCOVERY_INTERVAL_MINUTES = 360
QUEUE_CAPACITY = 10
OPEN_QUEUE_STATUSES = {
    "queued", "resume_ready", "application_started", "analyzed",
    "manual_apply_needed", "blocked_needs_user_input", "pending_remote_approval",
}
ACTIONABLE_QUEUE_STATUSES = {"queued", "resume_ready", "application_started"}


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def existing_application(company: str, role: str, db_path: Path, url: str = "") -> dict | None:
    matches = [
        row
        for row in tracker_rows(db_path)
        if _normalized(row.get("company")) == _normalized(company)
        and _normalized(row.get("role")) == _normalized(role)
    ]
    if url:
        url_matches = [row for row in matches if str(row.get("url") or "") == url]
        if len(url_matches) == 1:
            return url_matches[0]
    return matches[0] if len(matches) == 1 else None


def event_already_applied(existing: dict | None, event: dict, event_type: str, received: str) -> bool:
    if not existing:
        return False
    message_url = str(event.get("message_url") or "")
    if message_url and str(existing.get("email_url") or "") == message_url:
        return True
    return (
        str(existing.get("email_status") or "") == event_type
        and str(existing.get("email_subject") or "") == str(event.get("subject") or "")
        and str(existing.get("stage_date") or "") == received
    )


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def due_tasks(db_path: Path, now: datetime | None = None) -> list[dict]:
    initialize(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 ORDER BY next_run_at, task_name"
        ).fetchall()
        queue_statuses = [str(row["status"] or "") for row in conn.execute(
            "SELECT status FROM jobs"
        ).fetchall()]
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    open_jobs = sum(status in OPEN_QUEUE_STATUSES for status in queue_statuses)
    actionable_jobs = sum(status in ACTIONABLE_QUEUE_STATUSES for status in queue_statuses)
    urgent_refill = open_jobs < QUEUE_CAPACITY and actionable_jobs == 0

    def is_due(value: object) -> bool:
        parsed = _parse_datetime(value)
        if parsed is None:
            return True
        return parsed <= current

    result: list[dict] = []
    for row in rows:
        scheduled_due = is_due(row["next_run_at"])
        due = scheduled_due
        due_reason = "scheduled" if scheduled_due else ""
        if row["task_name"] == ALERT_TASK and not due and urgent_refill:
            last_run = _parse_datetime(row["last_run_at"])
            urgent_after = current - timedelta(minutes=URGENT_DISCOVERY_INTERVAL_MINUTES)
            if last_run is None or last_run <= urgent_after:
                due = True
                due_reason = "urgent_refill"
        result.append(
            {key: row[key] for key in row.keys()}
            | {"due": due, "due_reason": due_reason}
        )
    return result


def apply_event(event: dict, db_path: Path) -> bool:
    unknown = set(event) - ALLOWED_EVENT_FIELDS
    if unknown:
        raise ValueError("Event contains unsupported fields: " + ", ".join(sorted(unknown)))
    event_type = str(event.get("event") or "")
    if event_type not in EVENT_DEFAULTS:
        raise ValueError(f"Unsupported event: {event_type}")
    company = str(event.get("company") or "").strip()
    role = str(event.get("role") or "").strip()
    if not company or not role:
        raise ValueError("Each event requires company and role.")
    status, default_stage, default_action = EVENT_DEFAULTS[event_type]
    received = str(event.get("received_date") or date.today().isoformat())
    existing = existing_application(company, role, db_path, str(event.get("url") or ""))
    if event_already_applied(existing, event, event_type, received):
        return False
    follow_up = str(event.get("follow_up_date") or "")
    if not follow_up and event_type in {"application_received", "interview_completed"}:
        follow_up = (date.fromisoformat(received) + timedelta(days=7)).isoformat()
    source = str(event.get("source") or (existing or {}).get("source") or "Outlook")
    url = str(event.get("url") or (existing or {}).get("url") or "")
    job_values = {
        "company": company,
        "role": role,
        "source": source,
        "url": url,
        "status": status,
        "notes": str(event.get("notes") or ""),
    }
    app_values = {
        "company": company,
        "role": role,
        "source": source,
        "url": url,
        "status": status,
        "stage": str(event.get("stage") or default_stage),
        "stage_date": received,
        "next_action": str(event.get("next_action") or default_action),
        "follow_up_date": follow_up,
        "contact_name": str(event.get("contact_name") or ""),
        "last_contact_date": received,
        "email_status": event_type,
        "email_subject": str(event.get("subject") or ""),
        "email_url": str(event.get("message_url") or ""),
        "email_last_checked": date.today().isoformat(),
        "notes": str(event.get("notes") or ""),
    }
    record_application_state(job_values, app_values, path=db_path)
    return True


def reconciliation_manifest(db_path: Path) -> dict:
    applications = [
        {
            "company": row["company"],
            "role": row["role"],
            "status": row["status"],
            "submitted": row["submitted"],
            "last_contact_date": row["last_contact_date"],
            "email_last_checked": row["email_last_checked"],
        }
        for row in tracker_rows(db_path)
        if row["status"] in ACTIVE_MAILBOX_STATUSES
    ]
    schedules = due_tasks(db_path)
    schedule = next((item for item in schedules if item["task_name"] == OUTCOME_TASK), None)
    dates = [
        value
        for row in applications
        for value in (row["email_last_checked"], row["submitted"])
        if value
    ]
    return {
        "schedule": schedule,
        "since_date": min(dates) if dates else date.today().isoformat(),
        "active_applications": applications,
    }


def alert_discovery_manifest(db_path: Path) -> dict:
    """Return an independent cursor for LinkedIn alert discovery."""
    configure_schedule(
        ALERT_TASK,
        ALERT_INTERVAL_MINUTES,
        notes="Use Outlook alerts as leads only; verify each live posting before queueing.",
        path=db_path,
    )
    schedule = next(item for item in due_tasks(db_path) if item["task_name"] == ALERT_TASK)
    since_datetime = str(schedule.get("last_run_at") or "")
    if not since_datetime:
        since_datetime = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
    return {
        "schedule": schedule,
        "since_datetime": since_datetime,
        "since_date": since_datetime[:10],
        "source": "LinkedIn job-alert emails",
        "rules": {
            "lead_only": True,
            "verify_live_posting": True,
            "store_message_body": False,
            "change_mailbox_state": False,
        },
    }


def recruiter_discovery_manifest(db_path: Path) -> dict:
    """Return an independent cursor for new inbound recruiter opportunities."""
    configure_schedule(
        RECRUITER_TASK,
        RECRUITER_INTERVAL_MINUTES,
        notes="Inspect inbound recruiter outreach as leads; verify the named direct employer before queueing.",
        path=db_path,
    )
    schedule = next(item for item in due_tasks(db_path) if item["task_name"] == RECRUITER_TASK)
    since_datetime = str(schedule.get("last_run_at") or "")
    if not since_datetime:
        since_datetime = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
    return {
        "schedule": schedule,
        "since_datetime": since_datetime,
        "since_date": since_datetime[:10],
        "source": "Inbound recruiter outreach",
        "rules": {
            "lead_only": True,
            "named_direct_employer_required": True,
            "verify_recruiter_identity": True,
            "verify_live_posting_or_role": True,
            "allow_external_recruiter_for_named_direct_employer": True,
            "reject_staffing_placement_or_unnamed_client": True,
            "surface_unresolved_before_advancing_cursor": True,
            "store_message_body": False,
            "change_mailbox_state": False,
        },
    }


def pipeline_manifest(db_path: Path) -> dict:
    """Return the independent cursor for serialized application processing."""
    configure_schedule(
        PIPELINE_TASK,
        PIPELINE_INTERVAL_MINUTES,
        notes="Process queued packet work and application-ready jobs serially; discovery has its own two-hour cursor.",
        path=db_path,
    )
    schedule = next(item for item in due_tasks(db_path) if item["task_name"] == PIPELINE_TASK)
    return {
        "schedule": schedule,
        "rules": {
            "serialized": True,
            "queue_capacity": 10,
            "local_codex_default": True,
            "exact_private_facts_only": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage scheduled Outlook reconciliation metadata.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure = subparsers.add_parser("configure")
    configure.add_argument("--interval-minutes", type=int)
    configure.add_argument("--outlook-interval-minutes", type=int, default=OUTCOME_INTERVAL_MINUTES)
    configure.add_argument("--alert-interval-minutes", type=int, default=ALERT_INTERVAL_MINUTES)
    configure.add_argument("--recruiter-interval-minutes", type=int, default=RECRUITER_INTERVAL_MINUTES)
    configure.add_argument("--pipeline-interval-minutes", type=int, default=PIPELINE_INTERVAL_MINUTES)
    subparsers.add_parser("due")
    subparsers.add_parser("manifest")
    subparsers.add_parser("mark-checked")
    subparsers.add_parser("alert-manifest")
    subparsers.add_parser("mark-alerts-checked")
    subparsers.add_parser("recruiter-manifest")
    subparsers.add_parser("mark-recruiters-checked")
    subparsers.add_parser("pipeline-manifest")
    subparsers.add_parser("mark-pipeline-run")
    apply_events = subparsers.add_parser("apply-events")
    apply_events.add_argument("--events", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "configure":
        outlook_interval = args.interval_minutes or args.outlook_interval_minutes
        alert_interval = args.interval_minutes or args.alert_interval_minutes
        recruiter_interval = args.interval_minutes or args.recruiter_interval_minutes
        pipeline_interval = args.interval_minutes or args.pipeline_interval_minutes
        configure_schedule(
            OUTCOME_TASK,
            outlook_interval,
            notes="Run through the Codex Outlook connector; save only approved metadata events.",
            path=args.db,
        )
        configure_schedule(
            ALERT_TASK,
            alert_interval,
            notes="Use Outlook alerts as leads only; verify each live posting before queueing.",
            path=args.db,
        )
        configure_schedule(
            RECRUITER_TASK,
            recruiter_interval,
            notes="Inspect inbound recruiter outreach as leads; verify the named direct employer before queueing.",
            path=args.db,
        )
        configure_schedule(
            PIPELINE_TASK,
            pipeline_interval,
            notes="Process queued packet work and application-ready jobs serially; discovery has its own cursor.",
            path=args.db,
        )
        print(
            "Configured Outlook reconciliation every "
            f"{outlook_interval} minutes, alert discovery every {alert_interval} minutes, "
            f"recruiter discovery every {recruiter_interval} minutes, "
            f"and serialized application processing every {pipeline_interval} minutes."
        )
        return 0
    if args.command == "due":
        print(json.dumps(due_tasks(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "manifest":
        print(json.dumps(reconciliation_manifest(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "alert-manifest":
        print(json.dumps(alert_discovery_manifest(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "recruiter-manifest":
        print(json.dumps(recruiter_discovery_manifest(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "pipeline-manifest":
        print(json.dumps(pipeline_manifest(args.db), indent=2, ensure_ascii=False))
        return 0
    if args.command == "mark-checked":
        mark_schedule_run(OUTCOME_TASK, path=args.db)
        print("Marked Outlook reconciliation checked with no state changes.")
        return 0
    if args.command == "mark-alerts-checked":
        alert_discovery_manifest(args.db)
        mark_schedule_run(ALERT_TASK, path=args.db)
        print("Marked LinkedIn alert discovery checked.")
        return 0
    if args.command == "mark-recruiters-checked":
        recruiter_discovery_manifest(args.db)
        mark_schedule_run(RECRUITER_TASK, path=args.db)
        print("Marked inbound recruiter discovery checked.")
        return 0
    if args.command == "mark-pipeline-run":
        pipeline_manifest(args.db)
        mark_schedule_run(PIPELINE_TASK, path=args.db)
        print("Marked the full application pipeline cycle complete.")
        return 0
    payload = json.loads(args.events.read_text(encoding="utf-8"))
    events = payload.get("events", payload) if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise SystemExit("Events JSON must be a list or contain an events array.")
    applied = 0
    for event in events:
        if not isinstance(event, dict):
            raise SystemExit("Each event must be an object.")
        applied += int(apply_event(event, args.db))
    mark_schedule_run(OUTCOME_TASK, path=args.db)
    export_legacy_csv(path=args.db)
    print(f"Applied {applied} metadata-only Outlook event(s); skipped {len(events) - applied} duplicate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
