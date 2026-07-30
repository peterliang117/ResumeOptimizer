#!/usr/bin/env python3
"""Process queued jobs through scoring and the resume-review gate."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import re

from job_queue import read_rows, write_rows
from match_score import score_job, split_keywords
from run_application_pipeline import build_paths, read_json, write_json
from screen_job import evaluate_job
from search_criteria import require_target_pay
from tailor import load_job_text, load_resume_paragraphs
from tracker import upsert_tracker


def run(command: list[str]) -> int:
    result = subprocess.run(command, check=False, text=True)
    return result.returncode


def stored_score(row: dict[str, str]) -> int:
    try:
        return int(row.get("match_score", "") or 0)
    except ValueError:
        return 0


def score_queue_row(
    row: dict[str, str],
    resume: Path,
    profile: Path,
    keywords: list[str],
    target_pay: int,
    pay_tolerance: int,
    max_age_days: int,
) -> tuple[str, dict]:
    source = row["url"]
    job_text = load_job_text(job_url=source) if re.match(r"^https?://", source) else load_job_text(job=source)
    resume_text = "\n".join(paragraph.text for paragraph in load_resume_paragraphs(resume))
    profile_text = profile.read_text(encoding="utf-8") if profile.exists() else ""
    score = score_job(
        job_text=job_text,
        resume_text=resume_text,
        profile_text=profile_text,
        keywords=keywords,
        target_pay=target_pay,
        pay_tolerance=pay_tolerance,
        max_age_days=max_age_days,
    )
    return job_text, score


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score queued jobs and prepare high-match application packets.")
    parser.add_argument("--queue", type=Path, default=Path("jobs/queue.csv"))
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--criteria", type=Path, default=Path("profile/search_criteria.md"))
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--min-score", type=int, default=75)
    parser.add_argument("--target-pay", type=int)
    parser.add_argument("--pay-tolerance", type=int, default=15000)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--keywords")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model")
    parser.add_argument(
        "--llm-provider",
        choices=["codex", "auto", "azure", "local", "none"],
        default="codex",
        help="Suggestion backend passed through to the application packet pipeline. Defaults to local Codex review without an API call.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")

    target_pay = require_target_pay(args.target_pay, args.criteria)
    rows = read_rows(args.queue)
    queued_rows = [
        row
        for row in rows
        if row.get("status", "queued") == "queued" and row.get("url")
    ]
    queued_rows.sort(
        key=lambda row: (
            {"high": 0, "medium": 1, "low": 2}.get(
                row.get("priority", "medium").lower(),
                1,
            ),
            -stored_score(row),
        )
    )
    if not queued_rows:
        print("No queued jobs with URLs.")
        return 1

    processed = 0
    for row in queued_rows[: args.limit]:
        company = row.get("company", "Unknown")
        role = row.get("role", "Data Engineer")
        print(f"Scoring: {company} - {role}")
        try:
            source = row["url"]
            job_text = (
                load_job_text(job_url=source)
                if re.match(r"^https?://", source)
                else load_job_text(job=source)
            )
        except SystemExit as exc:
            row["status"] = "needs_manual_review"
            row["notes"] = f"Could not fetch job description for role-core screening: {exc}"
            continue

        paths = build_paths(company, role)
        packet_dir = paths["packet_dir"]
        assert isinstance(packet_dir, Path)
        packet_dir.mkdir(parents=True, exist_ok=True)
        job_description = paths["job_description"]
        fit_analysis = paths["fit_analysis"]
        screening_path = paths["screening"]
        assert isinstance(job_description, Path)
        assert isinstance(fit_analysis, Path)
        assert isinstance(screening_path, Path)
        job_description.write_text(job_text, encoding="utf-8")
        screening = evaluate_job(job_text, role=role)
        write_json(screening_path, screening)
        write_json(fit_analysis, {"role_core_screen": screening})

        if not screening["eligible"]:
            row["status"] = "rejected_role_core"
            row["notes"] = f"Role-core screen failed: {screening['decision_reason']}"
            upsert_tracker(
                args.tracker,
                {
                    "company": company,
                    "role": role,
                    "source": row.get("source", "LinkedIn"),
                    "url": row.get("url", ""),
                    "status": "rejected",
                    "application_folder": str(packet_dir),
                    "notes": row["notes"],
                },
            )
            print(f"Skipped role-core mismatch: {screening['decision_reason']}")
            continue

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

        if score["score"] < args.min_score:
            row["status"] = "rejected_low_match"
            row["notes"] = f"Match score {score['score']} below threshold {args.min_score}."
            upsert_tracker(
                args.tracker,
                {
                    "company": company,
                    "role": role,
                    "source": row.get("source", "LinkedIn"),
                    "url": row.get("url", ""),
                    "status": "rejected",
                    "application_folder": str(packet_dir),
                    "notes": row["notes"],
                },
            )
            print(f"Skipped low match: {score['score']}")
            continue

        command = [
            sys.executable,
            "scripts/run_application_pipeline.py",
            "--company",
            company,
            "--role",
            role,
            "--source",
            row.get("source", "LinkedIn"),
            "--job-file",
            str(job_description),
            "--resume",
            str(args.resume),
            "--profile",
            str(args.profile),
            "--tracker",
            str(args.tracker),
        ]
        if args.model:
            command.extend(["--model", args.model])
        if args.llm_provider:
            command.extend(["--llm-provider", args.llm_provider])
        exit_code = run(command)
        if exit_code != 0:
            row["status"] = "analysis_failed"
            row["notes"] = f"Application pipeline failed with exit code {exit_code}."
            continue

        pipeline_fit = read_json(fit_analysis)
        pipeline_fit["match_score"] = score
        pipeline_fit["role_core_screen"] = screening
        write_json(fit_analysis, pipeline_fit)

        resume_path = paths["resume"]
        assert isinstance(resume_path, Path)
        if resume_path.exists():
            row["status"] = "resume_ready"
            row["notes"] = f"Match score {score['score']} met threshold {args.min_score}. Tailored resume generated."
        else:
            row["status"] = "analyzed"
            row["notes"] = f"Match score {score['score']} met threshold {args.min_score}. Review proposed edits."
        processed += 1

    write_rows(args.queue, rows)
    print(f"Prepared {processed} high-match application packet(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
