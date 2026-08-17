#!/usr/bin/env python3
"""Verify enriched ATS snapshot jobs and persist every decision before queueing."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from discovery import evaluate_candidate, queue_candidate_result
from job_store import DEFAULT_DB, DEFAULT_QUEUE, DEFAULT_TRACKER, connection, identity_key, initialize
from match_score import score_job, split_keywords
from search_criteria import read_search_criteria, require_target_pay
from tailor import load_resume_paragraphs


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return re.sub(r"_+", "_", value) or "candidate"


def candidate_slug(candidate: dict[str, Any]) -> str:
    url_tail = str(candidate.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
    full = slugify(f"{candidate.get('company', '')}_{candidate.get('role', '')}_{url_tail}")
    digest = hashlib.sha256(str(candidate.get("url") or full).encode("utf-8")).hexdigest()[:10]
    return f"{full[:100].rstrip('_')}_{digest}"


PRESERVED_JOB_STATUSES = {
    "analyzed",
    "application_started",
    "blocked_needs_user_input",
    "interview",
    "interview_completed",
    "manual_apply_needed",
    "next_round",
    "offer",
    "pending_remote_approval",
    "rejected",
    "resume_ready",
    "skipped",
    "submitted",
}


def preserved_existing_status(candidate: dict[str, Any], db_path: Path) -> str:
    """Return a tracked workflow status that discovery must not overwrite."""
    initialize(db_path)
    key = identity_key(
        str(candidate.get("company") or ""),
        str(candidate.get("role") or ""),
        str(candidate.get("url") or ""),
        str(candidate.get("source") or ""),
    )
    with connection(db_path) as conn:
        row = conn.execute("SELECT status FROM jobs WHERE identity_key = ?", (key,)).fetchone()
    status = str(row["status"] or "").strip().casefold() if row else ""
    return status if status in PRESERVED_JOB_STATUSES else ""


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def snapshot_failure(payload: Any, *, now: datetime, max_age_minutes: int) -> str:
    if not isinstance(payload, dict):
        return "snapshot_not_an_object"
    if payload.get("schema_version") != 1:
        return "unsupported_schema_version"
    generated = parse_timestamp(payload.get("generated_at_utc"))
    if generated is None:
        return "missing_or_invalid_generated_at_utc"
    age_minutes = (now - generated).total_seconds() / 60
    if age_minutes < -5:
        return "snapshot_timestamp_is_in_the_future"
    if age_minutes > max_age_minutes:
        return "stale_snapshot"
    if payload.get("status") == "unavailable":
        return "snapshot_channel_unavailable"
    if not isinstance(payload.get("jobs"), list):
        return "snapshot_jobs_not_a_list"
    return ""


def foundational_failures(candidate: dict[str, Any], job_text: str) -> list[str]:
    failures: list[str] = []
    for field in ("company", "role", "source", "url", "location"):
        if not str(candidate.get(field) or "").strip():
            failures.append(f"missing_{field}")
    if candidate.get("direct_employer") is not True:
        failures.append("direct_employer_not_verified")
    if parse_timestamp(candidate.get("posted_at")) is None:
        failures.append("missing_live_posted_at")
    if len(job_text.strip()) < 200:
        failures.append("missing_exact_job_description")
    return failures


def recency_prefix(posted_at: str, *, now: datetime) -> str:
    parsed = parse_timestamp(posted_at)
    if parsed is None:
        return ""
    hours = max(0, round((now - parsed).total_seconds() / 3600))
    if hours < 24:
        return f"Posted {hours} hours ago.\n"
    return f"Posted {round(hours / 24)} days ago.\n"


def verify_snapshot(
    payload: Any,
    *,
    resume_text: str,
    profile_text: str,
    criteria_path: Path,
    db_path: Path,
    source_dir: Path,
    candidates_dir: Path,
    reviews_dir: Path,
    report_path: Path,
    queue_path: Path = DEFAULT_QUEUE,
    tracker_path: Path = DEFAULT_TRACKER,
    queue: bool = False,
    capacity: int = 10,
    batch_id: str = "",
    max_age_minutes: int = 150,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": 1,
        "verified_at_utc": current.isoformat(),
        "snapshot_generated_at_utc": payload.get("generated_at_utc", "") if isinstance(payload, dict) else "",
        "status": "ok",
        "channel_failure": "",
        "summary": {"candidates": 0, "eligible": 0, "queued": 0, "rejected": 0},
        "decisions": [],
    }
    channel_failure = snapshot_failure(payload, now=current, max_age_minutes=max_age_minutes)
    if channel_failure:
        report["status"] = "channel_failure"
        report["channel_failure"] = channel_failure
        atomic_json(report_path, report)
        return report

    criteria = read_search_criteria(criteria_path)
    target_pay = require_target_pay(None, criteria_path)
    batch = batch_id or f"ats-{current.strftime('%Y%m%dT%H%M%SZ')}"
    eligible: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, Any], str]] = []
    decisions: list[dict[str, Any]] = []

    for raw in payload["jobs"]:
        candidate = dict(raw) if isinstance(raw, dict) else {}
        job_text = str(candidate.pop("job_description", "") or "")
        slug = candidate_slug(candidate)
        source_path = source_dir / f"{slug}.txt"
        candidate_path = candidates_dir / f"{slug}.json"
        review_path = reviews_dir / f"{slug}.json"
        failures = foundational_failures(candidate, job_text)
        decision: dict[str, Any] = {
            "company": str(candidate.get("company") or ""),
            "role": str(candidate.get("role") or ""),
            "url": str(candidate.get("url") or ""),
            "eligible": False,
            "queued": False,
            "failures": failures,
            "candidate_record": str(candidate_path),
            "review_record": str(review_path),
        }
        if failures:
            atomic_json(candidate_path, {"candidate": candidate, "verification_failures": failures})
            atomic_json(review_path, {"eligible": False, "hard_filter_failures": failures})
            decisions.append(decision)
            continue

        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(job_text.rstrip() + "\n", encoding="utf-8")
        digest = hashlib.sha256(job_text.encode("utf-8")).hexdigest()
        scoring_text = recency_prefix(str(candidate.get("posted_at") or ""), now=current) + job_text
        score = score_job(
            job_text=scoring_text,
            resume_text=resume_text,
            profile_text=profile_text,
            keywords=split_keywords(None),
            target_pay=target_pay,
            pay_tolerance=15000,
            max_age_days=max(1, criteria.maximum_freshness_hours // 24),
        )
        candidate["match_score"] = score["score"]
        evaluation = evaluate_candidate(
            candidate,
            job_text,
            criteria_path=criteria_path,
            db_path=db_path,
            base_match_score=score["score"],
        )
        evidence = {
            "snapshot_generated_at_utc": payload["generated_at_utc"],
            "freshness_source": candidate.get("freshness_source", ""),
            "job_description_sha256": digest,
            "job_description_path": str(source_path),
        }
        atomic_json(candidate_path, {"candidate": candidate, "evidence": evidence})
        atomic_json(review_path, {"evaluation": evaluation, "match_score": score, "evidence": evidence})
        decision.update(
            {
                "eligible": evaluation["eligible"],
                "failures": evaluation["hard_gate"]["hard_filter_failures"],
                "base_match_score": evaluation["base_match_score"],
                "calibrated_match_score": evaluation["calibrated_match_score"],
                "job_description_path": str(source_path),
                "job_description_sha256": digest,
            }
        )
        decisions.append(decision)
        if evaluation["eligible"]:
            eligible.append((candidate, job_text, evaluation, evidence, slug))

    eligible.sort(key=lambda item: item[2]["calibrated_match_score"], reverse=True)
    if queue:
        queued = 0
        for candidate, job_text, evaluation, evidence, slug in eligible:
            if queued >= capacity:
                break
            matching_decision = next(item for item in decisions if item["review_record"].endswith(f"{slug}.json"))
            existing_status = preserved_existing_status(candidate, db_path)
            if existing_status:
                matching_decision["queue_disposition"] = "preserved_existing_status"
                matching_decision["existing_status"] = existing_status
                continue
            # Re-evaluate serially so duplicate and employer-concentration checks see earlier writes.
            current_evaluation = evaluate_candidate(
                candidate,
                job_text,
                criteria_path=criteria_path,
                db_path=db_path,
                base_match_score=evaluation["base_match_score"],
            )
            if not current_evaluation["eligible"]:
                matching_decision["eligible"] = False
                matching_decision["failures"] = current_evaluation["hard_gate"]["hard_filter_failures"]
                continue
            queue_candidate_result(
                current_evaluation,
                criteria_path=criteria_path,
                db_path=db_path,
                batch_id=batch,
                evidence=evidence,
                queue_path=queue_path,
                tracker_path=tracker_path,
            )
            matching_decision["queued"] = True
            queued += 1

    report["decisions"] = decisions
    report["summary"] = {
        "candidates": len(decisions),
        "eligible": sum(1 for item in decisions if item["eligible"]),
        "queued": sum(1 for item in decisions if item["queued"]),
        "rejected": sum(1 for item in decisions if not item["eligible"]),
    }
    atomic_json(report_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify an ATS discovery snapshot and optionally queue eligible jobs.")
    parser.add_argument("--snapshot", type=Path, default=Path("outputs/ats_discovery_snapshot.json"))
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-dir", type=Path, default=Path("jobs/source"))
    parser.add_argument("--candidates-dir", type=Path, default=Path("jobs/candidates"))
    parser.add_argument("--reviews-dir", type=Path, default=Path("jobs/candidate_reviews"))
    parser.add_argument("--report", type=Path, default=Path("outputs/discovery_verification_report.json"))
    parser.add_argument("--capacity", type=int, default=10)
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--max-snapshot-age-minutes", type=int, default=150)
    parser.add_argument("--queue", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.snapshot.exists():
        payload: Any = {}
    else:
        try:
            payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")
    resume_text = "\n".join(paragraph.text for paragraph in load_resume_paragraphs(args.resume))
    profile_text = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    report = verify_snapshot(
        payload,
        resume_text=resume_text,
        profile_text=profile_text,
        criteria_path=args.criteria,
        db_path=args.db,
        source_dir=args.source_dir,
        candidates_dir=args.candidates_dir,
        reviews_dir=args.reviews_dir,
        report_path=args.report,
        queue=args.queue,
        capacity=max(0, args.capacity),
        batch_id=args.batch_id,
        max_age_minutes=args.max_snapshot_age_minutes,
    )
    print(json.dumps({"status": report["status"], "channel_failure": report["channel_failure"], **report["summary"]}, indent=2))
    return 2 if report["status"] == "channel_failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())
