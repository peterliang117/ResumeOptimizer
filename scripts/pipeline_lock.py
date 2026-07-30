#!/usr/bin/env python3
"""Prevent overlapping browser-owning full-pipeline runs."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "tmp" / "full_pipeline.lock"


def acquire(path: Path = DEFAULT_LOCK, *, stale_minutes: int = 180) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age <= stale_minutes * 60:
            return {"acquired": False, "reason": "active_run", "lock": str(path)}
        path.unlink()
    token = secrets.token_hex(12)
    payload = {
        "token": token,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return {"acquired": True, "token": token, "lock": str(path)}


def release(token: str, path: Path = DEFAULT_LOCK) -> dict:
    if not path.exists():
        return {"released": False, "reason": "missing_lock"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not secrets.compare_digest(str(payload.get("token") or ""), token):
        raise PermissionError("Lock token does not own the active pipeline run.")
    path.unlink()
    return {"released": True, "lock": str(path)}


def status(path: Path = DEFAULT_LOCK) -> dict:
    if not path.exists():
        return {"active": False, "lock": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("token", None)
    return {"active": True, "lock": str(path), **payload}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the full-pipeline run lock.")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    sub = parser.add_subparsers(dest="command", required=True)
    claim = sub.add_parser("acquire")
    claim.add_argument("--stale-minutes", type=int, default=180)
    free = sub.add_parser("release")
    free.add_argument("--token", required=True)
    sub.add_parser("status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "acquire":
        result = acquire(args.lock, stale_minutes=args.stale_minutes)
    elif args.command == "release":
        result = release(args.token, args.lock)
    else:
        result = status(args.lock)
    print(json.dumps(result, indent=2))
    return 0 if result.get("acquired", result.get("released", True)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
