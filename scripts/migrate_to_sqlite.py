#!/usr/bin/env python3
"""Initialize the local SQLite store from legacy queue and tracker CSV files."""

from __future__ import annotations

import argparse
from pathlib import Path

from job_store import (
    DEFAULT_DB,
    DEFAULT_QUEUE,
    DEFAULT_TRACKER,
    export_legacy_csv,
    import_legacy_csv,
    initialize,
    seed_ats_mappings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local CSV workflow state into SQLite.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--tracker", type=Path, default=DEFAULT_TRACKER)
    parser.add_argument(
        "--import-csv",
        action="store_true",
        help="Import current CSV values into the database before exporting normalized copies.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Write compatibility CSV exports from the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    initialize(args.db)
    if args.import_csv:
        counts = import_legacy_csv(queue_path=args.queue, tracker_path=args.tracker, path=args.db)
        print(f"Imported {counts['jobs']} queue rows and {counts['applications']} tracker rows.")
    mappings = seed_ats_mappings(args.db)
    print(f"SQLite store ready: {args.db}")
    print(f"Seeded {mappings} ATS field mappings.")
    if args.export_csv:
        export_legacy_csv(queue_path=args.queue, tracker_path=args.tracker, path=args.db)
        print("Exported compatibility CSV files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
