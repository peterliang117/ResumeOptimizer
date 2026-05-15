#!/usr/bin/env python3
"""Load private application-answer facts for browser-assisted filling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = Path("profile/application_answers.json")


def load_answers(path: Path = DEFAULT_PROFILE) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"Application answers file not found: {path}\n"
            "Create it from profile/application_answers.example.json and keep it private."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def get_nested(payload: dict[str, Any], dotted_key: str) -> Any:
    value: Any = payload
    for key in dotted_key.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def classify_fill_action(field_type: str, field_key: str, answers: dict[str, Any]) -> dict[str, str]:
    policy = answers.get("answer_policy", {})
    value = get_nested(answers, field_key)
    if value is None or value == "":
        return {"action": "ask", "reason": "No explicit private fact found."}

    if field_type == "standard" and policy.get("allow_prefill_standard_fields", True):
        return {"action": "prefill", "value": str(value), "reason": "Standard field from private profile."}

    if field_type == "self_id" and policy.get("allow_prefill_sensitive_self_id", False):
        return {"action": "prefill", "value": str(value), "reason": "Sensitive self-ID explicitly provided."}

    if field_type == "work_authorization" and policy.get("allow_prefill_exact_work_authorization", True):
        return {"action": "prefill_if_exact", "value": str(value), "reason": "Use only if form wording exactly matches."}

    if field_type == "legal":
        return {"action": "review_required", "value": str(value), "reason": "Legal attestation requires explicit review."}

    if field_type == "custom_essay" and policy.get("allow_draft_custom_essays", True):
        return {"action": "draft_only", "reason": "Draft from private facts and job context; require review."}

    return {"action": "ask", "reason": "Policy does not allow automatic fill."}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect private application-answer facts.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--field-type", choices=["standard", "self_id", "work_authorization", "legal", "custom_essay"])
    parser.add_argument("--field-key", help="Dotted key, for example standard_fields.email.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    answers = load_answers(args.profile)
    if args.field_type and args.field_key:
        print(json.dumps(classify_fill_action(args.field_type, args.field_key, answers), indent=2))
    else:
        safe_summary = {
            "has_standard_fields": bool(answers.get("standard_fields")),
            "has_work_authorization": bool(answers.get("work_authorization")),
            "has_self_identification": bool(answers.get("self_identification")),
            "legal_attestations": answers.get("legal_attestations", {}),
            "answer_policy": answers.get("answer_policy", {}),
        }
        print(json.dumps(safe_summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
