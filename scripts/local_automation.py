#!/usr/bin/env python3
"""Run the safe, Windows-local maintenance loop for Resume Optimizer.

This coordinator intentionally has no browser, ATS form, or submit action. It
keeps local SQLite state healthy, consumes metadata-only Outlook events, exports
compatibility CSVs, refreshes the dashboard, and can produce an ATS discovery
report for manual review. Packet preparation is opt-in and never submits.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "profile" / "local_automation.json"
DEFAULT_LOG = ROOT / "logs" / "local_automation.log"
DEFAULT_LOCK = ROOT / "tmp" / "local_automation.lock"


@dataclass(frozen=True)
class AutomationConfig:
    interval_minutes: int = 30
    outlook_interval_minutes: int = 240
    discovery_interval_minutes: int = 120
    pipeline_interval_minutes: int = 30
    queue_capacity: int = 10
    low_watermark: int = 3
    enable_ats_discovery_report: bool = False
    prepare_packets: bool = False
    packet_limit: int = 3
    min_score: int = 75
    llm_provider: str = "codex"
    mailbox_events_path: str = "tmp/mailbox_events.json"
    max_safe_retries: int = 2
    retry_delay_seconds: int = 15
    stale_lock_minutes: int = 480


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}.")


def _coerce_config(values: dict[str, object]) -> AutomationConfig:
    allowed = set(AutomationConfig.__dataclass_fields__)
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError("Unknown local automation setting(s): " + ", ".join(unknown))
    merged = asdict(AutomationConfig())
    merged.update(values)
    for key in {"enable_ats_discovery_report", "prepare_packets"}:
        merged[key] = _as_bool(merged[key])
    for key in {
        "interval_minutes", "outlook_interval_minutes", "discovery_interval_minutes",
        "pipeline_interval_minutes", "queue_capacity", "low_watermark", "packet_limit",
        "min_score", "max_safe_retries", "retry_delay_seconds", "stale_lock_minutes",
    }:
        merged[key] = int(merged[key])
    config = AutomationConfig(**merged)
    if min(
        config.interval_minutes,
        config.outlook_interval_minutes,
        config.discovery_interval_minutes,
        config.pipeline_interval_minutes,
    ) <= 0:
        raise ValueError("automation intervals must be positive")
    if config.queue_capacity <= 0 or not 0 <= config.low_watermark <= config.queue_capacity:
        raise ValueError("low_watermark must be between zero and queue_capacity")
    if config.packet_limit <= 0 or not 0 <= config.min_score <= 100:
        raise ValueError("packet_limit must be positive and min_score must be between 0 and 100")
    if config.max_safe_retries < 0 or config.retry_delay_seconds < 0 or config.stale_lock_minutes <= 0:
        raise ValueError("retry values must be non-negative and stale_lock_minutes must be positive")
    if config.llm_provider not in {"codex", "auto", "azure", "local", "none"}:
        raise ValueError("llm_provider must be codex, auto, azure, local, or none")
    return config


def load_config(path: Path) -> AutomationConfig:
    values: dict[str, object] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{path} must contain one JSON object.")
        values.update(parsed)
    for key in AutomationConfig.__dataclass_fields__:
        env_key = "RESUME_AUTOMATION_" + key.upper()
        if env_key in os.environ:
            values[key] = os.environ[env_key]
    return _coerce_config(values)


def repo_path(value: str) -> Path:
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"Configured path must stay inside the repository: {value}") from exc
    return candidate


def configure_logging() -> logging.Logger:
    DEFAULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("resume_optimizer.local_automation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(DEFAULT_LOG, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _lock_is_stale(path: Path, stale_minutes: int) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_seconds > stale_minutes * 60


@contextmanager
def run_lock(path: Path, stale_minutes: int) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _lock_is_stale(path, stale_minutes):
        path.unlink()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Another local automation run owns {path}.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, handle)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def command_result(
    name: str,
    arguments: list[str],
    *,
    logger: logging.Logger,
    retries: int = 0,
    retry_delay_seconds: int = 0,
) -> tuple[bool, str]:
    command = [sys.executable, *arguments]
    for attempt in range(retries + 1):
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            logger.info("step=%s attempt=%s status=ok output=%s", name, attempt + 1, output[-2000:])
            return True, output
        logger.warning("step=%s attempt=%s exit=%s output=%s", name, attempt + 1, result.returncode, output[-2000:])
        if attempt < retries:
            time.sleep(retry_delay_seconds)
    return False, output


def planned(name: str, arguments: list[str], steps: list[dict[str, object]]) -> None:
    steps.append({"name": name, "status": "planned", "command": [sys.executable, *arguments]})


def execute(
    name: str,
    arguments: list[str],
    steps: list[dict[str, object]],
    logger: logging.Logger,
    *,
    dry_run: bool,
    retries: int = 0,
    retry_delay_seconds: int = 0,
) -> bool:
    if dry_run:
        planned(name, arguments, steps)
        return True
    success, output = command_result(
        name, arguments, logger=logger, retries=retries, retry_delay_seconds=retry_delay_seconds
    )
    steps.append({"name": name, "status": "ok" if success else "failed", "output": output[-2000:]})
    return success


def consume_events(
    event_path: Path,
    steps: list[dict[str, object]],
    logger: logging.Logger,
    *,
    dry_run: bool,
) -> None:
    processing_path = event_path.with_suffix(event_path.suffix + ".processing")
    source = processing_path if processing_path.exists() else event_path
    if not source.exists():
        steps.append({"name": "outlook_events", "status": "skipped", "reason": "No mailbox event file."})
        return
    if dry_run:
        planned("apply_outlook_events", ["scripts/scheduled_reconcile.py", "apply-events", "--events", str(source.relative_to(ROOT))], steps)
        return
    if source == event_path:
        event_path.replace(processing_path)
        source = processing_path
    arguments = ["scripts/scheduled_reconcile.py", "apply-events", "--events", str(source.relative_to(ROOT))]
    success = execute("apply_outlook_events", arguments, steps, logger, dry_run=False)
    if success:
        archive = source.with_name(source.stem + ".processed-" + datetime.now().strftime("%Y%m%dT%H%M%S") + source.suffix)
        source.replace(archive)
        steps.append({"name": "archive_outlook_events", "status": "ok", "path": str(archive.relative_to(ROOT))})
    else:
        source.replace(event_path)


def run_workflow(config: AutomationConfig, *, dry_run: bool, logger: logging.Logger) -> dict[str, object]:
    steps: list[dict[str, object]] = []
    execute(
        "configure_outlook_schedule",
        [
            "scripts/scheduled_reconcile.py", "configure",
            "--outlook-interval-minutes", str(config.outlook_interval_minutes),
            "--alert-interval-minutes", str(config.discovery_interval_minutes),
            "--pipeline-interval-minutes", str(config.pipeline_interval_minutes),
        ],
        steps, logger, dry_run=dry_run,
    )
    execute(
        "queue_maintenance",
        ["scripts/queue_maintenance.py", "--expire-stale", "--capacity", str(config.queue_capacity), "--low-watermark", str(config.low_watermark)],
        steps, logger, dry_run=dry_run,
    )
    consume_events(repo_path(config.mailbox_events_path), steps, logger, dry_run=dry_run)
    execute("export_tracker_csv", ["scripts/migrate_to_sqlite.py", "--export-csv"], steps, logger, dry_run=dry_run)
    execute("bootstrap_workflow_history", ["scripts/workflow_optimizer.py", "bootstrap-history"], steps, logger, dry_run=dry_run)
    if config.enable_ats_discovery_report:
        execute(
            "ats_discovery_report",
            ["scripts/ats_scan.py", "--dry-run", "--json"],
            steps, logger, dry_run=dry_run,
            retries=config.max_safe_retries,
            retry_delay_seconds=config.retry_delay_seconds,
        )
    else:
        steps.append({"name": "ats_discovery_report", "status": "skipped", "reason": "Disabled in local automation config."})
    if config.prepare_packets:
        execute(
            "prepare_application_packets",
            ["scripts/automation_pipeline.py", "--resume", "resumes/master.docx", "--min-score", str(config.min_score), "--limit", str(config.packet_limit), "--llm-provider", config.llm_provider],
            steps, logger, dry_run=dry_run,
        )
        execute("sync_packet_updates", ["scripts/migrate_to_sqlite.py", "--import-csv", "--export-csv"], steps, logger, dry_run=dry_run)
    else:
        steps.append({"name": "prepare_application_packets", "status": "skipped", "reason": "Disabled in local automation config."})
    execute(
        "refresh_workflow_optimization",
        ["scripts/workflow_optimizer.py", "report", "--out", "outputs/workflow_optimization_report.json", "--summary"],
        steps,
        logger,
        dry_run=dry_run,
    )
    execute("verify_tracker", ["scripts/verify_tracker.py"], steps, logger, dry_run=dry_run)
    execute("render_tracker_dashboard", ["scripts/tracker_report.py"], steps, logger, dry_run=dry_run)
    failed = [step["name"] for step in steps if step["status"] == "failed"]
    return {"dry_run": dry_run, "steps": steps, "failed_steps": failed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run safe local Resume Optimizer maintenance without browser submission.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true", help="Print planned work without modifying local workflow state.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logging()
    try:
        config = load_config(args.config)
        logger.info("local automation start dry_run=%s config=%s", args.dry_run, asdict(config))
        with run_lock(DEFAULT_LOCK, config.stale_lock_minutes):
            result = run_workflow(config, dry_run=args.dry_run, logger=logger)
        logger.info("local automation complete failed_steps=%s", result["failed_steps"])
        print(json.dumps(result, indent=2))
        return 1 if result["failed_steps"] else 0
    except Exception as exc:
        logger.exception("local automation failed: %s", exc)
        print(f"Local automation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
