#!/usr/bin/env python3
"""Keep the SQLite-backed rolling queue fresh without touching active applications."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from job_store import (
        DEFAULT_DB,
        connection,
        export_legacy_csv,
        initialize,
        transition_job_status,
    )
    from search_criteria import read_search_criteria
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import (
        DEFAULT_DB,
        connection,
        export_legacy_csv,
        initialize,
        transition_job_status,
    )
    from scripts.search_criteria import read_search_criteria


EXPIRABLE_STATUSES = {"queued", "resume_ready"}
ACTIONABLE_STATUSES = {"queued", "resume_ready", "application_started"}
BLOCKED_STATUSES = {
    "analyzed",
    "manual_apply_needed",
    "blocked_needs_user_input",
    "pending_remote_approval",
}
OPEN_STATUSES = ACTIONABLE_STATUSES | BLOCKED_STATUSES
POSTING_ID_QUERY_KEYS = {"gh_jid", "jid", "job_id", "jobid", "requisitionid"}


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expiry(row: dict[str, object], freshness_hours: int) -> datetime | None:
    posted = _parse_timestamp(str(row.get("posted_at") or ""))
    if posted:
        return posted + timedelta(hours=freshness_hours)
    return _parse_timestamp(str(row.get("expires_at") or ""))


def _posting_reference(url: str) -> tuple[str, str] | None:
    """Extract a stable posting id only when the ATS exposes one explicitly."""

    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    query = {key.casefold(): values for key, values in parse_qs(parsed.query).items()}
    for key in POSTING_ID_QUERY_KEYS:
        values = query.get(key)
        if values and values[0].strip():
            return host, values[0].strip().casefold()
    if "/jobdetail/" in parsed.path.casefold():
        match = re.search(r"/(\d+)/?$", parsed.path)
        if match:
            return host, match.group(1)
    return None


def _score(row: dict[str, object]) -> int:
    try:
        return int(row.get("calibrated_match_score") or row.get("base_match_score") or 0)
    except (TypeError, ValueError):
        return 0


def _preferred_duplicate(rows: list[dict[str, object]]) -> dict[str, object]:
    def rank(row: dict[str, object]) -> tuple[int, int, int, int]:
        parsed = urlparse(str(row.get("url") or ""))
        direct = int("login" not in parsed.path.casefold())
        return direct, int(bool(row.get("batch_id"))), _score(row), len(parsed.path)

    return max(rows, key=rank)


def maintain_queue(
    *,
    db_path: Path = DEFAULT_DB,
    expire_stale: bool = False,
    capacity: int = 10,
    low_watermark: int = 3,
    blocked_timeout_hours: int = 24,
    criteria_path: Path = Path("profile/search_criteria.md"),
    now: datetime | None = None,
) -> dict[str, object]:
    """Mark only unstarted, stale postings outdated and report refill capacity."""

    if capacity <= 0 or low_watermark < 0 or low_watermark > capacity:
        raise ValueError("capacity must be positive and low_watermark must be within capacity")
    if blocked_timeout_hours <= 0:
        raise ValueError("blocked_timeout_hours must be positive")
    initialize(db_path)
    freshness_hours = read_search_criteria(criteria_path).maximum_freshness_hours
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    expired: list[dict[str, str]] = []
    expired_blockers: list[dict[str, str]] = []
    duplicate_jobs: list[dict[str, str]] = []
    with connection(db_path) as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
        if expire_stale:
            duplicate_groups: dict[tuple[str, str, tuple[str, str]], list[dict[str, object]]] = {}
            for row in rows:
                if str(row.get("status") or "") not in OPEN_STATUSES:
                    continue
                reference = _posting_reference(str(row.get("url") or ""))
                if reference is None:
                    continue
                key = (
                    str(row.get("company") or "").strip().casefold(),
                    str(row.get("role") or "").strip().casefold(),
                    reference,
                )
                duplicate_groups.setdefault(key, []).append(row)
            for group in duplicate_groups.values():
                if len(group) < 2:
                    continue
                preferred = _preferred_duplicate(group)
                for row in group:
                    if row["id"] == preferred["id"]:
                        continue
                    message = (
                        "Duplicate posting wrapper closed; canonical URL: "
                        + str(preferred.get("url") or "")
                    )
                    existing_notes = str(row.get("notes") or "").strip()
                    transition_job_status(
                        str(row.get("url") or ""),
                        "skipped",
                        notes=f"{existing_notes} | {message}" if existing_notes else message,
                        stage="duplicate",
                        stage_date=current.date().isoformat(),
                        next_action="No action",
                        conn=conn,
                        event_type="duplicate_job_closed",
                    )
                    duplicate_jobs.append({
                        "company": str(row["company"]),
                        "role": str(row["role"]),
                    })
            if duplicate_jobs:
                rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]

            blocker_cutoff = current - timedelta(hours=blocked_timeout_hours)
            for row in rows:
                if str(row.get("status") or "") not in BLOCKED_STATUSES:
                    continue
                updated = _parse_timestamp(str(row.get("updated_at") or ""))
                if updated is None or updated > blocker_cutoff:
                    continue
                message = f"Attempt budget expired after {blocked_timeout_hours} blocked hours."
                existing_notes = str(row.get("notes") or "").strip()
                transition_job_status(
                    str(row.get("url") or ""),
                    "skipped",
                    notes=f"{existing_notes} | {message}" if existing_notes else message,
                    stage="skipped",
                    stage_date=current.date().isoformat(),
                    next_action="No action",
                    conn=conn,
                    event_type="blocked_job_expired",
                )
                expired_blockers.append({
                    "company": str(row["company"]),
                    "role": str(row["role"]),
                })
            if expired_blockers:
                rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]

            for row in rows:
                if str(row.get("status") or "") not in EXPIRABLE_STATUSES:
                    continue
                expiry = _expiry(row, freshness_hours)
                if not expiry or expiry > current:
                    continue
                message = f"Posting expired under the {freshness_hours}-hour freshness policy."
                existing_notes = str(row.get("notes") or "").strip()
                transition_job_status(
                    str(row.get("url") or ""),
                    "outdated",
                    notes=f"{existing_notes} | {message}" if existing_notes else message,
                    stage="posting_closed",
                    stage_date=current.date().isoformat(),
                    next_action="No action",
                    conn=conn,
                    event_type="job_expired",
                )
                expired.append({"company": str(row["company"]), "role": str(row["role"])})
            if expired:
                rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
    if expire_stale and (expired or expired_blockers or duplicate_jobs) and db_path.resolve() == DEFAULT_DB.resolve():
        export_legacy_csv(path=db_path)
    open_jobs = [row for row in rows if str(row.get("status") or "") in OPEN_STATUSES]
    actionable_jobs = [
        row for row in open_jobs if str(row.get("status") or "") in ACTIONABLE_STATUSES
    ]
    blocked_jobs = [
        row for row in open_jobs if str(row.get("status") or "") in BLOCKED_STATUSES
    ]
    available_slots = max(0, capacity - len(open_jobs))
    return {
        "as_of": current.replace(microsecond=0).isoformat(),
        "expired": expired,
        "expired_blockers": expired_blockers,
        "duplicate_jobs_closed": duplicate_jobs,
        "open_jobs": len(open_jobs),
        "ready_jobs": len(actionable_jobs),
        "actionable_jobs": len(actionable_jobs),
        "blocked_jobs": len(blocked_jobs),
        "capacity": capacity,
        "available_slots": available_slots,
        "refill_recommended": available_slots > 0 and len(actionable_jobs) <= low_watermark,
        "urgent_refill": available_slots > 0 and not actionable_jobs,
        "low_watermark": low_watermark,
        "freshness_hours": freshness_hours,
        "blocked_timeout_hours": blocked_timeout_hours,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire stale queued jobs and report rolling queue capacity.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--expire-stale", action="store_true")
    parser.add_argument("--capacity", type=int, default=10)
    parser.add_argument("--low-watermark", type=int, default=3)
    parser.add_argument("--blocked-timeout-hours", type=int, default=24)
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = maintain_queue(
        db_path=args.db,
        expire_stale=args.expire_stale,
        capacity=args.capacity,
        low_watermark=args.low_watermark,
        blocked_timeout_hours=args.blocked_timeout_hours,
        criteria_path=args.criteria,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
