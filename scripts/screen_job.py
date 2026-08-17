#!/usr/bin/env python3
"""Fail-closed role-core screening before match scoring or resume tailoring."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DATA_SIGNALS = {
    "sql": r"\bsql\b",
    "python": r"\bpython\b",
    "data pipelines": r"\bdata pipeline(?:s)?\b",
    "data infrastructure": r"\bdata infrastructure\b",
    "data orchestration": r"\bdata orchestration\b|\borchestration models?\b",
    "data systems": r"\bdata systems?\b",
    "cdc pipelines": r"\bcdc pipelines?\b",
    "etl/elt": r"\b(?:etl|elt)\b",
    "data modeling": r"\bdata model(?:ing|s)?\b",
    "data quality": r"\bdata quality\b",
    "data warehouse": r"\bdata warehouse\b",
    "analytics engineering": r"\banalytics engineer(?:ing)?\b",
    "business intelligence": r"\bbusiness intelligence\b|\bbi reporting\b",
    "governed reporting": r"\b(?:governed|automated) (?:reporting|analytics)\b",
    "kpi/kri metrics": r"\b(?:kpi|kri)(?:s)?\b|\bmetric logic\b",
    "data governance": r"\bdata governance\b",
}

GRC_OPERATIONS_SIGNALS = {
    "audit readiness": r"\baudit readiness\b",
    "evidence collection": r"\bevidence collection\b|\bcollect(?:ing)? evidence\b",
    "policy ownership": r"\bpolicy (?:and )?procedure(?:s)?\b|\bpolicy ownership\b",
    "access reviews": r"\baccess review(?:s)?\b",
    "control attestations": r"\bcontrol attestation(?:s)?\b|\battestation(?:s)?\b",
    "poa&m tracking": r"\bpoa&m\b|\bplan of action and milestones\b",
    "framework mapping": r"\bframework mapping\b|\bmap(?:ping)? .*?(?:nist|soc ?2|iso)\b",
    "auditor coordination": r"\b(?:internal |external )?auditor(?:s)?\b|\baudit coordination\b",
    "compliance program operations": r"\bcompliance program\b|\bgrc program\b",
}

STAFFING_SIGNALS = {
    "staffing client": r"\bstaffing clients?\b",
    "agency recruiting": r"\bagency recruiting\b",
    "third-party consulting vendor": r"\bthird[- ]party (?:consulting|vendor)\b",
    "client representation": r"\b(?:our|the) client is (?:seeking|looking)\b",
    "unnamed client": r"\b(?:confidential|unnamed) client\b",
    "contract placement": r"\b(?:w-?2|c2c|corp[- ]to[- ]corp) contract\b|\bcontract[- ]to[- ]hire\b",
}

NO_SPONSORSHIP_SIGNALS = {
    "does not sponsor": r"\b(?:does not|do not|will not|cannot) sponsor\b",
    "no visa sponsorship": r"\bno visa sponsorship\b|\bwithout visa sponsorship\b",
    "not eligible for visa sponsorship": r"\bnot eligible for (?:visa )?sponsorship\b",
    "without need for sponsorship": (
        r"\bwithout\s+(?:the\s+)?need\s+for\s+"
        r"(?:current\s+or\s+future\s+)?(?:employer\s+|visa\s+)?sponsorship\b"
    ),
    "no h-1b support": r"\bno h[- ]?1b(?: transfer)?\b|\b(?:cannot|will not) support h[- ]?1b\b",
    "must not need sponsorship": r"\b(?:must|should) (?:not )?be authorized .*?without sponsorship\b",
}


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def matching_signals(text: str, patterns: dict[str, str]) -> list[str]:
    return [label for label, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def role_core_summary(data_signals: list[str], grc_signals: list[str]) -> str:
    if len(data_signals) >= 2 and len(grc_signals) < 3:
        return "Core work is evidenced as data or analytics engineering: " + ", ".join(data_signals[:4]) + "."
    if grc_signals:
        return "Core work is evidenced as GRC or compliance operations: " + ", ".join(grc_signals[:4]) + "."
    return "The posting does not provide enough evidence of data or analytics engineering work."


def evaluate_job(job_text: str, role: str = "") -> dict[str, Any]:
    """Return a deterministic, evidence-backed role-core eligibility decision.

    This intentionally does not score compensation or candidate skills. It gates
    whether the posting is technically in scope before later scoring stages.
    """

    text = normalized(f"{role}\n{job_text}")
    data_signals = matching_signals(text, DATA_SIGNALS)
    grc_signals = matching_signals(text, GRC_OPERATIONS_SIGNALS)
    staffing_signals = matching_signals(text, STAFFING_SIGNALS)
    sponsorship_signals = matching_signals(text, NO_SPONSORSHIP_SIGNALS)
    sponsorship_status = (
        "explicit_no_sponsorship" if sponsorship_signals else "not_mentioned_or_possible"
    )
    failures: list[str] = []

    if staffing_signals:
        failures.append("Contract staffing placement, unnamed client, or third-party vendor posting detected.")
    if sponsorship_signals:
        failures.append("Posting explicitly indicates no sponsorship or H-1B support.")
    if len(grc_signals) >= 3 and len(data_signals) < 3:
        failures.append("Role core is GRC or compliance operations rather than data or analytics engineering.")
    elif len(data_signals) < 2:
        failures.append("Role core lacks at least two concrete data or analytics engineering work signals.")

    eligible = not failures
    if eligible:
        decision_reason = "Passed role-core screen; continue to fact-overlap and compensation scoring."
    else:
        decision_reason = failures[0]

    return {
        "eligible": eligible,
        "decision_reason": decision_reason,
        "sponsorship_status": sponsorship_status,
        "role_core_summary": role_core_summary(data_signals, grc_signals),
        "hard_filter_failures": failures,
        "evidence": {
            "data_signals": data_signals,
            "grc_operations_signals": grc_signals,
            "staffing_signals": staffing_signals,
            "no_sponsorship_signals": sponsorship_signals,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen a job description before queueing or tailoring.")
    parser.add_argument("--job", type=Path, required=True, help="Local job-description text file.")
    parser.add_argument("--role", default="")
    parser.add_argument("--out", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.job.exists():
        raise SystemExit(f"Job description not found: {args.job}")
    result = evaluate_job(args.job.read_text(encoding="utf-8"), role=args.role)
    output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if result["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
