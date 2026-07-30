#!/usr/bin/env python3
"""Keep the SQLite-backed rolling queue fresh without touching active applications."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from job_store import DEFAULT_DB, connection, export_legacy_csv, initialize, record_event
    from search_criteria import read_search_criteria
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, connection, export_legacy_csv, initialize, record_event
    from scripts.search_criteria import read_search_criteria


EXPIRABLE_STATUSES = {"queued", "analyzed", "resume_ready", "manual_apply_needed"}
OPEN_STATUSES = EXPIRABLE_STATUSES | {"application_started", "blocked_needs_user_input"}


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


def maintain_queue(
    *,
    db_path: Path = DEFAULT_DB,
    expire_stale: bool = False,
    capacity: int = 10,
    low_watermark: int = 3,
    criteria_path: Path = Path("profile/search_criteria.md"),
    now: datetime | None = None,
) -> dict[str, object]:
    """Mark only unstarted, stale postings outdated and report refill capacity."""

    if capacity <= 0 or low_watermark < 0 or low_watermark > capacity:
        raise ValueError("capacity must be positive and low_watermark must be within capacity")
    initialize(db_path)
    freshness_hours = read_search_criteria(criteria_path).maximum_freshness_hours
    current = now or datetime.now(timezone.utc)
    expired: list[dict[str, str]] = []
    with connection(db_path) as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
        if expire_stale:
            for row in rows:
                if str(row.get("status") or "") not in EXPIRABLE_STATUSES:
                    continue
                expiry = _expiry(row, freshness_hours)
                if not expiry or expiry > current:
                    continue
                conn.execute(
                    "UPDATE jobs SET status = 'outdated', updated_at = ? WHERE id = ?",
                    (current.replace(microsecond=0).isoformat(), row["id"]),
                )
                record_event(
                    conn,
                    application_id=None,
                    job_id=int(row["id"]),
                    event_type="job_expired",
                    source=str(row.get("source") or ""),
                    summary=f"Queued job expired under the {freshness_hours}-hour freshness policy.",
                    metadata={"expires_at": expiry.isoformat()},
                )
                expired.append({"company": str(row["company"]), "role": str(row["role"])})
            if expired:
                rows = [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]
    if expire_stale and db_path.resolve() == DEFAULT_DB.resolve():
        export_legacy_csv(path=db_path)
    open_jobs = [row for row in rows if str(row.get("status") or "") in OPEN_STATUSES]
    ready_jobs = [row for row in open_jobs if str(row.get("status") or "") in EXPIRABLE_STATUSES]
    return {
        "as_of": current.replace(microsecond=0).isoformat(),
        "expired": expired,
        "open_jobs": len(open_jobs),
        "ready_jobs": len(ready_jobs),
        "capacity": capacity,
        "available_slots": max(0, capacity - len(open_jobs)),
        "refill_recommended": len(open_jobs) <= low_watermark,
        "low_watermark": low_watermark,
        "freshness_hours": freshness_hours,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire stale queued jobs and report rolling queue capacity.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--expire-stale", action="store_true")
    parser.add_argument("--capacity", type=int, default=10)
    parser.add_argument("--low-watermark", type=int, default=3)
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = maintain_queue(
        db_path=args.db,
        expire_stale=args.expire_stale,
        capacity=args.capacity,
        low_watermark=args.low_watermark,
        criteria_path=args.criteria,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
