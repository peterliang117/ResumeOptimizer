#!/usr/bin/env python3
"""Stable command facade for the local job-application workflow."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from datetime import datetime, timezone
from pathlib import Path

from job_queue import QUEUE_FIELDS
from job_queue import batch_progress
from job_queue import latest_batch_id
from job_queue import read_rows as read_queue_rows
from job_queue import sorted_queued_rows
from job_queue import write_rows as write_queue_rows
from linkedin_search import build_linkedin_jobs_url
from match_score import score_job
from match_score import split_keywords
from search_criteria import read_search_criteria
from search_criteria import require_target_pay
from tailor import load_job_text
from tailor import load_resume_paragraphs
from tracker import read_rows as read_tracker_rows


ROOT = Path(__file__).resolve().parents[1]


def script_path(name: str) -> str:
    return str(ROOT / "scripts" / name)


def run_step(label: str, command: list[str], *, allow_failure: bool = False) -> int:
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False, text=True)
    if result.returncode != 0 and not allow_failure:
        raise SystemExit(result.returncode)
    return result.returncode


def abs_value(value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return str(path)


def command_status(args: argparse.Namespace) -> int:
    run_step(
        "Batch status",
        [
            sys.executable,
            script_path("job_queue.py"),
            "batch-status",
            "--target-size",
            str(args.target_size),
        ],
        allow_failure=True,
    )
    run_step(
        "Next queued job",
        [sys.executable, script_path("job_queue.py"), "next"],
        allow_failure=True,
    )
    print_tracker_summary(args.tracker, limit=args.followup_limit)
    run_step(
        "Local LLM status",
        [
            sys.executable,
            script_path("local_llm_status.py"),
            "--check-server",
        ],
        allow_failure=True,
    )
    return 0


def print_tracker_summary(path: Path, *, limit: int) -> None:
    print("\n== Tracker summary ==", flush=True)
    rows = read_tracker_rows(path)
    if not rows:
        print(f"No tracker rows found: {path}")
        return

    counts = Counter(row.get("status", "") or "(blank)" for row in rows)
    for status, count in counts.most_common():
        print(f"{status}: {count}")

    today = date.today().isoformat()
    followups = sorted(
        (
            row
            for row in rows
            if row.get("follow_up_date", "") and row.get("follow_up_date", "") <= today
        ),
        key=lambda row: (
            row.get("follow_up_date", ""),
            row.get("company", "").lower(),
            row.get("role", "").lower(),
        ),
    )
    if not followups:
        print("Due follow-ups: none")
        return

    print("Due follow-ups:")
    for row in followups[:limit]:
        print(
            f"- {row.get('follow_up_date', '')}: {row.get('company', '')} - "
            f"{row.get('role', '')} ({row.get('status', '')})"
        )
    if len(followups) > limit:
        print(f"... {len(followups) - limit} more")


def command_verify(args: argparse.Namespace) -> int:
    run_step(
        "Doctor",
        [
            sys.executable,
            script_path("doctor.py"),
            "--check-local-llm",
        ],
    )
    run_step(
        "Tracker verification",
        [sys.executable, script_path("verify_tracker.py")],
    )
    run_step(
        "Tracker report",
        [sys.executable, script_path("tracker_report.py")],
    )
    return 0


def command_optimize(args: argparse.Namespace) -> int:
    run_step(
        "Import historical workflow barriers",
        [
            sys.executable,
            script_path("workflow_optimizer.py"),
            "bootstrap-history",
            "--tracker",
            str(args.tracker),
        ],
    )
    run_step(
        "Refresh workflow optimization report",
        [
            sys.executable,
            script_path("workflow_optimizer.py"),
            "report",
            "--out",
            str(args.out),
            "--summary",
        ],
    )
    return 0


def command_run_current_batch(args: argparse.Namespace) -> int:
    run_step(
        "Required first branch check",
        [
            sys.executable,
            script_path("job_queue.py"),
            "batch-status",
            "--target-size",
            str(args.target_size),
        ],
    )
    queued = sorted_queued_rows(read_queue_rows(args.queue))
    if not queued:
        print("No queued jobs to process.")
        print("Next step: run refill-next-batch, screen/review candidates, then apply the reviewed refill.")
        return 0

    command = [
        sys.executable,
        script_path("automation_pipeline.py"),
        "--resume",
        str(args.resume),
        "--min-score",
        str(args.min_score),
        "--limit",
        str(args.limit),
    ]
    if args.llm_provider:
        command.extend(["--llm-provider", args.llm_provider])
    if args.model:
        command.extend(["--model", args.model])
    run_step("Process current queued jobs", command)
    return 0


def queue_identity_keys(row: dict[str, str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    url = row.get("url", "").strip().rstrip("/").lower()
    company = row.get("company", "").strip().lower()
    role = row.get("role", "").strip().lower()
    if url:
        keys.add(("url", url))
    if company and role:
        keys.add(("company_role", f"{company}::{role}"))
    return keys


def next_batch_id(rows: list[dict[str, str]]) -> str:
    prefix = date.today().isoformat()
    existing = {
        row.get("batch_id", "").strip()
        for row in rows
        if row.get("batch_id", "").strip().startswith(prefix)
    }
    index = 1
    while True:
        candidate = f"{prefix}-{index:02d}"
        if candidate not in existing:
            return candidate
        index += 1


def ensure_refill_ready(queue_path: Path, target_size: int) -> tuple[list[dict[str, str]], str]:
    rows = read_queue_rows(queue_path)
    queued = sorted_queued_rows(rows)
    if queued:
        raise SystemExit("Refill blocked: queue still has queued jobs. Run current queued jobs first.")

    current_batch_id = latest_batch_id(rows)
    if not current_batch_id:
        return rows, next_batch_id(rows)

    terminal, open_rows, slots_remaining, refill_ready = batch_progress(rows, current_batch_id, target_size)
    print(f"batch_id={current_batch_id}")
    print(f"terminal_or_handoff={len(terminal)}")
    print(f"open={len(open_rows)}")
    print(f"slots_remaining={slots_remaining}")
    print(f"refill_ready={'yes' if refill_ready else 'no'}")
    if not refill_ready:
        raise SystemExit("Refill blocked: latest batch still has open jobs.")
    return rows, next_batch_id(rows)


def existing_keys(queue_rows: list[dict[str, str]], tracker_rows: list[dict[str, str]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in [*queue_rows, *tracker_rows]:
        keys.update(queue_identity_keys(row))
    return keys


def existing_refill_report_keys(outputs_dir: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not outputs_dir.exists():
        return keys
    for path in outputs_dir.glob("refill_candidates_*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidates = report.get("candidates", [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict):
                keys.update(queue_identity_keys(candidate))
    return keys


def refill_report_candidate_count(path: Path) -> int:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    candidates = report.get("candidates", [])
    return len(candidates) if isinstance(candidates, list) else 0


def default_refill_report_path(batch_id: str) -> Path:
    base = ROOT / "outputs" / f"refill_candidates_{batch_id}.json"
    if not base.exists() or refill_report_candidate_count(base) == 0:
        return base
    for path in sorted((ROOT / "outputs").glob(f"refill_candidates_{batch_id}_*.json")):
        if refill_report_candidate_count(path) == 0:
            return path
    for index in range(2, 100):
        candidate = ROOT / "outputs" / f"refill_candidates_{batch_id}_{index:02d}.json"
        if not candidate.exists():
            return candidate
    raise SystemExit(f"Could not find an unused refill report path for batch_id={batch_id}.")


def build_linkedin_searches(criteria_path: Path) -> list[dict[str, str]]:
    criteria = read_search_criteria(criteria_path)
    keyword_terms = [item for item in (criteria.keyword_terms or [criteria.keywords or ""]) if item]
    missing = []
    if not keyword_terms:
        missing.append("keywords")
    if not criteria.locations:
        missing.append("locations")
    if not criteria.date_posted:
        missing.append("date posted")
    if missing:
        raise SystemExit(f"Refill blocked: missing search criteria: {', '.join(missing)} in {criteria_path}")

    searches = []
    for keywords in keyword_terms:
        for location in criteria.locations:
            searches.append(
                {
                    "keywords": keywords,
                    "location": location,
                    "url": build_linkedin_jobs_url(
                        keywords=keywords,
                        location=location,
                        date_posted=criteria.date_posted,
                        min_salary=criteria.target_pay,
                        easy_apply=False,
                    ),
                }
            )
    return searches


def collect_ats_candidates(config_path: Path, known_keys: set[tuple[str, str]]) -> tuple[list[dict[str, object]], list[str]]:
    if not config_path.exists():
        return [], [f"ATS config not found: {config_path}"]

    from ats_scan import FETCHERS, include_job, read_config
    import requests

    config = read_config(config_path)
    candidates: list[dict[str, object]] = []
    warnings: list[str] = []
    seen = set(known_keys)
    for company in config.companies:
        if not company.enabled:
            continue
        fetcher = FETCHERS.get(company.provider)
        if not fetcher:
            warnings.append(f"Skipping unsupported provider for {company.name}: {company.provider}")
            continue
        try:
            jobs = fetcher(company)
        except requests.RequestException as exc:
            warnings.append(f"Could not fetch {company.name}: {exc}")
            continue
        for job in jobs:
            if not include_job(job, config):
                continue
            keys = queue_identity_keys(job)
            if seen.intersection(keys):
                continue
            seen.update(keys)
            candidates.append(candidate_record(job, source_type="ats"))
    return candidates, warnings


def candidate_record(job: dict[str, str], *, source_type: str) -> dict[str, object]:
    return {
        "approved": False,
        "hard_filters_passed": False,
        "company": job.get("company", ""),
        "role": job.get("role", ""),
        "source": job.get("source", ""),
        "url": job.get("url", ""),
        "location": job.get("location", ""),
        "priority": "medium",
        "match_score": None,
        "source_type": source_type,
        "verification_notes": "",
    }


def write_refill_report(args: argparse.Namespace, batch_id: str, queue_rows: list[dict[str, str]]) -> Path:
    tracker_rows = read_tracker_rows(args.tracker)
    known_keys = existing_keys(queue_rows, tracker_rows)
    known_keys.update(existing_refill_report_keys(ROOT / "outputs"))
    linkedin_searches = build_linkedin_searches(args.criteria)
    ats_candidates: list[dict[str, object]] = []
    warnings: list[str] = []
    if not args.skip_ats:
        ats_candidates, warnings = collect_ats_candidates(args.portals, known_keys)

    output = args.out or default_refill_report_path(batch_id)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "review_required",
        "target_size": args.target_size,
        "candidate_batch_id": batch_id,
        "candidate_summary": {
            "ats_candidates": len(ats_candidates),
            "linkedin_searches": len(linkedin_searches),
            "dedupe_scope": "queue, tracker, and prior refill reports",
            "empty_reason": (
                "No new ATS candidates after excluding queue, tracker, and prior refill report duplicates."
                if not ats_candidates and not args.skip_ats
                else ""
            ),
        },
        "apply_command": f".\\scripts\\jobctl.ps1 refill-next-batch --apply-reviewed {output}",
        "review_requirements": [
            "Mark 1 to target_size candidates with approved=true.",
            "Set hard_filters_passed=true only after verifying location, pay, sponsorship, recency, employer type, and unsupported-skill blockers.",
            "Set match_score to an integer from 0 to 100 for every approved candidate.",
            "Do not approve staffing firms, explicit no-sponsorship roles, closed posts, duplicates, or roles outside the private search criteria.",
        ],
        "linkedin_searches": linkedin_searches,
        "candidates": ats_candidates,
        "warnings": warnings,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote refill review report: {output}")
    print(f"LinkedIn searches: {len(linkedin_searches)}")
    print(f"ATS candidates: {len(ats_candidates)}")
    if not ats_candidates and not args.skip_ats:
        print("No new ATS candidates after excluding queue, tracker, and prior refill report duplicates.")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    print("Queue was not modified. Review the report, approve 1 to target_size candidates, then run --apply-reviewed.")
    return output


def normalize_score(value: object, *, company: str, role: str) -> str:
    if isinstance(value, bool):
        raise SystemExit(f"Approved candidate has invalid match_score: {company} - {role}")
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit(f"Approved candidate is missing integer match_score: {company} - {role}")
    if not 0 <= score <= 100:
        raise SystemExit(f"Approved candidate match_score must be 0-100: {company} - {role}")
    return str(score)


def approved_candidates(report: dict[str, object], target_size: int) -> list[dict[str, object]]:
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        raise SystemExit("Reviewed report must contain a candidates array.")
    approved = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("approved") is True
    ]
    if not approved:
        raise SystemExit("Refill requires at least 1 approved candidate; found 0.")
    if len(approved) > target_size:
        raise SystemExit(f"Refill accepts at most {target_size} approved candidates; found {len(approved)}.")
    return approved


def command_apply_reviewed_refill(args: argparse.Namespace) -> int:
    queue_rows, batch_id = ensure_refill_ready(args.queue, args.target_size)
    report = json.loads(args.apply_reviewed.read_text(encoding="utf-8"))
    report_batch_id = str(report.get("candidate_batch_id") or "").strip()
    if report_batch_id:
        batch_id = report_batch_id

    tracker_rows = read_tracker_rows(args.tracker)
    known = existing_keys(queue_rows, tracker_rows)
    additions: list[dict[str, str]] = []
    for candidate in approved_candidates(report, args.target_size):
        company = str(candidate.get("company", "")).strip()
        role = str(candidate.get("role", "")).strip()
        source = str(candidate.get("source", "")).strip() or "Reviewed"
        url = str(candidate.get("url", "")).strip()
        priority = str(candidate.get("priority", "")).strip() or "medium"
        notes = str(candidate.get("verification_notes", "")).strip()
        location = str(candidate.get("location", "")).strip()
        if not company or not role or not url:
            raise SystemExit("Approved candidates must include company, role, and url.")
        if candidate.get("hard_filters_passed") is not True:
            raise SystemExit(f"Approved candidate did not pass hard filters: {company} - {role}")
        row = {
            "company": company,
            "role": role,
            "source": source,
            "url": url,
            "status": "queued",
            "priority": priority,
            "batch_id": batch_id,
            "match_score": normalize_score(candidate.get("match_score"), company=company, role=role),
            "notes": notes or f"Reviewed refill candidate. Location: {location}",
        }
        keys = queue_identity_keys(row)
        if known.intersection(keys):
            raise SystemExit(f"Duplicate approved candidate blocked: {company} - {role}")
        known.update(keys)
        additions.append(row)

    write_queue_rows(args.queue, [*queue_rows, *additions])
    print(f"Added {len(additions)} reviewed candidates to {args.queue} with batch_id={batch_id}.")
    run_step("Verify tracker", [sys.executable, script_path("verify_tracker.py")], allow_failure=True)
    return 0


def command_refill_next_batch(args: argparse.Namespace) -> int:
    if args.apply_reviewed:
        return command_apply_reviewed_refill(args)
    queue_rows, batch_id = ensure_refill_ready(args.queue, args.target_size)
    write_refill_report(args, batch_id, queue_rows)
    return 0


SPONSORSHIP_BLOCKER_PATTERNS = [
    r"\b(no|not|cannot|can't|unable to)\s+(?:provide\s+)?(?:visa\s+)?sponsor",
    r"\bwithout\s+(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship\b",
    r"\bnow\s+or\s+in\s+the\s+future\b.{0,80}\bsponsor",
    r"\bsponsor\b.{0,80}\bnow\s+or\s+in\s+the\s+future\b",
]

AUTHORIZATION_BLOCKER_PATTERNS = [
    r"\bu\.?s\.?\s+person\b",
    r"\bu\.?s\.?\s+citizen(?:ship)?\b",
    r"\bgreen\s+card\b",
    r"\bpermanent\s+resident\b",
    r"\bsecurity\s+clearance\b",
    r"\bsecret\s+clearance\b",
]

SENSITIVE_REVIEW_PATTERNS = [
    r"\bbackground\s+check\b",
    r"\bprivacy\s+policy\b",
    r"\bterms\s+(?:and|&)\s+conditions\b",
    r"\bself[-\s]?identification\b",
]

UNSUPPORTED_REVIEW_TERMS = [
    "airflow",
    "databricks",
    "dbt",
    "looker",
    "omni",
    "rag",
    "snowflake",
]


def regex_hits(patterns: list[str], text: str) -> list[str]:
    hits = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            hits.append(pattern)
    return hits


def detect_location_blockers(candidate: dict[str, object], criteria_locations: list[str]) -> list[str]:
    location = str(candidate.get("location", "")).strip().lower()
    if not location or not criteria_locations:
        return []

    acceptable_phrases: set[str] = set()
    for item in criteria_locations:
        normalized = item.lower()
        acceptable_phrases.add(normalized)
        if "new york" in normalized:
            acceptable_phrases.update({"new york", "new york city", "nyc", "ny, ny"})
        if "jersey city" in normalized:
            acceptable_phrases.update({"jersey city"})

    if any(phrase and phrase in location for phrase in acceptable_phrases):
        return []
    if "remote" in location and ("united states" in location or "usa" in location or "us" in location):
        return []
    return [f"Candidate location is outside configured locations: {candidate.get('location', '')}"]


def detect_title_blockers(candidate: dict[str, object]) -> list[str]:
    role = str(candidate.get("role", "")).lower()
    blockers = []
    if re.search(r"\b(intern|internship|junior|entry[-\s]?level)\b", role):
        blockers.append("Title indicates intern, junior, or entry-level role.")
    return blockers


def detect_title_review_flags(candidate: dict[str, object]) -> list[str]:
    role = str(candidate.get("role", "")).lower()
    flags = []
    if re.search(r"\bph\.?d\b|\bphd\b", role):
        flags.append("Title indicates PhD-specific role; verify fit before queueing.")
    if re.search(r"\b(marketing|growth|credit|market data|c\+\+)\b", role):
        flags.append("Title may be outside the core data-engineering or security-analytics target.")
    return flags


def detect_review_flags(job_text: str, score_payload: dict[str, object]) -> list[str]:
    text = job_text.lower()
    flags = []
    missing = {str(item).lower() for item in score_payload.get("missing_keywords", []) if isinstance(item, str)}
    for term in UNSUPPORTED_REVIEW_TERMS:
        if term in text and term in missing:
            flags.append(f"Job mentions unconfirmed or unsupported term: {term}")
    if int(score_payload.get("seniority_score", 0) or 0) <= 60:
        flags.append("Seniority signal may be above target; verify level fit.")
    if regex_hits(SENSITIVE_REVIEW_PATTERNS, job_text):
        flags.append("Job text includes legal/privacy/background-check wording; keep application answers review-gated.")
    return flags


def screen_decision(blockers: list[str], flags: list[str], score_payload: dict[str, object], min_score: int) -> str:
    if blockers:
        return "reject"
    score = int(score_payload.get("score", 0) or 0)
    pay_score = int(score_payload.get("pay_score", 0) or 0)
    pay_ranges = score_payload.get("pay_ranges", [])
    if score < min_score:
        return "reject"
    if pay_ranges and pay_score == 0:
        return "reject"
    material_flags = [
        flag
        for flag in flags
        if not flag.startswith("Job text includes legal/privacy/background-check wording")
    ]
    if material_flags:
        return "review"
    return "candidate"


def command_screen_refill_candidates(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        raise SystemExit("Refill report must contain a candidates array.")

    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")
    resume_text = "\n".join(paragraph.text for paragraph in load_resume_paragraphs(args.resume))
    profile_text = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    target_pay = require_target_pay(args.target_pay, args.criteria)
    criteria = read_search_criteria(args.criteria)

    screened = 0
    failed = 0
    recommended = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        url = str(candidate.get("url", "")).strip()
        if not url:
            continue
        company = str(candidate.get("company", "")).strip()
        role = str(candidate.get("role", "")).strip()
        try:
            job_text = load_job_text(job_url=url)
            score_payload = score_job(
                job_text=job_text,
                resume_text=resume_text,
                profile_text=profile_text,
                keywords=split_keywords(args.keywords),
                target_pay=target_pay,
                pay_tolerance=args.pay_tolerance,
                max_age_days=args.max_age_days,
            )
        except SystemExit as exc:
            failed += 1
            candidate["auto_screen"] = {
                "screened_at": datetime.now(timezone.utc).isoformat(),
                "decision": "fetch_failed",
                "blockers": [str(exc)],
                "review_flags": [],
                "score": None,
            }
            continue

        blockers = []
        blockers.extend(detect_location_blockers(candidate, criteria.locations))
        blockers.extend(detect_title_blockers(candidate))
        if regex_hits(SPONSORSHIP_BLOCKER_PATTERNS, job_text):
            blockers.append("Job text has explicit sponsorship blocker.")
        if regex_hits(AUTHORIZATION_BLOCKER_PATTERNS, job_text):
            blockers.append("Job text has citizenship, permanent-resident, US-person, or clearance wording.")
        flags = detect_title_review_flags(candidate)
        flags.extend(detect_review_flags(job_text, score_payload))
        decision = screen_decision(blockers, flags, score_payload, args.min_score)

        candidate["match_score"] = score_payload["score"]
        candidate["auto_screen"] = {
            "screened_at": datetime.now(timezone.utc).isoformat(),
            "decision": decision,
            "blockers": blockers,
            "review_flags": flags,
            "score": score_payload,
        }
        if decision == "candidate":
            recommended += 1
        screened += 1
        print(f"{decision.upper()}: {company} - {role} score={score_payload['score']}")

    report["last_auto_screened_at"] = datetime.now(timezone.utc).isoformat()
    report["auto_screen_summary"] = {
        "screened": screened,
        "fetch_failed": failed,
        "recommended_candidates": recommended,
        "min_score": args.min_score,
        "note": "Auto-screening does not set approved=true or hard_filters_passed=true.",
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated refill report: {args.report}")
    print(f"Screened={screened}; fetch_failed={failed}; recommended_candidates={recommended}")
    return 0


REFILL_REVIEW_FIELDS = [
    "index",
    "decision",
    "recommended_action",
    "approved",
    "hard_filters_passed",
    "match_score",
    "company",
    "role",
    "source",
    "location",
    "priority",
    "url",
    "blockers",
    "review_flags",
    "score_reasons",
    "skill_score",
    "pay_score",
    "seniority_score",
    "recency_score",
    "matched_keywords",
    "transferable_keywords",
    "missing_keywords",
    "pay_ranges",
    "verification_notes",
]


def join_cell(values: object) -> str:
    if values is None:
        return ""
    if isinstance(values, list):
        return " | ".join(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item) for item in values)
    if isinstance(values, dict):
        return json.dumps(values, ensure_ascii=False)
    return str(values)


def recommended_action(decision: str) -> str:
    if decision == "candidate":
        return "review_then_approve_if_verified"
    if decision == "review":
        return "manual_review_required"
    if decision == "reject":
        return "do_not_queue"
    if decision == "fetch_failed":
        return "open_manually_or_ignore"
    return "screen_first"


def refill_review_row(index: int, candidate: dict[str, object]) -> dict[str, str]:
    auto_screen = candidate.get("auto_screen") if isinstance(candidate.get("auto_screen"), dict) else {}
    assert isinstance(auto_screen, dict)
    score = auto_screen.get("score") if isinstance(auto_screen.get("score"), dict) else {}
    assert isinstance(score, dict)
    decision = str(auto_screen.get("decision") or "unscreened")
    return {
        "index": str(index),
        "decision": decision,
        "recommended_action": recommended_action(decision),
        "approved": str(candidate.get("approved", False)).lower(),
        "hard_filters_passed": str(candidate.get("hard_filters_passed", False)).lower(),
        "match_score": join_cell(candidate.get("match_score") if candidate.get("match_score") is not None else score.get("score")),
        "company": join_cell(candidate.get("company")),
        "role": join_cell(candidate.get("role")),
        "source": join_cell(candidate.get("source")),
        "location": join_cell(candidate.get("location")),
        "priority": join_cell(candidate.get("priority")),
        "url": join_cell(candidate.get("url")),
        "blockers": join_cell(auto_screen.get("blockers")),
        "review_flags": join_cell(auto_screen.get("review_flags")),
        "score_reasons": join_cell(score.get("reasons")),
        "skill_score": join_cell(score.get("skill_score")),
        "pay_score": join_cell(score.get("pay_score")),
        "seniority_score": join_cell(score.get("seniority_score")),
        "recency_score": join_cell(score.get("recency_score")),
        "matched_keywords": join_cell(score.get("matched_keywords")),
        "transferable_keywords": join_cell(score.get("transferable_keywords")),
        "missing_keywords": join_cell(score.get("missing_keywords")),
        "pay_ranges": join_cell(score.get("pay_ranges")),
        "verification_notes": join_cell(candidate.get("verification_notes")),
    }


def command_review_refill_report(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        raise SystemExit("Refill report must contain a candidates array.")

    output = args.out or args.report.with_suffix(".csv")
    rows: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        row = refill_review_row(index, candidate)
        if args.actionable_only and row["decision"] not in {"candidate", "review", "fetch_failed"}:
            continue
        rows.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REFILL_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote refill review CSV: {output}")
    print(f"Rows: {len(rows)}")
    print("CSV is for review only. Apply queue changes with the JSON report and refill-next-batch --apply-reviewed.")
    return 0


def next_queued_row(queue_path: Path) -> dict[str, str]:
    rows = read_queue_rows(queue_path)
    queued = sorted_queued_rows(rows)
    if not queued:
        raise SystemExit("No queued jobs.")
    return queued[0]


def command_prepare_next_packet(args: argparse.Namespace) -> int:
    row = next_queued_row(args.queue)
    company = row.get("company", "")
    role = row.get("role", "")
    url = row.get("url", "")
    source = row.get("source", "") or "LinkedIn"
    if not company or not role or not url:
        raise SystemExit("Next queued job is missing company, role, or URL.")

    command = [
        sys.executable,
        script_path("run_application_pipeline.py"),
        "--company",
        company,
        "--role",
        role,
        "--source",
        source,
        "--job-url",
        url,
        "--resume",
        str(args.resume),
    ]
    if args.llm_provider:
        command.extend(["--llm-provider", args.llm_provider])
    if args.model:
        command.extend(["--model", args.model])
    run_step(f"Prepare packet for {company} - {role}", command)
    return 0


def load_answers(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Application answers file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_tracker_row(args: argparse.Namespace) -> dict[str, str]:
    rows = read_tracker_rows(args.tracker)
    for row in rows:
        if args.url and row.get("url") == args.url:
            return row
        if (
            args.company
            and args.role
            and row.get("company", "").lower() == args.company.lower()
            and row.get("role", "").lower() == args.role.lower()
        ):
            return row
    raise SystemExit("Could not find a tracker row for the requested application.")


def resolve_resume_file(row: dict[str, str]) -> str:
    resume_file = row.get("resume_file", "").strip()
    if resume_file:
        return abs_value(resume_file)

    folder = row.get("application_folder", "").strip()
    if not folder:
        return ""
    folder_path = Path(abs_value(folder))
    if not folder_path.exists():
        return ""
    matches = sorted(folder_path.glob("*Resume.docx"))
    if not matches:
        return ""
    return str(matches[0])


def allowed_answer_fields(answers: dict) -> dict[str, object]:
    policy = answers.get("answer_policy", {})
    allowed: dict[str, object] = {}
    if policy.get("allow_prefill_standard_fields", False):
        allowed["standard_fields"] = answers.get("standard_fields", {})
    if policy.get("allow_prefill_exact_work_authorization", False):
        allowed["work_authorization"] = answers.get("work_authorization", {})
    if policy.get("allow_prefill_sensitive_self_id", False):
        allowed["self_identification"] = answers.get("self_identification", {})
    return allowed


def command_generate_handoff(args: argparse.Namespace) -> int:
    row = find_tracker_row(args)
    answers = load_answers(args.answers)
    folder_value = row.get("application_folder", "").strip()
    folder = Path(abs_value(folder_value)) if folder_value else ROOT / "outputs"
    folder.mkdir(parents=True, exist_ok=True)
    output = args.out or folder / "agent_handoff.json"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company": row.get("company", ""),
        "role": row.get("role", ""),
        "application_url": row.get("url", ""),
        "status": row.get("status", ""),
        "resume_file": resolve_resume_file(row),
        "application_folder": abs_value(folder_value),
        "allowed_fields": allowed_answer_fields(answers),
        "answer_sources": [
            str((ROOT / "profile" / "facts.md").resolve()),
            str(args.answers.resolve()),
        ],
        "blocked_actions": [
            "Do not click final submit.",
            "Do not accept legal attestations, background-check consent, terms, or privacy acknowledgements without explicit human confirmation.",
            "Do not infer missing work authorization, sponsorship, compensation, relocation, self-identification, legal, or privacy answers.",
            "Do not add unsupported skills, tools, employers, metrics, or experience.",
        ],
        "review_required": {
            "final_submit": True,
            "legal_attestations": True,
            "unknown_questions": True,
            "custom_essay_answers": True,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote handoff: {output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable commands for the local job workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show batch, queue, tracker, and local LLM status.")
    status.add_argument("--target-size", type=int, default=10)
    status.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    status.add_argument("--followup-limit", type=int, default=8)
    status.set_defaults(func=command_status)

    verify = subparsers.add_parser("verify", help="Run local setup and tracker checks.")
    verify.set_defaults(func=command_verify)

    optimize = subparsers.add_parser("optimize", help="Learn from local workflow barriers and refresh recommendations.")
    optimize.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    optimize.add_argument("--out", type=Path, default=Path("outputs/workflow_optimization_report.json"))
    optimize.set_defaults(func=command_optimize)

    run_batch = subparsers.add_parser("run-current-batch", help="Process queued jobs without refilling.")
    run_batch.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    run_batch.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    run_batch.add_argument("--min-score", type=int, default=75)
    run_batch.add_argument("--limit", type=int, default=10)
    run_batch.add_argument("--target-size", type=int, default=10)
    run_batch.add_argument("--llm-provider", choices=["codex", "auto", "azure", "local", "none"], default="codex")
    run_batch.add_argument("--model")
    run_batch.set_defaults(func=command_run_current_batch)

    refill = subparsers.add_parser("refill-next-batch", help="Generate or apply a guarded next-batch refill.")
    refill.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    refill.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    refill.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    refill.add_argument("--portals", type=Path, default=Path("profile/portals.yml"))
    refill.add_argument("--target-size", type=int, default=10)
    refill.add_argument("--skip-ats", action="store_true", help="Only generate LinkedIn search URLs; do not fetch ATS feeds.")
    refill.add_argument("--out", type=Path, help="Review report path for candidate discovery mode.")
    refill.add_argument("--apply-reviewed", type=Path, help="Apply a reviewed refill report with 1 to target-size approved candidates.")
    refill.set_defaults(func=command_refill_next_batch)

    screen_refill = subparsers.add_parser("screen-refill-candidates", help="Auto-score and flag candidates in a refill report.")
    screen_refill.add_argument("report", type=Path)
    screen_refill.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    screen_refill.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    screen_refill.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    screen_refill.add_argument("--keywords")
    screen_refill.add_argument("--target-pay", type=int)
    screen_refill.add_argument("--pay-tolerance", type=int, default=15000)
    screen_refill.add_argument("--max-age-days", type=int, default=7)
    screen_refill.add_argument("--min-score", type=int, default=75)
    screen_refill.set_defaults(func=command_screen_refill_candidates)

    review_refill = subparsers.add_parser("review-refill-report", help="Export a refill report to a review CSV.")
    review_refill.add_argument("report", type=Path)
    review_refill.add_argument("--out", type=Path)
    review_refill.add_argument("--actionable-only", action="store_true", help="Only include candidate, review, and fetch_failed rows.")
    review_refill.set_defaults(func=command_review_refill_report)

    next_packet = subparsers.add_parser("prepare-next-packet", help="Prepare an application packet for the next queued job.")
    next_packet.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    next_packet.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    next_packet.add_argument("--llm-provider", choices=["codex", "auto", "azure", "local", "none"], default="codex")
    next_packet.add_argument("--model")
    next_packet.set_defaults(func=command_prepare_next_packet)

    handoff = subparsers.add_parser("generate-handoff", help="Write an agent/browser handoff JSON for one tracker row.")
    handoff.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    handoff.add_argument("--answers", type=Path, default=Path("profile/application_answers.json"))
    handoff.add_argument("--company", default="")
    handoff.add_argument("--role", default="")
    handoff.add_argument("--url", default="")
    handoff.add_argument("--out", type=Path)
    handoff.set_defaults(func=command_generate_handoff)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
