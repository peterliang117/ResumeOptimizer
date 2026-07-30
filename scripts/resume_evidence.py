#!/usr/bin/env python3
"""Build an evidence map and select truthful role-family resume variants."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from job_store import DEFAULT_DB, upsert_resume_variant
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, upsert_resume_variant


ROLE_FAMILIES = {
    "data_engineering": "Data Engineering",
    "analytics_engineering": "Analytics Engineering",
    "cyber_risk_data": "Cyber and Risk Data",
    "business_intelligence": "Business Intelligence",
}
UNSUPPORTED_TOOL_TERMS = {
    "airflow", "aws", "azure", "bigquery", "databricks", "dbt", "gcp",
    "kafka", "pyspark", "snowflake", "spark",
}
GENERIC_WORDS = {
    "across", "analytics", "automated", "business", "build", "data", "deliver",
    "engineering", "governance", "improve", "models", "platform", "quality",
    "reliable", "reporting", "stakeholders", "trusted", "using", "with",
}


def classify_role_family(role: str, job_text: str = "") -> str:
    text = f"{role} {job_text}".lower()
    if any(term in text for term in ("cyber", "security", "grc", "risk", "iam", "privacy")):
        return "cyber_risk_data"
    if any(term in text for term in ("analytics engineer", "semantic layer", "dbt", "metrics layer")):
        return "analytics_engineering"
    if any(term in text for term in ("business intelligence", "bi engineer", "reporting", "tableau")):
        return "business_intelligence"
    return "data_engineering"


def _profile_lines(profile: Path) -> list[str]:
    if not profile.exists():
        return []
    lines = []
    for line in profile.read_text(encoding="utf-8").splitlines():
        clean = line.strip().lstrip("-* ").strip()
        if clean and not clean.startswith("#"):
            lines.append(clean)
    return lines


def build_evidence_map(resume: Path, profile: Path) -> dict[str, Any]:
    try:
        from tailor import load_resume_paragraphs
    except ImportError:  # pragma: no cover - package invocation
        from scripts.tailor import load_resume_paragraphs

    resume_records = [
        {"id": paragraph.paragraph_id, "source": "resume", "text": paragraph.text}
        for paragraph in load_resume_paragraphs(resume)
    ]
    profile_records = [
        {"id": f"profile:{index}", "source": "profile", "text": line}
        for index, line in enumerate(_profile_lines(profile))
    ]
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "resume_file": str(resume),
        "profile_file": str(profile),
        "records": [*resume_records, *profile_records],
    }


def evidence_text(evidence: dict[str, Any]) -> str:
    records = evidence.get("records", [])
    return "\n".join(str(record.get("text", "")) for record in records if isinstance(record, dict)).lower()


def default_variants(master_resume: Path, evidence_path: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "variants": {
            family: {
                "name": name,
                "resume_file": str(master_resume),
                "evidence_path": str(evidence_path),
                "focus": focus,
            }
            for family, name, focus in [
                ("data_engineering", "Data Engineering", ["SQL", "Python", "ETL", "data quality", "CI/CD"]),
                ("analytics_engineering", "Analytics Engineering", ["data modeling", "metrics", "reporting", "data quality"]),
                ("cyber_risk_data", "Cyber and Risk Data", ["KRI", "governance", "security metrics", "reconciliation"]),
                ("business_intelligence", "Business Intelligence", ["dashboards", "KPI", "reporting", "stakeholders"]),
            ]
        },
    }


def resolve_variant_resume(
    role_family: str,
    *,
    master_resume: Path,
    manifest_path: Path = Path("profile/resume_variants.private.json"),
) -> Path:
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        variant = payload.get("variants", {}).get(role_family, {})
        candidate = Path(str(variant.get("resume_file", "")))
        if candidate.exists():
            return candidate
    return master_resume


def novel_terms(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", text)
        if token.lower() not in GENERIC_WORDS
    }


def validate_edit_against_evidence(edit: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    """Fail closed on new tools, metrics, and claims absent from verified evidence."""
    suggested = str(edit.get("suggested", ""))
    original = str(edit.get("original", ""))
    corpus = evidence_text(evidence)
    failures: list[str] = []
    if edit.get("truth_risk") != "low":
        failures.append("Only low truth-risk edits may be auto-applied.")
    for tool in UNSUPPORTED_TOOL_TERMS:
        if re.search(rf"\b{re.escape(tool)}\b", suggested, re.IGNORECASE) and tool not in corpus:
            failures.append(f"Unsupported tool claim: {tool}.")
    original_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", original))
    for number in set(re.findall(r"\b\d+(?:\.\d+)?%?\b", suggested)) - original_numbers:
        if number not in corpus:
            failures.append(f"Unsupported metric or date: {number}.")
    new_claim_terms = novel_terms(suggested) - novel_terms(original)
    evidence_terms = novel_terms(corpus)
    unsupported = sorted(term for term in new_claim_terms if term not in evidence_terms and term in UNSUPPORTED_TOOL_TERMS)
    if unsupported:
        failures.append("Unsupported newly introduced terms: " + ", ".join(unsupported) + ".")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage evidence-backed resume variants.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--resume", type=Path, default=Path("resumes/master.docx"))
    common.add_argument("--profile", type=Path, default=Path("profile/facts.md"))
    common.add_argument("--evidence", type=Path, default=Path("profile/evidence_map.private.json"))
    common.add_argument("--manifest", type=Path, default=Path("profile/resume_variants.private.json"))
    common.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers.add_parser("init", parents=[common], help="Create local evidence and role-family variant metadata.")
    validate = subparsers.add_parser("validate", parents=[common], help="Validate a JSON edit list against local evidence.")
    validate.add_argument("--edits", type=Path, required=True)
    validate.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.exists():
        raise SystemExit(f"Resume not found: {args.resume}")
    evidence = build_evidence_map(args.resume, args.profile)
    if args.command == "init":
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest = default_variants(args.resume, args.evidence)
        args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for family, variant in manifest["variants"].items():
            upsert_resume_variant(
                family,
                name=variant["name"],
                resume_file=variant["resume_file"],
                evidence_path=variant["evidence_path"],
                claims=variant["focus"],
                path=args.db,
            )
        print(f"Wrote evidence map: {args.evidence}")
        print(f"Wrote role-family variants: {args.manifest}")
        return 0
    payload = json.loads(args.edits.read_text(encoding="utf-8"))
    edits = payload.get("suggested_edits", payload) if isinstance(payload, dict) else payload
    if not isinstance(edits, list):
        raise SystemExit("Edits JSON must be a list or contain suggested_edits.")
    results = []
    for edit in edits:
        if not isinstance(edit, dict):
            results.append({"edit": edit, "failures": ["Edit must be an object."]})
            continue
        results.append({"edit": edit, "failures": validate_edit_against_evidence(edit, evidence)})
    result = {"valid": all(not item["failures"] for item in results), "results": results}
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
