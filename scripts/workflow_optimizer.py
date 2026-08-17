#!/usr/bin/env python3
"""Learn from recurring workflow barriers without storing private form values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from job_store import DEFAULT_DB, connection, initialize, utc_now
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, connection, initialize, utc_now


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "outputs" / "workflow_optimization_report.json"

DEFAULT_STAGE_POLICY = {
    "budget_seconds": 180,
    "interaction_limit": 8,
    "token_estimate_limit": 6000,
    "max_attempts": 1,
}

STAGE_POLICIES: dict[str, dict[str, int]] = {
    "discovery": {"budget_seconds": 180, "interaction_limit": 8, "token_estimate_limit": 5000, "max_attempts": 1},
    "posting_verify": {"budget_seconds": 60, "interaction_limit": 5, "token_estimate_limit": 3000, "max_attempts": 1},
    "hard_screen": {"budget_seconds": 90, "interaction_limit": 5, "token_estimate_limit": 3500, "max_attempts": 1},
    "resume_tailoring": {"budget_seconds": 240, "interaction_limit": 8, "token_estimate_limit": 7000, "max_attempts": 1},
    "resume_render": {"budget_seconds": 90, "interaction_limit": 3, "token_estimate_limit": 1500, "max_attempts": 1},
    "ats_open": {"budget_seconds": 60, "interaction_limit": 5, "token_estimate_limit": 2500, "max_attempts": 1},
    "ats_fill": {"budget_seconds": 360, "interaction_limit": 14, "token_estimate_limit": 9000, "max_attempts": 1},
    "ats_upload": {"budget_seconds": 90, "interaction_limit": 5, "token_estimate_limit": 2500, "max_attempts": 2},
    "ats_submit": {"budget_seconds": 120, "interaction_limit": 5, "token_estimate_limit": 3000, "max_attempts": 1},
    "mailbox_reconcile": {"budget_seconds": 180, "interaction_limit": 8, "token_estimate_limit": 5000, "max_attempts": 1},
}

BARRIER_POLICIES: dict[str, tuple[str, str, str]] = {
    "azure_bad_json": ("avoid", "use_codex", "Azure returned malformed JSON repeatedly; bypass it for tailoring."),
    "azure_unavailable": ("avoid", "use_codex", "The remote model is unavailable; continue with local Codex."),
    "expired_post": ("avoid", "skip_candidate", "The live posting is closed or no longer accepting applications."),
    "duplicate_role": ("avoid", "skip_candidate", "An equivalent application already exists."),
    "employer_saturation": ("avoid", "skip_candidate", "Enough similar roles at this employer were already submitted."),
    "explicit_no_sponsorship": ("avoid", "skip_candidate", "The posting explicitly conflicts with the saved sponsorship requirement."),
    "staffing_company": (
        "avoid",
        "skip_candidate",
        "Reject contract staffing placements or unnamed clients; a verified external recruiter representing a named direct employer remains eligible.",
    ),
    "role_mismatch": ("avoid", "skip_candidate", "The core deliverable is outside the accepted data role families."),
    "linkedin_wrapper": ("switch", "open_direct_ats", "Use the direct employer ATS URL and do not revisit the wrapper."),
    "browser_native_host": ("switch", "switch_browser_path", "Switch browser path after one native-host failure; do not repair it during an application."),
    "captcha": ("handoff", "manual_handoff", "CAPTCHA requires user interaction."),
    "login_required": ("handoff", "manual_handoff", "Account login is a user handoff rather than a retry loop."),
    "email_verification": ("handoff", "manual_handoff", "A one-time email code requires user interaction."),
    "missing_required_fact": ("handoff", "ask_user_once", "A required answer is not covered by the private fact sources."),
    "upload_failure": ("retry_once", "alternate_upload_once", "Try one alternate upload path, then hand off with the resume path."),
    "wrong_resume_selected": ("retry_once", "replace_and_verify_resume", "Replace the stale resume and verify the exact filename once."),
    "render_timeout": ("switch", "use_fallback_renderer", "Stop the hanging renderer and use the verified fallback path."),
    "ats_widget_failure": ("retry_once", "alternate_widget_once", "Try one grounded alternate interaction, then hand off."),
    "timeout": ("avoid", "move_on", "The stage exceeded its budget; preserve a handoff and continue the queue."),
    "budget_overrun": ("review", "shorten_path", "The stage succeeded but consistently exceeds its time or interaction budget."),
}

PLATFORM_WIDE_BARRIERS = {"azure_bad_json", "azure_unavailable", "browser_native_host"}
FAILURE_OUTCOMES = {"failure", "handoff", "skipped", "timeout"}


def normalize(value: str | None, default: str = "") -> str:
    return "_".join((value or default).strip().lower().split())


def stage_policy(stage: str) -> dict[str, int]:
    return dict(STAGE_POLICIES.get(normalize(stage), DEFAULT_STAGE_POLICY))


def signature(stage: str, platform: str, barrier: str) -> str:
    return "|".join([normalize(stage, "unknown"), normalize(platform, "unknown"), normalize(barrier, "unknown_failure")])


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fallback_action(stage: str) -> str:
    normalized = normalize(stage)
    if normalized in {"discovery", "posting_verify", "hard_screen"}:
        return "skip_candidate"
    if normalized == "resume_tailoring":
        return "use_codex"
    if normalized == "resume_render":
        return "use_fallback_renderer"
    if normalized.startswith("ats_"):
        return "manual_handoff"
    return "move_on"


def _insert_attempt(values: dict[str, Any], *, path: Path) -> tuple[int, bool]:
    initialize(path)
    now = utc_now()
    payload = {
        "source_ref": str(values.get("source_ref") or ""),
        "stage": normalize(str(values.get("stage") or "unknown")),
        "platform": normalize(str(values.get("platform") or "unknown")),
        "company": str(values.get("company") or ""),
        "role": str(values.get("role") or ""),
        "outcome": normalize(str(values.get("outcome") or "in_progress")),
        "barrier": normalize(str(values.get("barrier") or "")),
        "action_taken": normalize(str(values.get("action_taken") or "")),
        "started_at": str(values.get("started_at") or now),
        "finished_at": str(values.get("finished_at") or ""),
        "duration_seconds": values.get("duration_seconds"),
        "interaction_count": values.get("interaction_count"),
        "token_estimate": values.get("token_estimate"),
        "notes": str(values.get("notes") or ""),
        "created_at": now,
        "updated_at": now,
    }
    columns = list(payload)
    with connection(path) as conn:
        try:
            cursor = conn.execute(
                f"INSERT INTO workflow_attempts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [payload[column] for column in columns],
            )
            attempt_id = int(cursor.lastrowid)
            created = True
        except sqlite3.IntegrityError:
            if not payload["source_ref"]:
                raise
            row = conn.execute(
                "SELECT id FROM workflow_attempts WHERE source_ref = ?",
                (payload["source_ref"],),
            ).fetchone()
            if not row:
                raise
            attempt_id = int(row["id"])
            created = False
    if created and payload["outcome"] != "in_progress" and payload["barrier"]:
        recompute_rule(path, payload["stage"], payload["platform"], payload["barrier"])
    return attempt_id, created


def record_attempt(
    *,
    path: Path = Path(DEFAULT_DB),
    stage: str,
    platform: str = "unknown",
    outcome: str,
    barrier: str = "",
    action_taken: str = "",
    duration_seconds: float | None = None,
    interaction_count: int | None = None,
    token_estimate: int | None = None,
    company: str = "",
    role: str = "",
    notes: str = "",
    source_ref: str = "",
) -> tuple[int, bool]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    duration = max(0.0, float(duration_seconds or 0.0))
    started = now - timedelta(seconds=duration)
    return _insert_attempt(
        {
            "source_ref": source_ref,
            "stage": stage,
            "platform": platform,
            "company": company,
            "role": role,
            "outcome": outcome,
            "barrier": barrier,
            "action_taken": action_taken,
            "started_at": started.isoformat(),
            "finished_at": now.isoformat(),
            "duration_seconds": duration_seconds,
            "interaction_count": interaction_count,
            "token_estimate": token_estimate,
            "notes": notes,
        },
        path=path,
    )


def recompute_rule(path: Path, stage: str, platform: str, barrier: str) -> dict[str, Any] | None:
    stage = normalize(stage, "unknown")
    platform = normalize(platform, "unknown")
    barrier = normalize(barrier, "unknown_failure")
    policy = stage_policy(stage)
    with connection(path) as conn:
        rows = conn.execute(
            """
            SELECT outcome, duration_seconds, interaction_count, token_estimate
            FROM workflow_attempts
            WHERE stage = ? AND platform = ? AND barrier = ? AND outcome <> 'in_progress'
            ORDER BY started_at
            """,
            (stage, platform, barrier),
        ).fetchall()
        if not rows:
            return None

        failure_count = sum(1 for row in rows if row["outcome"] in FAILURE_OUTCOMES)
        success_count = sum(1 for row in rows if row["outcome"] == "success")
        durations = [float(row["duration_seconds"]) for row in rows if row["duration_seconds"] is not None and float(row["duration_seconds"]) > 0]
        interactions = [int(row["interaction_count"]) for row in rows if row["interaction_count"] is not None]
        token_total = sum(int(row["token_estimate"] or 0) for row in rows)
        overruns = sum(
            1
            for row in rows
            if (row["duration_seconds"] is not None and float(row["duration_seconds"]) > policy["budget_seconds"])
            or (row["interaction_count"] is not None and int(row["interaction_count"]) > policy["interaction_limit"])
            or (row["token_estimate"] is not None and int(row["token_estimate"]) > policy["token_estimate_limit"])
        )

        base = BARRIER_POLICIES.get(barrier)
        if base:
            decision, action, reason = base
        else:
            decision, action, reason = "retry_once", "retry_once", "No stable pattern has been learned yet."

        if failure_count >= 2 or overruns >= 2:
            decision = "avoid" if not stage.startswith("ats_") else "handoff"
            action = fallback_action(stage)
            reason = (
                f"Learned from {failure_count} failed or handed-off attempts and {overruns} budget overruns; "
                "do not repeat the same path."
            )

        expires = (datetime.now(timezone.utc) + timedelta(days=45)).replace(microsecond=0).isoformat()
        payload = {
            "signature": signature(stage, platform, barrier),
            "stage": stage,
            "platform": platform,
            "barrier": barrier,
            "decision": decision,
            "action": action,
            "reason": reason,
            "observation_count": len(rows),
            "failure_count": failure_count,
            "success_count": success_count,
            "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
            "avg_interaction_count": round(sum(interactions) / len(interactions), 1) if interactions else None,
            "token_estimate_total": token_total,
            "expires_at": expires,
            "updated_at": utc_now(),
        }
        columns = list(payload)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "signature")
        conn.execute(
            f"""
            INSERT INTO workflow_learned_rules ({', '.join(columns)})
            VALUES ({', '.join('?' for _ in columns)})
            ON CONFLICT(signature) DO UPDATE SET {updates}
            """,
            [payload[column] for column in columns],
        )
        return payload


def advise(*, path: Path = Path(DEFAULT_DB), stage: str, platform: str = "unknown", barrier: str = "") -> dict[str, Any]:
    initialize(path)
    stage = normalize(stage, "unknown")
    platform = normalize(platform, "unknown")
    barrier = normalize(barrier)
    policy = stage_policy(stage)
    learned: dict[str, Any] | None = None

    with connection(path) as conn:
        if barrier:
            row = conn.execute(
                "SELECT * FROM workflow_learned_rules WHERE signature = ? AND expires_at >= ?",
                (signature(stage, platform, barrier), utc_now()),
            ).fetchone()
        else:
            placeholders = ", ".join("?" for _ in PLATFORM_WIDE_BARRIERS)
            row = conn.execute(
                f"""
                SELECT * FROM workflow_learned_rules
                WHERE stage = ? AND platform = ? AND expires_at >= ?
                  AND barrier IN ({placeholders})
                ORDER BY failure_count DESC, observation_count DESC
                LIMIT 1
                """,
                [stage, platform, utc_now(), *sorted(PLATFORM_WIDE_BARRIERS)],
            ).fetchone()
        if row:
            learned = {key: row[key] for key in row.keys()}

    if learned:
        decision = str(learned["decision"])
        action = str(learned["action"])
        reason = str(learned["reason"])
    elif barrier in BARRIER_POLICIES:
        decision, action, reason = BARRIER_POLICIES[barrier]
    else:
        decision, action, reason = "proceed", "proceed", "No active learned barrier applies."

    return {
        "stage": stage,
        "platform": platform,
        "barrier": barrier,
        "decision": decision,
        "action": action,
        "reason": reason,
        **policy,
        "learned_rule": learned,
    }


def start_attempt(
    *,
    path: Path = Path(DEFAULT_DB),
    stage: str,
    platform: str = "unknown",
    barrier: str = "",
    company: str = "",
    role: str = "",
    notes: str = "",
) -> dict[str, Any]:
    guidance = advise(path=path, stage=stage, platform=platform, barrier=barrier)
    if guidance["decision"] in {"avoid", "handoff"}:
        return {"attempt_id": None, "started": False, "guidance": guidance}
    attempt_id, _ = _insert_attempt(
        {
            "stage": stage,
            "platform": platform,
            "company": company,
            "role": role,
            "outcome": "in_progress",
            "barrier": barrier,
            "started_at": utc_now(),
            "notes": notes,
        },
        path=path,
    )
    return {"attempt_id": attempt_id, "started": True, "guidance": guidance}


def finish_attempt(
    *,
    path: Path = Path(DEFAULT_DB),
    attempt_id: int,
    outcome: str,
    barrier: str = "",
    action_taken: str = "",
    duration_seconds: float | None = None,
    interaction_count: int | None = None,
    token_estimate: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    initialize(path)
    outcome = normalize(outcome)
    if outcome not in {"success", "failure", "handoff", "skipped", "timeout"}:
        raise ValueError(f"Unsupported outcome: {outcome}")
    with connection(path) as conn:
        row = conn.execute("SELECT * FROM workflow_attempts WHERE id = ?", (attempt_id,)).fetchone()
        if not row:
            raise ValueError(f"Workflow attempt not found: {attempt_id}")
        if row["outcome"] != "in_progress":
            return {"attempt_id": attempt_id, "updated": False, "outcome": row["outcome"]}
        now = datetime.now(timezone.utc).replace(microsecond=0)
        elapsed = max(0.0, (now - parse_time(row["started_at"])).total_seconds())
        duration = elapsed if duration_seconds is None else max(0.0, float(duration_seconds))
        policy = stage_policy(row["stage"])
        normalized_barrier = normalize(barrier)
        if not normalized_barrier and (
            duration > policy["budget_seconds"]
            or (interaction_count is not None and interaction_count > policy["interaction_limit"])
            or (token_estimate is not None and token_estimate > policy["token_estimate_limit"])
        ):
            normalized_barrier = "budget_overrun"
        if not normalized_barrier and outcome in FAILURE_OUTCOMES:
            normalized_barrier = "unknown_failure"
        conn.execute(
            """
            UPDATE workflow_attempts
            SET outcome = ?, barrier = ?, action_taken = ?, finished_at = ?, duration_seconds = ?,
                interaction_count = ?, token_estimate = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                outcome,
                normalized_barrier,
                normalize(action_taken),
                now.isoformat(),
                duration,
                interaction_count,
                token_estimate,
                notes or row["notes"],
                now.isoformat(),
                attempt_id,
            ),
        )
        stage = str(row["stage"])
        platform = str(row["platform"])
    rule = recompute_rule(path, stage, platform, normalized_barrier) if normalized_barrier else None
    return {
        "attempt_id": attempt_id,
        "updated": True,
        "outcome": outcome,
        "barrier": normalized_barrier,
        "duration_seconds": duration,
        "learned_rule": rule,
    }


def close_stale_attempts(path: Path) -> int:
    initialize(path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale: list[tuple[int, str, str, float]] = []
    with connection(path) as conn:
        rows = conn.execute("SELECT id, stage, platform, started_at FROM workflow_attempts WHERE outcome = 'in_progress'").fetchall()
        for row in rows:
            elapsed = max(0.0, (now - parse_time(row["started_at"])).total_seconds())
            if elapsed > stage_policy(row["stage"])["budget_seconds"] * 2:
                stale.append((int(row["id"]), str(row["stage"]), str(row["platform"]), elapsed))
        for attempt_id, _, _, elapsed in stale:
            conn.execute(
                """
                UPDATE workflow_attempts
                SET outcome = 'timeout', barrier = 'timeout', action_taken = 'move_on',
                    finished_at = ?, duration_seconds = ?, updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), elapsed, now.isoformat(), attempt_id),
            )
    for _, stage, platform, _ in stale:
        recompute_rule(path, stage, platform, "timeout")
    return len(stale)


def bootstrap_history(*, path: Path = Path(DEFAULT_DB), root: Path = ROOT, tracker: Path | None = None) -> dict[str, int]:
    added = 0
    existing = 0
    applications = root / "applications"
    for proposed in applications.glob("*/proposed_edits.json") if applications.exists() else []:
        try:
            payload = json.loads(proposed.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = str(payload.get("job_summary") or "").lower()
        if "azure openai response was not valid json" not in summary:
            continue
        _, created = record_attempt(
            path=path,
            stage="resume_tailoring",
            platform="azure",
            outcome="failure",
            barrier="azure_bad_json",
            action_taken="use_codex",
            notes="Imported from a historical application packet.",
            source_ref=f"history:azure_bad_json:{proposed.parent.name}",
        )
        added += int(created)
        existing += int(not created)

    tracker_path = tracker or root / "tracker" / "applications.csv"
    if tracker_path.exists():
        with tracker_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                status = normalize(row.get("status"))
                notes = str(row.get("notes") or "").lower()
                mapped: tuple[str, str, str, str] | None = None
                if status == "expired":
                    mapped = ("posting_verify", "employer_site", "expired_post", "skip_candidate")
                elif status == "manual_apply_needed" and "login" in notes:
                    mapped = ("ats_open", "employer_ats", "login_required", "manual_handoff")
                elif status == "skipped" and "sponsor" in notes:
                    mapped = ("hard_screen", "employer_site", "explicit_no_sponsorship", "skip_candidate")
                elif status == "blocked_needs_user_input" and "captcha" in notes:
                    mapped = ("ats_fill", "employer_ats", "captcha", "manual_handoff")
                if not mapped:
                    continue
                stage, platform, barrier, action = mapped
                digest = hashlib.sha256(
                    "|".join([str(row.get("company") or ""), str(row.get("role") or ""), status]).encode("utf-8")
                ).hexdigest()[:16]
                _, created = record_attempt(
                    path=path,
                    stage=stage,
                    platform=platform,
                    outcome="skipped" if status in {"expired", "skipped"} else "handoff",
                    barrier=barrier,
                    action_taken=action,
                    notes="Imported from historical tracker state.",
                    source_ref=f"history:tracker:{digest}:{status}",
                )
                added += int(created)
                existing += int(not created)
    return {"added": added, "already_present": existing}


def report_data(*, path: Path = Path(DEFAULT_DB)) -> dict[str, Any]:
    initialize(path)
    closed_stale = close_stale_attempts(path)
    with connection(path) as conn:
        pairs = conn.execute(
            "SELECT DISTINCT stage, platform, barrier FROM workflow_attempts WHERE barrier <> '' AND outcome <> 'in_progress'"
        ).fetchall()
    for row in pairs:
        recompute_rule(path, row["stage"], row["platform"], row["barrier"])

    with connection(path) as conn:
        outcome_rows = conn.execute(
            "SELECT outcome, COUNT(*) AS count FROM workflow_attempts GROUP BY outcome ORDER BY count DESC"
        ).fetchall()
        barrier_rows = conn.execute(
            """
            SELECT stage, platform, barrier, COUNT(*) AS count,
                   ROUND(AVG(CASE WHEN duration_seconds > 0 THEN duration_seconds END), 1) AS avg_duration_seconds,
                   ROUND(AVG(interaction_count), 1) AS avg_interaction_count,
                   SUM(COALESCE(token_estimate, 0)) AS token_estimate_total
            FROM workflow_attempts
            WHERE barrier <> '' AND outcome <> 'in_progress'
            GROUP BY stage, platform, barrier
            ORDER BY count DESC, stage, platform
            """
        ).fetchall()
        rule_rows = conn.execute(
            """
            SELECT * FROM workflow_learned_rules
            WHERE expires_at >= ?
            ORDER BY failure_count DESC, observation_count DESC, stage, platform
            """,
            (utc_now(),),
        ).fetchall()

    rules = [{key: row[key] for key in row.keys()} for row in rule_rows]
    recommendations = [
        {
            "stage": rule["stage"],
            "platform": rule["platform"],
            "barrier": rule["barrier"],
            "action": rule["action"],
            "reason": rule["reason"],
        }
        for rule in rules
        if rule["decision"] != "proceed"
    ]
    return {
        "generated_at": utc_now(),
        "closed_stale_attempts": closed_stale,
        "outcomes": {row["outcome"]: int(row["count"]) for row in outcome_rows},
        "top_barriers": [{key: row[key] for key in row.keys()} for row in barrier_rows],
        "active_rules": rules,
        "recommendations": recommendations,
        "measurement_note": "Elapsed time and interaction count are primary effort proxies; token estimates are optional when available.",
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_attempt_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage", required=True)
    parser.add_argument("--platform", default="unknown")
    parser.add_argument("--barrier", default="")
    parser.add_argument("--company", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--notes", default="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record workflow effort and learn when to retry, switch, hand off, or skip.")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB))
    commands = parser.add_subparsers(dest="command", required=True)

    advice = commands.add_parser("advise", help="Check budget and learned rules before a workflow stage.")
    advice.add_argument("--stage", required=True)
    advice.add_argument("--platform", default="unknown")
    advice.add_argument("--barrier", default="")

    start = commands.add_parser("start", help="Start a timed workflow attempt after consulting learned rules.")
    add_attempt_fields(start)

    finish = commands.add_parser("finish", help="Finish a timed attempt and update learned rules.")
    finish.add_argument("--attempt-id", type=int, required=True)
    finish.add_argument("--outcome", choices=["success", "failure", "handoff", "skipped", "timeout"], required=True)
    finish.add_argument("--barrier", default="")
    finish.add_argument("--action-taken", default="")
    finish.add_argument("--duration-seconds", type=float)
    finish.add_argument("--interaction-count", type=int)
    finish.add_argument("--token-estimate", type=int)
    finish.add_argument("--notes", default="")

    record = commands.add_parser("record", help="Record a completed attempt when no timer was started.")
    add_attempt_fields(record)
    record.add_argument("--outcome", choices=["success", "failure", "handoff", "skipped", "timeout"], required=True)
    record.add_argument("--action-taken", default="")
    record.add_argument("--duration-seconds", type=float)
    record.add_argument("--interaction-count", type=int)
    record.add_argument("--token-estimate", type=int)
    record.add_argument("--source-ref", default="")

    bootstrap = commands.add_parser("bootstrap-history", help="Import idempotent barrier evidence from local packets and tracker states.")
    bootstrap.add_argument("--root", type=Path, default=ROOT)
    bootstrap.add_argument("--tracker", type=Path)

    report = commands.add_parser("report", help="Refresh learned rules and write an aggregate local report.")
    report.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    report.add_argument("--summary", action="store_true", help="Print only counts and actions; keep full details in the report file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "advise":
        payload = advise(path=args.db, stage=args.stage, platform=args.platform, barrier=args.barrier)
    elif args.command == "start":
        payload = start_attempt(
            path=args.db,
            stage=args.stage,
            platform=args.platform,
            barrier=args.barrier,
            company=args.company,
            role=args.role,
            notes=args.notes,
        )
    elif args.command == "finish":
        payload = finish_attempt(
            path=args.db,
            attempt_id=args.attempt_id,
            outcome=args.outcome,
            barrier=args.barrier,
            action_taken=args.action_taken,
            duration_seconds=args.duration_seconds,
            interaction_count=args.interaction_count,
            token_estimate=args.token_estimate,
            notes=args.notes,
        )
    elif args.command == "record":
        attempt_id, created = record_attempt(
            path=args.db,
            stage=args.stage,
            platform=args.platform,
            outcome=args.outcome,
            barrier=args.barrier,
            action_taken=args.action_taken,
            duration_seconds=args.duration_seconds,
            interaction_count=args.interaction_count,
            token_estimate=args.token_estimate,
            company=args.company,
            role=args.role,
            notes=args.notes,
            source_ref=args.source_ref,
        )
        payload = {"attempt_id": attempt_id, "created": created}
    elif args.command == "bootstrap-history":
        payload = bootstrap_history(path=args.db, root=args.root, tracker=args.tracker)
    else:
        full_payload = report_data(path=args.db)
        write_report(args.out, full_payload)
        if args.summary:
            payload = {
                "report_path": str(args.out),
                "outcomes": full_payload["outcomes"],
                "active_rule_count": len(full_payload["active_rules"]),
                "actions": sorted({item["action"] for item in full_payload["recommendations"]}),
                "closed_stale_attempts": full_payload["closed_stale_attempts"],
            }
        else:
            payload = {**full_payload, "report_path": str(args.out)}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
