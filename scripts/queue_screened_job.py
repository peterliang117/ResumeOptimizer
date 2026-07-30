#!/usr/bin/env python3
"""Screen, score, and serially queue one verified job candidate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from job_queue import read_rows, write_rows
from match_score import score_job, split_keywords
from screen_job import evaluate_job
from search_criteria import require_target_pay
from tailor import load_resume_paragraphs


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return re.sub(r"_+", "_", value) or "candidate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue a job only after deterministic role-core and match scoring gates pass."
    )
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--source", default="LinkedIn")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--priority", default="medium")
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument("--reviews-dir", type=Path, default=Path("jobs/candidate_reviews"))
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--target-pay", type=int)
    parser.add_argument("--pay-tolerance", type=int, default=15000)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--keywords")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.job.exists():
        raise SystemExit(f"Job description not found: {args.job}")
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")

    job_text = args.job.read_text(encoding="utf-8")
    screening = evaluate_job(job_text, role=args.role)
    review_path = args.reviews_dir / f"{slugify(args.company)}_{slugify(args.role)}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(screening, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if not screening["eligible"]:
        print(f"Not queued: {screening['decision_reason']}")
        print(f"Screening record: {review_path}")
        return 2

    target_pay = require_target_pay(args.target_pay, args.criteria)
    resume_text = "\n".join(
        paragraph.text for paragraph in load_resume_paragraphs(args.resume)
    )
    profile_text = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    score = score_job(
        job_text=job_text,
        resume_text=resume_text,
        profile_text=profile_text,
        keywords=split_keywords(args.keywords),
        target_pay=target_pay,
        pay_tolerance=args.pay_tolerance,
        max_age_days=args.max_age_days,
    )
    screening["match_score"] = score
    review_path.write_text(json.dumps(screening, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if score["score"] < args.min_score:
        print(f"Not queued: match score {score['score']} below threshold {args.min_score}.")
        print(f"Screening record: {review_path}")
        return 2

    rows = read_rows(args.queue)
    normalized_url = args.url.rstrip("/")
    if any(row.get("url", "").rstrip("/") == normalized_url for row in rows):
        raise SystemExit(f"Queue URL already exists: {args.url}")
    rows.append(
        {
            "company": args.company,
            "role": args.role,
            "source": args.source,
            "url": args.url,
            "status": "queued",
            "priority": args.priority,
            "batch_id": args.batch_id,
            "match_score": str(score["score"]),
            "notes": f"Role-core screen passed. Screening record: {review_path}",
        }
    )
    write_rows(args.queue, rows)
    print(f"Queued screened job: {args.company} - {args.role} (score {score['score']})")
    print(f"Screening record: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
