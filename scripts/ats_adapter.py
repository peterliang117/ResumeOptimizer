#!/usr/bin/env python3
"""Generate fact-gated form-fill plans for supported ATS providers.

This module intentionally plans browser work rather than bypassing login, CAPTCHA,
native upload, legal, or unknown-question gates.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

try:
    from job_store import DEFAULT_DB, connection, initialize, seed_ats_mappings
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, connection, initialize, seed_ats_mappings


def detect_ats(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "greenhouse" in host or "grnh.se" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs.com" in host or "wd1." in host or "wd5." in host:
        return "workday"
    if "linkedin.com" in host:
        return "linkedin"
    return "unknown"


def normalized_field(label: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())


def get_nested(payload: dict, key: str) -> object | None:
    value: object = payload
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def load_mappings(ats: str, db_path: Path) -> list[dict]:
    initialize(db_path)
    seed_ats_mappings(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            """SELECT normalized_field, answer_key, match_patterns_json, requires_exact_wording
               FROM ats_field_mappings WHERE ats = ? AND enabled = 1""",
            (ats,),
        ).fetchall()
    return [dict(row) for row in rows]


def prefill_allowed(answer_key: str, answers: dict) -> bool:
    policy = answers.get("answer_policy", {})
    if not isinstance(policy, dict):
        return False
    if answer_key.startswith("standard_fields."):
        return bool(policy.get("allow_prefill_standard_fields"))
    if answer_key.startswith("work_authorization."):
        return bool(policy.get("allow_prefill_exact_work_authorization"))
    if answer_key.startswith("self_identification."):
        return bool(policy.get("allow_prefill_sensitive_self_id"))
    return False


def plan_field(ats: str, label: str, answers: dict, db_path: Path) -> dict:
    clean = normalized_field(label)
    for mapping in load_mappings(ats, db_path):
        patterns = json.loads(mapping["match_patterns_json"])
        if clean not in {normalized_field(str(pattern)) for pattern in patterns}:
            continue
        value = get_nested(answers, mapping["answer_key"])
        if value in (None, ""):
            return {
                "label": label,
                "decision": "blocked_missing_fact",
                "answer_key": mapping["answer_key"],
                "reason": "Approved answer key is empty or unavailable.",
            }
        if not prefill_allowed(mapping["answer_key"], answers):
            return {
                "label": label,
                "decision": "blocked_policy_not_approved",
                "answer_key": mapping["answer_key"],
                "reason": "The local answer policy does not permit this prefill category.",
            }
        return {
            "label": label,
            "decision": "prefill_exact",
            "answer_key": mapping["answer_key"],
            "requires_exact_wording": bool(mapping["requires_exact_wording"]),
        }
    return {
        "label": label,
        "decision": "manual_review_required",
        "reason": "No exact approved field mapping exists.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a safe ATS form-fill plan.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--fields", type=Path, required=True, help="JSON array of visible form labels.")
    parser.add_argument("--answers", type=Path, default=Path("profile/application_answers.json"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fields = json.loads(args.fields.read_text(encoding="utf-8"))
    if not isinstance(fields, list) or not all(isinstance(item, str) for item in fields):
        raise SystemExit("--fields must contain a JSON array of form-label strings.")
    if not args.answers.exists():
        raise SystemExit(f"Answer file not found: {args.answers}")
    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    ats = detect_ats(args.url)
    plan = [plan_field(ats, label, answers, args.db) for label in fields]
    result = {
        "ats": ats,
        "url": args.url,
        "fields": plan,
        "safe_to_continue": all(item["decision"] == "prefill_exact" for item in plan),
        "blocked_actions": [
            "Do not bypass login, CAPTCHA, or browser security checks.",
            "Do not upload, submit, or accept legal/privacy attestations unless separately authorized and fact-covered.",
        ],
    }
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["safe_to_continue"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
