#!/usr/bin/env python3
"""Run the safe job-application pipeline through the resume review gate."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tailor import load_job_text
from tracker import upsert_tracker


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return re.sub(r"_+", "_", slug) or "Unknown"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_paths(company: str, role: str) -> dict[str, Path | str]:
    company_slug = slugify(company)
    role_slug = slugify(role)
    packet_slug = f"{company_slug}_{role_slug}"
    resume_name = f"Zicong_Liang_{company_slug}_{role_slug}_Resume.docx"
    packet_dir = Path("applications") / packet_slug
    return {
        "company_slug": company_slug,
        "role_slug": role_slug,
        "packet_slug": packet_slug,
        "resume_name": resume_name,
        "packet_dir": packet_dir,
        "job_description": packet_dir / "job_description.txt",
        "proposed_edits": packet_dir / "proposed_edits.json",
        "fit_analysis": packet_dir / "fit_analysis.json",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and optionally generate a tailored resume packet.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--source", default="LinkedIn")
    job_input = parser.add_mutually_exclusive_group(required=True)
    job_input.add_argument("--job-url")
    job_input.add_argument("--job-file", type=Path)
    parser.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--accepted-edits", type=Path)
    parser.add_argument("--model", default=None)
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

    proposed_edits = paths["proposed_edits"]
    assert isinstance(proposed_edits, Path)
    command = [
        sys.executable,
        "scripts/tailor.py",
        "--resume",
        str(args.resume),
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
    run(command)

    suggestions = read_json(proposed_edits)
    fit_analysis = paths["fit_analysis"]
    assert isinstance(fit_analysis, Path)
    write_json(fit_analysis, create_fit_analysis(suggestions))

    upsert_tracker(
        args.tracker,
        {
            "company": args.company,
            "role": args.role,
            "source": args.source,
            "url": args.job_url or "",
            "status": "analyzed",
            "application_folder": str(packet_dir),
            "notes": "Review proposed_edits.json in chat before applying edits.",
        },
    )

    if not args.accepted_edits:
        print(f"Prepared application packet: {packet_dir}")
        print(f"Review required before resume generation: {proposed_edits}")
        return 0

    accepted_edits = paths["accepted_edits"]
    assert isinstance(accepted_edits, Path)
    shutil.copy2(args.accepted_edits, accepted_edits)

    resume_path = paths["resume"]
    assert isinstance(resume_path, Path)
    run(
        [
            sys.executable,
            "scripts/tailor.py",
            "--resume",
            str(args.resume),
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
            "url": args.job_url or "",
            "status": "resume_ready",
            "resume_file": str(resume_path),
            "application_folder": str(packet_dir),
            "notes": "Tailored resume generated and one-page check passed.",
        },
    )
    print(f"Tailored resume ready: {resume_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
