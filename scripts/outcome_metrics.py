#!/usr/bin/env python3
"""Export application-funnel metrics and bounded discovery-score calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from job_store import DEFAULT_DB, outcome_metrics
except ImportError:  # pragma: no cover - package invocation in tests
    from scripts.job_store import DEFAULT_DB, outcome_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Report outcome metrics from the local SQLite store.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--min-observations", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("outputs/outcome_metrics.json"))
    args = parser.parse_args()
    payload = outcome_metrics(path=args.db, min_observations=args.min_observations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
