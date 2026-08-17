#!/usr/bin/env python3
"""Score job descriptions against resume/profile evidence and compensation targets."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from search_criteria import require_target_pay
from tailor import load_job_text, load_resume_paragraphs
from ai_resume_strategy import phrase_present


DEFAULT_REQUIRED_KEYWORDS = [
    "data engineer",
    "python",
    "sql",
    "spark",
    "airflow",
    "etl",
    "elt",
    "data pipeline",
    "warehouse",
    "aws",
    "gcp",
    "azure",
    "dbt",
    "snowflake",
    "kafka",
]

TRANSFERABLE_TOOL_WORKFLOWS = {
    "dbt": ("sql", "etl", "elt", "data pipeline", "warehouse", "data quality", "data modeling"),
    "snowflake": ("sql", "etl", "elt", "data pipeline", "warehouse", "data quality", "data modeling"),
}


@dataclass(frozen=True)
class PayRange:
    low: int | None
    high: int | None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def has_confirmed_tool_evidence(evidence_text: str, tool: str) -> bool:
    """Require an explicit skill entry for tools mentioned in policy text."""
    escaped = re.escape(tool)
    explicit_patterns = (
        rf"(?im)^\s*[*-]\s*{escaped}\s*$",
        rf"(?i)\bprofessional\s+{escaped}\s+experience\b",
        rf"(?i)\bhands-on\s+{escaped}\s+experience\b",
    )
    return any(re.search(pattern, evidence_text) for pattern in explicit_patterns)


def split_keywords(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_REQUIRED_KEYWORDS
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def extract_pay_ranges(text: str) -> list[PayRange]:
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-").replace("\u00a0", " ")
    ranges: list[PayRange] = []
    range_spans: list[tuple[int, int]] = []
    hourly_amount = r"\$?\s*(\d{2,3})(?:\.\d+)?"
    hourly_unit = r"(?:/\s*hr|/\s*hour|per\s+hour|hourly|hr)"
    hourly_range_pattern = re.compile(
        rf"{hourly_amount}\s*{hourly_unit}?\s*(?:-|to|and)\s*{hourly_amount}\s*{hourly_unit}",
        re.IGNORECASE,
    )
    hourly_single_pattern = re.compile(rf"{hourly_amount}\s*{hourly_unit}", re.IGNORECASE)
    for match in hourly_range_pattern.finditer(text):
        low = round(float(match.group(1)) * 2080)
        high = round(float(match.group(2)) * 2080)
        ranges.append(PayRange(low=low, high=high))
        range_spans.append(match.span())

    amount = r"\$?\s*((?:\d{1,3}(?:,\d{3})+)|(?:\d{5,6})|(?:\d{2,3}(?:\.\d+)?\s*k?))"
    range_pattern = re.compile(rf"{amount}\s*(?:-|to|and|/)\s*{amount}", re.IGNORECASE)
    single_pattern = re.compile(rf"{amount}\s*(?:per\s+year|annually|annual|base|salary|yr|year)", re.IGNORECASE)

    def annual_value(raw: str) -> int:
        cleaned = raw.lower().replace(",", "").replace(" ", "")
        if cleaned.endswith("k"):
            return round(float(cleaned[:-1]) * 1000)
        value = float(cleaned)
        return round(value * 1000) if value < 1000 else round(value)

    for match in range_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        low = annual_value(match.group(1))
        high = annual_value(match.group(2))
        if low < 50000 or high < 50000:
            continue
        ranges.append(PayRange(low=low, high=high))
        range_spans.append(match.span())

    for match in single_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        low = annual_value(match.group(1))
        if low < 50000:
            continue
        ranges.append(PayRange(low=low, high=None))

    for match in hourly_single_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        low = round(float(match.group(1)) * 2080)
        ranges.append(PayRange(low=low, high=None))
    return ranges


def pay_score(ranges: list[PayRange], target: int, tolerance: int) -> tuple[int, str]:
    if not ranges:
        return 0, "No base-pay range detected."

    for pay_range in ranges:
        low = pay_range.low or 0
        high = pay_range.high or low
        if low - tolerance <= target <= high + tolerance:
            return 100, f"Detected pay range ${low:,}-${high:,} overlaps target ${target:,}."
        if high >= target - tolerance:
            return 75, f"Detected pay range ${low:,}-${high:,} is near target ${target:,}."
    best = max(ranges, key=lambda item: item.high or item.low or 0)
    best_high = best.high or best.low or 0
    return 25 if best_high >= target * 0.75 else 0, f"Best detected pay range tops at ${best_high:,}."


def keyword_score(
    job_text: str,
    evidence_text: str,
    keywords: list[str],
) -> tuple[int, list[str], list[str], list[str]]:
    job = normalize(job_text)
    evidence = normalize(evidence_text)
    relevant = [keyword for keyword in keywords if phrase_present(job, keyword)]
    if not relevant:
        return 0, [], keywords, []
    matched = [
        keyword
        for keyword in relevant
        if phrase_present(evidence, keyword)
        and (
            keyword not in TRANSFERABLE_TOOL_WORKFLOWS
            or has_confirmed_tool_evidence(evidence_text, keyword)
        )
    ]
    transferable = [
        keyword
        for keyword in relevant
        if keyword not in matched
        and keyword in TRANSFERABLE_TOOL_WORKFLOWS
        and any(phrase_present(evidence, workflow) for workflow in TRANSFERABLE_TOOL_WORKFLOWS[keyword])
    ]
    missing = [keyword for keyword in relevant if keyword not in matched and keyword not in transferable]
    weighted_matches = len(matched) + (0.65 * len(transferable))
    return round(100 * weighted_matches / len(relevant)), matched, missing, transferable


def seniority_score(job_text: str) -> tuple[int, str]:
    text = normalize(job_text)
    if re.search(r"\b(principal|staff|lead|manager|director)\b", text):
        return 60, "Role may be above target seniority."
    if re.search(r"\b(senior|sr\.?)\b", text):
        return 100, "Senior-level wording detected."
    if re.search(r"\b(junior|entry[- ]level|intern)\b", text):
        return 20, "Junior or entry-level wording detected."
    return 75, "No strong seniority mismatch detected."


def recency_score(job_text: str, max_age_days: int) -> tuple[int, str]:
    text = normalize(job_text)
    match = re.search(r"posted\s+(\d+)\s+(hour|hours|day|days|week|weeks)\s+ago", text)
    if not match:
        if "just posted" in text or "posted today" in text:
            return 100, "Recent-post wording detected."
        return 50, "No post age detected in job text."

    amount = int(match.group(1))
    unit = match.group(2)
    days = amount / 24 if unit.startswith("hour") else amount * 7 if unit.startswith("week") else amount
    if days <= max_age_days:
        return 100, f"Post age appears within {max_age_days} day(s)."
    return 0, f"Post age appears older than {max_age_days} day(s)."


def score_job(
    job_text: str,
    resume_text: str,
    profile_text: str,
    keywords: list[str],
    target_pay: int,
    pay_tolerance: int,
    max_age_days: int,
) -> dict[str, Any]:
    evidence_text = f"{resume_text}\n{profile_text}"
    skill_score, matched, missing, transferable = keyword_score(job_text, evidence_text, keywords)
    pay_ranges = extract_pay_ranges(job_text)
    pay_points, pay_reason = pay_score(pay_ranges, target_pay, pay_tolerance)
    seniority_points, seniority_reason = seniority_score(job_text)
    recency_points, recency_reason = recency_score(job_text, max_age_days)
    total = round((skill_score * 0.55) + (pay_points * 0.25) + (seniority_points * 0.10) + (recency_points * 0.10))
    return {
        "score": total,
        "skill_score": skill_score,
        "pay_score": pay_points,
        "seniority_score": seniority_points,
        "recency_score": recency_points,
        "matched_keywords": matched,
        "transferable_keywords": transferable,
        "missing_keywords": missing,
        "pay_ranges": [{"low": item.low, "high": item.high} for item in pay_ranges],
        "reasons": [pay_reason, seniority_reason, recency_reason],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score a job against resume/profile evidence.")
    job_input = parser.add_mutually_exclusive_group(required=True)
    job_input.add_argument("--job", help="Job text file or raw pasted job text.")
    job_input.add_argument("--job-url", help="URL of the job post to fetch and score.")
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--keywords", help="Comma-separated role keywords. Defaults to Data Engineer terms.")
    parser.add_argument("--target-pay", type=int)
    parser.add_argument("--pay-tolerance", type=int, default=15000)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")

    job_text = load_job_text(job=args.job, job_url=args.job_url)
    resume_text = "\n".join(paragraph.text for paragraph in load_resume_paragraphs(args.resume))
    profile_text = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    target_pay = require_target_pay(args.target_pay, args.criteria)
    result = score_job(
        job_text=job_text,
        resume_text=resume_text,
        profile_text=profile_text,
        keywords=split_keywords(args.keywords),
        target_pay=target_pay,
        pay_tolerance=args.pay_tolerance,
        max_age_days=args.max_age_days,
    )
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
