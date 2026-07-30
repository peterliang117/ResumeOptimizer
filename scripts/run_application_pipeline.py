#!/usr/bin/env python3
"""Run the safe job-application pipeline through tailored resume generation."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from resume_evidence import (
    build_evidence_map,
    classify_role_family,
    resolve_variant_resume,
    validate_edit_against_evidence,
)
from ai_resume_strategy import build_selection_report, validate_edit_against_strategy
from tailor import load_job_text, load_resume_paragraphs, validate_edit
from tracker import upsert_tracker


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return re.sub(r"_+", "_", slug) or "Unknown"


def candidate_name_slug(path: Path = Path("profile/application_answers.json")) -> str:
    try:
        payload = read_json(path)
        fields = payload.get("standard_fields", {})
        names = [
            str(fields.get(key, "")).strip()
            for key in ("first_name", "last_name")
            if str(fields.get(key, "")).strip()
        ]
    except (OSError, json.JSONDecodeError, TypeError):
        names = []
    return slugify("_".join(names)) if names else "Candidate"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_paths(
    company: str,
    role: str,
    *,
    candidate_slug: str | None = None,
) -> dict[str, Path | str]:
    company_slug = slugify(company)
    role_slug = slugify(role)
    packet_slug = f"{company_slug}_{role_slug}"
    owner_slug = slugify(candidate_slug) if candidate_slug else candidate_name_slug()
    resume_name = f"{owner_slug}_{company_slug}_{role_slug}_Resume.docx"
    packet_dir = Path("applications") / packet_slug
    return {
        "company_slug": company_slug,
        "role_slug": role_slug,
        "packet_slug": packet_slug,
        "resume_name": resume_name,
        "packet_dir": packet_dir,
        "job_description": packet_dir / "job_description.txt",
        "screening": packet_dir / "screening.json",
        "proposed_edits": packet_dir / "proposed_edits.json",
        "fit_analysis": packet_dir / "fit_analysis.json",
        "ai_selection_report": packet_dir / "ai_selection_report.json",
        "accepted_edits": packet_dir / "accepted_edits.json",
        "resume": packet_dir / resume_name,
        "render_dir": packet_dir / "render_check",
        "tailored_copy": Path("tailored_resumes") / resume_name,
    }


def create_fit_analysis(suggestions: dict) -> dict:
    return {
        key: value
        for key, value in suggestions.items()
        if key not in {"suggested_edits"}
    }


def auto_accept_safe_edits(
    suggestions: dict,
    resume: Path,
    profile: Path,
    selection_report: dict | None = None,
) -> dict:
    paragraph_by_id = {
        paragraph.paragraph_id: paragraph.text.strip()
        for paragraph in load_resume_paragraphs(resume)
        if paragraph.text.strip()
    }
    accepted = []
    rejected = []
    evidence = build_evidence_map(resume, profile)
    for edit in suggestions.get("suggested_edits", []):
        if not isinstance(edit, dict):
            rejected.append({"edit": edit, "reason": "Edit must be an object."})
            continue
        error = validate_edit(edit, paragraph_by_id)
        if error:
            rejected.append({"edit": edit, "reason": error})
            continue
        evidence_failures = validate_edit_against_evidence(edit, evidence)
        if evidence_failures:
            rejected.append({"edit": edit, "reason": " ".join(evidence_failures)})
            continue
        strategy_failures = validate_edit_against_strategy(edit, selection_report or {})
        if strategy_failures:
            rejected.append({"edit": edit, "reason": " ".join(strategy_failures)})
            continue
        accepted.append(edit)

    payload = dict(suggestions)
    payload["suggested_edits"] = accepted
    payload["_auto_accept"] = {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejected_edits": rejected,
    }
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and optionally generate a tailored resume packet.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--source", default="LinkedIn")
    job_input = parser.add_mutually_exclusive_group(required=True)
    job_input.add_argument("--job-url")
    job_input.add_argument("--job-file", type=Path)
    parser.add_argument(
        "--application-url",
        help="Canonical live posting or application URL to track when --job-file is used.",
    )
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument(
        "--role-family",
        choices=["auto", "data_engineering", "analytics_engineering", "cyber_risk_data", "business_intelligence"],
        default="auto",
        help="Select a local evidence-backed base resume variant before tailoring.",
    )
    parser.add_argument(
        "--variants",
        type=Path,
        default=Path("profile/resume_variants.private.json"),
        help="Local-only role-family variant manifest.",
    )
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--accepted-edits", type=Path)
    parser.add_argument(
        "--review-edits",
        action="store_true",
        help="Stop after writing proposed_edits.json instead of generating the tailored resume.",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--llm-provider",
        choices=["codex", "auto", "azure", "local", "none"],
        default="codex",
        help="Suggestion backend passed through to tailor.py. Defaults to local Codex review without an API call.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")

    paths = build_paths(args.company, args.role)
    packet_dir = paths["packet_dir"]
    assert isinstance(packet_dir, Path)
    packet_dir.mkdir(parents=True, exist_ok=True)

    job_text = load_job_text(
        job=str(args.job_file) if args.job_file else None,
        job_url=args.job_url,
    )
    job_description = paths["job_description"]
    assert isinstance(job_description, Path)
    job_description.write_text(job_text, encoding="utf-8")
    tracking_url = args.application_url or args.job_url or ""
    role_family = (
        classify_role_family(args.role, job_text)
        if args.role_family == "auto"
        else args.role_family
    )
    selected_resume = resolve_variant_resume(
        role_family,
        master_resume=args.resume,
        manifest_path=args.variants,
    )
    profile_facts = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    selection_report = build_selection_report(
        job_text,
        load_resume_paragraphs(selected_resume),
        profile_facts,
        resume_path=selected_resume,
    )
    ai_selection_report = paths["ai_selection_report"]
    assert isinstance(ai_selection_report, Path)
    write_json(ai_selection_report, selection_report)

    proposed_edits = paths["proposed_edits"]
    assert isinstance(proposed_edits, Path)
    command = [
        sys.executable,
        "scripts/tailor.py",
        "--resume",
        str(selected_resume),
        "--job",
        str(job_description),
        "--profile",
        str(args.profile),
        "--out",
        str(paths["resume"]),
        "--suggestions-out",
        str(proposed_edits),
        "--dry-run",
    ]
    if args.model:
        command.extend(["--model", args.model])
    if args.llm_provider:
        command.extend(["--llm-provider", args.llm_provider])
    run(command)

    suggestions = read_json(proposed_edits)
    fit_analysis = paths["fit_analysis"]
    assert isinstance(fit_analysis, Path)
    analysis = create_fit_analysis(suggestions)
    analysis["role_family"] = role_family
    analysis["base_resume"] = str(selected_resume)
    analysis["ai_selection_report"] = str(ai_selection_report)
    analysis["ai_coverage_score"] = selection_report["coverage_score"]
    analysis["parser_risk"] = selection_report["parser_audit"]["risk"]
    write_json(fit_analysis, analysis)

    upsert_tracker(
        args.tracker,
        {
            "company": args.company,
            "role": args.role,
            "source": args.source,
            "url": tracking_url,
            "status": "analyzed",
            "application_folder": str(packet_dir),
            "notes": "Review proposed_edits.json in chat before applying edits.",
        },
    )

    if args.review_edits:
        print(f"Prepared application packet: {packet_dir}")
        print(f"Review required before resume generation: {proposed_edits}")
        return 0

    accepted_edits = paths["accepted_edits"]
    assert isinstance(accepted_edits, Path)
    if args.accepted_edits:
        if args.accepted_edits.resolve() != accepted_edits.resolve():
            shutil.copy2(args.accepted_edits, accepted_edits)
    else:
        write_json(
            accepted_edits,
            auto_accept_safe_edits(suggestions, selected_resume, args.profile, selection_report),
        )

    resume_path = paths["resume"]
    assert isinstance(resume_path, Path)
    run(
        [
            sys.executable,
            "scripts/tailor.py",
            "--resume",
            str(selected_resume),
            "--job",
            str(job_description),
            "--profile",
            str(args.profile),
            "--accepted-edits",
            str(accepted_edits),
            "--out",
            str(resume_path),
        ]
    )
    render_dir = paths["render_dir"]
    assert isinstance(render_dir, Path)
    run([sys.executable, "scripts/check_one_page.py", "--docx", str(resume_path), "--outdir", str(render_dir)])

    tailored_copy = paths["tailored_copy"]
    assert isinstance(tailored_copy, Path)
    tailored_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resume_path, tailored_copy)

    upsert_tracker(
        args.tracker,
        {
            "company": args.company,
            "role": args.role,
            "source": args.source,
            "url": tracking_url,
            "status": "resume_ready",
            "resume_file": str(resume_path),
            "application_folder": str(packet_dir),
            "notes": "Tailored resume generated and one-page check passed. Safe edits were auto-accepted.",
        },
    )
    print(f"Tailored resume ready: {resume_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
