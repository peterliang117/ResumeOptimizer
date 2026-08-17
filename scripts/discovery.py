#!/usr/bin/env python3
"""Normalize, hard-screen, and queue a verified job discovery record."""

from __future__ import annotations

import argparse
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from job_store import DEFAULT_DB, DEFAULT_QUEUE, DEFAULT_TRACKER, database_enabled, export_legacy_csv, initialize, score_adjustment, upsert_job
    from match_score import extract_pay_ranges
    from screen_job import evaluate_job
    from search_criteria import SearchCriteria, read_search_criteria
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, DEFAULT_QUEUE, DEFAULT_TRACKER, database_enabled, export_legacy_csv, initialize, score_adjustment, upsert_job
    from scripts.match_score import extract_pay_ranges
    from scripts.screen_job import evaluate_job
    from scripts.search_criteria import SearchCriteria, read_search_criteria


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(candidate)
    for field in ("company", "role", "source", "url", "location", "work_mode", "employment_type", "posted_at"):
        normalized[field] = normalize_space(str(candidate.get(field) or ""))
    normalized["company_key"] = normalized["company"].lower()
    normalized["role_key"] = normalized["role"].lower()
    normalized["direct_employer"] = bool(candidate.get("direct_employer", False))
    return normalized


def classify_role_family(role: str, job_text: str) -> str:
    text = f"{role} {job_text}".lower()
    if any(term in text for term in ("cyber", "security", "grc", "risk", "iam")):
        return "cyber_risk_data"
    if "analytics engineer" in text or "semantic layer" in text or "dbt" in text:
        return "analytics_engineering"
    if any(term in text for term in ("business intelligence", "bi engineer", "reporting", "tableau")):
        return "business_intelligence"
    return "data_engineering"


def _parse_posted_at(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{raw}T00:00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def in_location_scope(location: str, work_mode: str, criteria: SearchCriteria) -> bool:
    text = f"{location} {work_mode}".lower()
    if "remote" in text and ("united states" in text or "u.s." in text or "us" in text):
        return True
    if "jersey city" in text:
        return True
    return "new york" in text and ("ny" in text or "city" in text)


def explicit_location_exclusion(job_text: str) -> bool:
    text = normalize_space(job_text).lower()
    target_metros = r"(?:nyc|new york city|new york|jersey city)\s+(?:metro(?:politan)?(?:\s+area)?|area)"
    return bool(
        re.search(rf"\bexcept\b.{{0,160}}\b{target_metros}\b", text)
        or re.search(rf"\b{target_metros}\b.{{0,160}}\b(?:excluded|not eligible|not available)\b", text)
    )


def requires_out_of_scope_relocation(job_text: str) -> bool:
    text = normalize_space(job_text).lower()
    required_residence = r"(?:must|required to)\s+(?:currently\s+)?(?:reside|live|be based)"
    relocation = r"(?:reside in or be willing to relocate to|willing to relocate to)"
    out_of_scope = r"(?:san francisco|bay area|washington,? d\.?c\.?|seattle|austin)"
    return bool(
        re.search(rf"\b(?:{required_residence}|{relocation})\b.{{0,120}}\b{out_of_scope}\b", text)
    )


def compensation_range(candidate: dict[str, Any], job_text: str) -> tuple[int | None, int | None]:
    low = candidate.get("compensation_low")
    high = candidate.get("compensation_high")
    try:
        low = int(low) if low not in (None, "") else None
    except (TypeError, ValueError):
        low = None
    try:
        high = int(high) if high not in (None, "") else None
    except (TypeError, ValueError):
        high = None
    if low is not None or high is not None:
        return low, high
    ranges = extract_pay_ranges(job_text)
    if not ranges:
        return None, None
    return min(item.low or item.high or 0 for item in ranges), max(item.high or item.low or 0 for item in ranges)


def duplicate_reason(candidate: dict[str, Any], db_path: Path, max_active_per_employer: int) -> str:
    if not database_enabled(db_path):
        return ""
    import sqlite3

    with closing(sqlite3.connect(db_path)) as conn:
        url = candidate["url"].rstrip("/")
        if url:
            existing_job = conn.execute(
                """SELECT status FROM jobs WHERE lower(rtrim(url, '/')) = lower(?)
                   AND status NOT IN ('rejected', 'rejected_low_match', 'expired', 'outdated', 'closed')
                   LIMIT 1""",
                (url,),
            ).fetchone()
            if existing_job:
                return ""
            row = conn.execute(
                """SELECT status FROM applications WHERE lower(rtrim(url, '/')) = lower(?)
                   AND status NOT IN ('rejected', 'rejected_low_match', 'expired', 'outdated', 'closed')
                   LIMIT 1""",
                (url,),
            ).fetchone()
            if row:
                return "An application with this job URL already exists."
        row = conn.execute(
            """SELECT status FROM applications WHERE lower(company) = ? AND lower(role) = ?
               AND status IN ('submitted', 'interview', 'offer', 'application_started', 'resume_ready')
               LIMIT 1""",
            (candidate["company_key"], candidate["role_key"]),
        ).fetchone()
        if row:
            return "A non-terminal application for the same company and role already exists."
        active_count = conn.execute(
            """SELECT COUNT(*) FROM jobs WHERE lower(company) = ?
               AND status IN (
                   'queued', 'analyzed', 'resume_ready', 'application_started',
                   'pending_remote_approval', 'blocked_needs_user_input',
                   'manual_apply_needed', 'submitted', 'interview', 'offer'
               )""",
            (candidate["company_key"],),
        ).fetchone()[0]
    if active_count >= max_active_per_employer:
        return (
            f"Employer already has {active_count} active application(s); "
            f"the configured limit is {max_active_per_employer}."
        )
    return ""


def hard_gate(
    candidate: dict[str, Any],
    job_text: str,
    criteria: SearchCriteria,
    db_path: Path,
    calibrated_match_score: int,
) -> dict[str, Any]:
    failures: list[str] = []
    if not candidate["company"] or not candidate["role"] or not candidate["url"]:
        failures.append("Company, role, and direct posting URL are required.")
    if not candidate["direct_employer"]:
        failures.append("Posting is not verified as a direct-employer role.")
    if not in_location_scope(candidate["location"], candidate["work_mode"], criteria):
        failures.append("Location or work mode is outside NYC, Jersey City, or remote U.S. scope.")
    elif explicit_location_exclusion(job_text):
        failures.append("Posting explicitly excludes the NYC or Jersey City metro area.")
    elif requires_out_of_scope_relocation(job_text):
        failures.append("Posting requires residence in or relocation to an out-of-scope metro area.")
    posted_at = _parse_posted_at(candidate["posted_at"])
    age_hours: float | None = None
    if not posted_at:
        failures.append("Live posting timestamp is required for the freshness rule.")
    else:
        age_hours = max(0.0, (datetime.now(timezone.utc) - posted_at).total_seconds() / 3600)
        if age_hours > criteria.maximum_freshness_hours:
            failures.append(
                f"Posting is older than the {criteria.maximum_freshness_hours}-hour freshness limit."
            )
    target_pay = criteria.target_pay
    if target_pay is None:
        failures.append("Search criteria does not define a target pay threshold.")
        target_pay = 0
    low, high = compensation_range(candidate, job_text)
    compensation_tier = ""
    if high is None:
        failures.append("Compensation is not verified against the target range.")
    elif high >= target_pay:
        compensation_tier = "A"
    elif (
        criteria.secondary_pay_floor is not None
        and high >= criteria.secondary_pay_floor
        and calibrated_match_score >= criteria.secondary_pay_min_score
    ):
        compensation_tier = "B"
    elif criteria.secondary_pay_floor is not None and high >= criteria.secondary_pay_floor:
        failures.append(
            f"Tier B compensation requires a calibrated match score of "
            f"{criteria.secondary_pay_min_score} or higher."
        )
    else:
        floor = criteria.secondary_pay_floor or target_pay
        failures.append(f"Verified compensation does not reach the ${floor:,} minimum base pay.")
    role_screen = evaluate_job(job_text, role=candidate["role"])
    failures.extend(role_screen["hard_filter_failures"])
    if calibrated_match_score < criteria.minimum_match_score:
        failures.append(
            f"Calibrated match score {calibrated_match_score} is below the configured "
            f"minimum of {criteria.minimum_match_score}."
        )
    duplicate = duplicate_reason(candidate, db_path, criteria.max_active_per_employer)
    if duplicate:
        failures.append(duplicate)
    return {
        "eligible": not failures,
        "hard_filter_failures": failures,
        "role_core": role_screen,
        "compensation_low": low,
        "compensation_high": high,
        "compensation_tier": compensation_tier,
        "posted_at": posted_at.isoformat() if posted_at else "",
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "freshness_priority": (
            "high"
            if age_hours is not None and age_hours <= criteria.preferred_freshness_hours
            else "standard"
        ),
    }


def evaluate_candidate(
    candidate: dict[str, Any],
    job_text: str,
    *,
    criteria_path: Path = Path("profile/search_criteria.md"),
    db_path: Path = DEFAULT_DB,
    base_match_score: int | None = None,
) -> dict[str, Any]:
    criteria = read_search_criteria(criteria_path)
    normalized = normalize_candidate(candidate)
    family = classify_role_family(normalized["role"], job_text)
    adjustment = score_adjustment(normalized["source"], family, path=db_path) if database_enabled(db_path) else 0
    score = base_match_score if base_match_score is not None else int(candidate.get("match_score") or 0)
    calibrated = max(0, min(100, score + adjustment))
    gate = hard_gate(normalized, job_text, criteria, db_path, calibrated)
    return {
        "candidate": normalized,
        "eligible": gate["eligible"],
        "role_family": family,
        "base_match_score": score,
        "calibration_adjustment": adjustment,
        "calibrated_match_score": calibrated,
        "hard_gate": gate,
    }


def queue_candidate_result(
    result: dict[str, Any],
    *,
    criteria_path: Path = Path("profile/search_criteria.md"),
    db_path: Path = DEFAULT_DB,
    batch_id: str = "",
    evidence: dict[str, Any] | None = None,
    queue_path: Path = DEFAULT_QUEUE,
    tracker_path: Path = DEFAULT_TRACKER,
) -> dict[str, Any]:
    if not result["eligible"]:
        raise ValueError("Candidate failed hard gates; it was not queued.")
    initialize(db_path)
    values = dict(result["candidate"])
    posted_at = _parse_posted_at(result["hard_gate"]["posted_at"])
    values.update(
        {
            "status": "queued",
            "batch_id": batch_id,
            "role_family": result["role_family"],
            "base_match_score": result["base_match_score"],
            "calibrated_match_score": result["calibrated_match_score"],
            "compensation_low": result["hard_gate"]["compensation_low"],
            "compensation_high": result["hard_gate"]["compensation_high"],
            "priority": result["hard_gate"]["freshness_priority"],
            "posted_at": result["hard_gate"]["posted_at"],
            "expires_at": (
                posted_at + timedelta(hours=read_search_criteria(criteria_path).maximum_freshness_hours)
            ).isoformat() if posted_at else "",
            "sponsorship_status": result["hard_gate"]["role_core"]["sponsorship_status"],
            "eligibility": result,
            "evidence": evidence or {},
            "notes": "Normalized discovery record passed deterministic hard gates.",
        }
    )
    upsert_job(values, path=db_path)
    export_legacy_csv(path=db_path, queue_path=queue_path, tracker_path=tracker_path)
    result["queued"] = True
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize and hard-screen one verified job candidate.")
    parser.add_argument("--candidate", type=Path, required=True, help="JSON record with discovery metadata.")
    parser.add_argument("--job", type=Path, required=True, help="Saved live job description text.")
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--queue", action="store_true", help="Persist an eligible candidate and refresh CSV exports.")
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.candidate.exists() or not args.job.exists():
        raise SystemExit("Both --candidate and --job must exist.")
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        raise SystemExit("Candidate JSON must be an object.")
    result = evaluate_candidate(candidate, args.job.read_text(encoding="utf-8"), criteria_path=args.criteria, db_path=args.db)
    if args.queue:
        try:
            queue_candidate_result(result, criteria_path=args.criteria, db_path=args.db, batch_id=args.batch_id)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
