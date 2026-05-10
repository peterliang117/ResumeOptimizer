#!/usr/bin/env python3
"""Generate truthful resume tailoring suggestions and optionally apply accepted edits."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "job_summary": {"type": "string"},
        "must_have_skills": {"type": "array", "items": {"type": "string"}},
        "nice_to_have_skills": {"type": "array", "items": {"type": "string"}},
        "matched_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "requirement": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["requirement", "evidence", "confidence"],
            },
        },
        "suggested_edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "paragraph_id": {"type": "string"},
                    "original": {"type": "string"},
                    "suggested": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_source": {
                        "type": "string",
                        "enum": ["resume", "profile", "resume_and_profile"],
                    },
                    "truth_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": [
                    "paragraph_id",
                    "original",
                    "suggested",
                    "reason",
                    "evidence_source",
                    "truth_risk",
                ],
            },
        },
        "confirmation_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "requirement": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["requirement", "question"],
            },
        },
    },
    "required": [
        "job_summary",
        "must_have_skills",
        "nice_to_have_skills",
        "matched_evidence",
        "suggested_edits",
        "confirmation_questions",
    ],
}


@dataclass(frozen=True)
class ResumeParagraph:
    paragraph_id: str
    text: str


def iter_document_paragraphs(document: Any) -> list[tuple[str, Any]]:
    paragraphs: list[tuple[str, Any]] = []
    for index, paragraph in enumerate(document.paragraphs):
        paragraphs.append((f"body:p{index}", paragraph))

    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                for paragraph_index, paragraph in enumerate(cell.paragraphs):
                    paragraphs.append(
                        (
                            f"table:{table_index}:r{row_index}:c{cell_index}:p{paragraph_index}",
                            paragraph,
                        )
                    )
    return paragraphs


def load_job_text(job: str | None = None, job_url: str | None = None) -> str:
    if job_url:
        return load_url_text(job_url)
    if not job:
        raise SystemExit("Provide either --job or --job-url.")
    if re.match(r"^https?://", job):
        return load_url_text(job)

    path = Path(job)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return job


def load_url_text(url: str) -> str:
    if not re.match(r"^https?://", url):
        raise SystemExit("--job-url must start with http:// or https://")

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit("Install requests and beautifulsoup4 to load job URLs.") from exc

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(
            f"Could not fetch job URL: {exc}\n"
            "If the job board blocks automated access, paste the description into jobs/job.txt "
            "and use --job jobs/job.txt."
        ) from exc

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(cleaned) < 200:
        raise SystemExit(
            "Fetched page contained very little readable text. "
            "The job board may require JavaScript or login; paste the description into jobs/job.txt."
        )
    return cleaned


def load_resume_paragraphs(path: Path) -> list[ResumeParagraph]:
    try:
        from docx import Document
    except ImportError as exc:
        raise SystemExit("Install python-docx to read DOCX resumes.") from exc

    document = Document(path)
    paragraphs: list[ResumeParagraph] = []
    for paragraph_id, paragraph in iter_document_paragraphs(document):
        text = paragraph.text.strip()
        if text:
            paragraphs.append(ResumeParagraph(paragraph_id=paragraph_id, text=text))
    return paragraphs


def build_prompt(
    resume_paragraphs: list[ResumeParagraph],
    job_text: str,
    profile_facts: str,
) -> str:
    resume_json = [{"paragraph_id": p.paragraph_id, "text": p.text} for p in resume_paragraphs]
    return f"""
You are helping tailor a one-page resume to a job description.

Hard rules:
- Do not invent or exaggerate facts.
- Only suggest changes supported by the resume or profile facts.
- Prefer compact edits that keep the resume one page.
- Do not add unsupported tools, metrics, employers, dates, degrees, or responsibilities.
- If a job requirement is not supported, ask a confirmation question instead of adding it.
- Suggest edits to existing paragraphs only, using paragraph_id.

Resume paragraphs:
{json.dumps(resume_json, indent=2)}

Profile facts allowed as evidence:
{profile_facts}

Job description:
{job_text}
""".strip()


def analyze_with_openai(prompt: str, model: str) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Install requests or run without API by creating accepted edits manually.") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required for OpenAI analysis.")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": "Return only structured JSON. Be conservative and truth-preserving.",
            },
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "resume_tailoring_suggestions",
                "schema": SUGGESTION_SCHEMA,
                "strict": True,
            }
        },
    }
    response = requests.post(
        f"{base_url}/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    if response.status_code >= 400:
        raise SystemExit(f"OpenAI API error {response.status_code}: {response.text}")
    return json.loads(extract_output_text(response.json()))


def extract_output_text(response_payload: dict[str, Any]) -> str:
    if isinstance(response_payload.get("output_text"), str):
        return response_payload["output_text"]

    chunks: list[str] = []
    for item in response_payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if not chunks:
        raise SystemExit("OpenAI response did not include output text.")
    return "".join(chunks)


def fallback_analysis(resume_paragraphs: list[ResumeParagraph], job_text: str) -> dict[str, Any]:
    tokens = sorted(
        {
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z+#.\-]{2,}", job_text)
            if token.lower()
            not in {
                "and",
                "the",
                "for",
                "with",
                "you",
                "our",
                "are",
                "this",
                "that",
                "will",
                "from",
                "your",
                "have",
            }
        }
    )
    resume_text = "\n".join(p.text for p in resume_paragraphs).lower()
    matched = [token for token in tokens if token in resume_text][:30]
    missing = [token for token in tokens if token not in resume_text][:20]
    return {
        "job_summary": "Fallback keyword scan. Set OPENAI_API_KEY for rewrite suggestions.",
        "must_have_skills": matched,
        "nice_to_have_skills": [],
        "matched_evidence": [
            {"requirement": token, "evidence": "Keyword appears in resume.", "confidence": "medium"}
            for token in matched
        ],
        "suggested_edits": [],
        "confirmation_questions": [
            {"requirement": token, "question": f"Can you truthfully claim experience with {token}?"}
            for token in missing
        ],
    }


def validate_edit(edit: dict[str, Any], paragraph_by_id: dict[str, str]) -> str | None:
    paragraph_id = edit.get("paragraph_id")
    original = edit.get("original", "")
    suggested = edit.get("suggested", "")
    if paragraph_id not in paragraph_by_id:
        return f"Unknown paragraph_id: {paragraph_id}"
    if paragraph_by_id[paragraph_id].strip() != original.strip():
        return f"Original text mismatch for {paragraph_id}"
    if not suggested.strip():
        return f"Empty suggestion for {paragraph_id}"
    if edit.get("truth_risk") == "high":
        return f"Refusing high truth-risk edit for {paragraph_id}"
    return None


def apply_edits(source_docx: Path, output_docx: Path, accepted_edits: list[dict[str, Any]]) -> None:
    try:
        from docx import Document
    except ImportError as exc:
        raise SystemExit("Install python-docx to edit DOCX resumes.") from exc

    document = Document(source_docx)
    paragraph_text = {
        paragraph_id: paragraph.text.strip()
        for paragraph_id, paragraph in iter_document_paragraphs(document)
        if paragraph.text.strip()
    }

    for edit in accepted_edits:
        error = validate_edit(edit, paragraph_text)
        if error:
            raise SystemExit(error)

    edits_by_id = {edit["paragraph_id"]: edit for edit in accepted_edits}
    for paragraph_id, paragraph in iter_document_paragraphs(document):
        if paragraph_id in edits_by_id:
            replace_paragraph_text_keep_style(paragraph, edits_by_id[paragraph_id]["suggested"])

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_docx)


def replace_paragraph_text_keep_style(paragraph: Any, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tailor a DOCX resume conservatively.")
    parser.add_argument("--resume", required=True, type=Path, help="Source DOCX resume.")
    job_input = parser.add_mutually_exclusive_group(required=True)
    job_input.add_argument("--job", help="Job text file or raw pasted job text.")
    job_input.add_argument("--job-url", help="URL of the job post to fetch and analyze.")
    parser.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    parser.add_argument("--out", required=True, type=Path, help="Output DOCX path.")
    parser.add_argument("--suggestions-out", type=Path, default=Path("outputs/suggestions.json"))
    parser.add_argument("--accepted-edits", type=Path, help="JSON file with accepted suggested_edits.")
    parser.add_argument("--dry-run", action="store_true", help="Only write suggestions JSON.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.1"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")

    job_text = load_job_text(job=args.job, job_url=args.job_url)
    profile_facts = args.profile.read_text(encoding="utf-8") if args.profile.exists() else ""
    resume_paragraphs = load_resume_paragraphs(args.resume)

    if args.dry_run or not args.accepted_edits:
        if os.getenv("OPENAI_API_KEY"):
            suggestions = analyze_with_openai(
                build_prompt(resume_paragraphs, job_text, profile_facts),
                args.model,
            )
        else:
            suggestions = fallback_analysis(resume_paragraphs, job_text)
        write_json(args.suggestions_out, suggestions)
        print(f"Wrote suggestions: {args.suggestions_out}")
        if args.dry_run:
            return 0

    if args.accepted_edits:
        accepted_payload = json.loads(args.accepted_edits.read_text(encoding="utf-8"))
        accepted_edits = (
            accepted_payload.get("suggested_edits", accepted_payload)
            if isinstance(accepted_payload, dict)
            else accepted_payload
        )
        if not isinstance(accepted_edits, list):
            raise SystemExit("Accepted edits JSON must be a list or an object with suggested_edits.")
        apply_edits(args.resume, args.out, accepted_edits)
        print(f"Wrote tailored resume: {args.out}")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.resume, args.out)
        print(f"No accepted edits provided; copied original resume to: {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
