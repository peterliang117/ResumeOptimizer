#!/usr/bin/env python3
"""Serve a local control dashboard for the job workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_queue import batch_progress, latest_batch_id, read_rows as read_queue_rows, sorted_queued_rows
from tracker import read_rows as read_tracker_rows


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"


def script_path(name: str) -> str:
    return str(ROOT / "scripts" / name)


def safe_report_path(value: str) -> Path:
    if not value:
        raise ValueError("report is required")
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    outputs = OUTPUTS.resolve()
    if outputs not in [resolved, *resolved.parents]:
        raise ValueError("report must be under outputs/")
    if not resolved.name.startswith("refill_candidates_") or resolved.suffix != ".json":
        raise ValueError("report must be a refill_candidates_*.json file")
    return resolved


def run_jobctl(args: list[str], timeout: int = 300) -> dict[str, object]:
    command = [sys.executable, script_path("jobctl.py"), *args]
    env = os.environ.copy()
    env.setdefault("LOCAL_LLM_ENABLED", "1")
    env.setdefault("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    env.setdefault("LOCAL_LLM_SCREENING_MODEL", "qwen3:8b")
    env.setdefault("LOCAL_LLM_RESUME_MODEL", "qwen3:14b")
    env.setdefault("LOCAL_LLM_APPLICATION_MODEL", "qwen3:14b")
    env.setdefault("RESUME_OPTIMIZER_LLM_PROVIDER", "local")
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "command": " ".join(command),
        "output": result.stdout,
    }


def batch_state(target_size: int = 10) -> dict[str, object]:
    rows = read_queue_rows(ROOT / "jobs" / "queue.csv")
    batch_id = latest_batch_id(rows)
    queued = sorted_queued_rows(rows)
    if not batch_id:
        return {
            "batch_id": "",
            "total": 0,
            "terminal_or_handoff": 0,
            "open": 0,
            "slots_remaining": target_size,
            "refill_ready": False,
            "queued": len(queued),
            "next_job": queued[0] if queued else None,
        }
    terminal, open_rows, slots_remaining, refill_ready = batch_progress(rows, batch_id, target_size)
    return {
        "batch_id": batch_id,
        "total": len([row for row in rows if row.get("batch_id", "") == batch_id]),
        "terminal_or_handoff": len(terminal),
        "open": len(open_rows),
        "slots_remaining": slots_remaining,
        "refill_ready": refill_ready,
        "queued": len(queued),
        "next_job": queued[0] if queued else None,
    }


def tracker_state() -> dict[str, object]:
    rows = read_tracker_rows(ROOT / "tracker" / "applications.csv")
    counts = Counter(row.get("status", "") or "(blank)" for row in rows)
    today = date.today().isoformat()
    due = sorted(
        [
            row
            for row in rows
            if row.get("follow_up_date", "") and row.get("follow_up_date", "") <= today
        ],
        key=lambda row: (row.get("follow_up_date", ""), row.get("company", ""), row.get("role", "")),
    )
    return {
        "total": len(rows),
        "counts": dict(counts.most_common()),
        "due_followups": due[:8],
    }


def tracker_match(rows: list[dict[str, str]], queue_row: dict[str, str]) -> dict[str, str]:
    url = queue_row.get("url", "")
    company = queue_row.get("company", "").strip().lower()
    role = queue_row.get("role", "").strip().lower()
    for row in rows:
        if url and row.get("url", "") == url:
            return row
        if row.get("company", "").strip().lower() == company and row.get("role", "").strip().lower() == role:
            return row
    return {}


def outcome_state() -> dict[str, object] | None:
    queue_rows = read_queue_rows(ROOT / "jobs" / "queue.csv")
    tracker_rows = read_tracker_rows(ROOT / "tracker" / "applications.csv")
    actionable_statuses = {
        "resume_ready",
        "manual_apply_needed",
        "blocked_needs_user_input",
        "analyzed",
    }
    for row in reversed(queue_rows):
        if row.get("status", "") not in actionable_statuses:
            continue
        tracker_row = tracker_match(tracker_rows, row)
        return {
            "company": row.get("company", ""),
            "role": row.get("role", ""),
            "source": row.get("source", ""),
            "url": row.get("url", ""),
            "status": row.get("status", ""),
            "priority": row.get("priority", ""),
            "match_score": row.get("match_score", ""),
            "resume_file": tracker_row.get("resume_file", ""),
            "application_folder": tracker_row.get("application_folder", ""),
        }
    return None


def update_application_outcome(status: str, job: dict[str, object]) -> dict[str, object]:
    today = date.today()
    company = str(job.get("company", ""))
    role = str(job.get("role", ""))
    if not company or not role:
        raise ValueError("No current application is available for outcome update.")

    if status == "submitted":
        notes = "Marked submitted from dashboard after manual application."
        command = [
            sys.executable,
            script_path("application_state.py"),
            "--company",
            company,
            "--role",
            role,
            "--status",
            "submitted",
            "--source",
            str(job.get("source", "")),
            "--url",
            str(job.get("url", "")),
            "--priority",
            str(job.get("priority", "")),
            "--resume-file",
            str(job.get("resume_file", "")),
            "--application-folder",
            str(job.get("application_folder", "")),
            "--submitted",
            today.isoformat(),
            "--follow-up-date",
            (today + timedelta(days=7)).isoformat(),
            "--stage",
            "application_submitted",
            "--stage-date",
            today.isoformat(),
            "--next-action",
            "Await company response.",
            "--notes",
            notes,
        ]
    elif status == "rejected":
        notes = "Marked rejected from dashboard."
        command = [
            sys.executable,
            script_path("application_state.py"),
            "--company",
            company,
            "--role",
            role,
            "--status",
            "rejected",
            "--source",
            str(job.get("source", "")),
            "--url",
            str(job.get("url", "")),
            "--priority",
            str(job.get("priority", "")),
            "--resume-file",
            str(job.get("resume_file", "")),
            "--application-folder",
            str(job.get("application_folder", "")),
            "--stage",
            "closed",
            "--stage-date",
            today.isoformat(),
            "--next-action",
            "",
            "--notes",
            notes,
        ]
    else:
        raise ValueError(f"Unsupported outcome status: {status}")

    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "command": " ".join(command),
        "output": result.stdout,
    }


def report_files() -> list[dict[str, object]]:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    reports = []
    empty_seen: set[str] = set()
    for path in sorted(OUTPUTS.glob("refill_candidates_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        metadata = report_file_metadata(path)
        batch_id = str(metadata["candidate_batch_id"])
        if metadata["candidate_count"] == 0 and batch_id in empty_seen:
            continue
        if metadata["candidate_count"] == 0:
            empty_seen.add(batch_id)
        reports.append(
            {
                "name": path.name,
                "label": metadata["label"],
                "path": str(path),
                "relative_path": str(path.relative_to(ROOT)),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "candidate_batch_id": batch_id,
                "candidate_count": metadata["candidate_count"],
            }
        )
    return reports


def report_file_metadata(path: Path) -> dict[str, object]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "candidate_batch_id": path.stem.replace("refill_candidates_", ""),
            "candidate_count": 0,
            "label": f"{path.stem} - unreadable",
        }
    candidates = report.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    batch_id = str(report.get("candidate_batch_id") or path.stem.replace("refill_candidates_", ""))
    suffix = "no new ATS candidates" if candidate_count == 0 else f"{candidate_count} candidates"
    return {
        "candidate_batch_id": batch_id,
        "candidate_count": candidate_count,
        "label": f"Batch {batch_id} - {suffix}",
    }


def load_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def valid_match_score(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        score = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return 0 <= score <= 100


def row_identity_keys(row: dict[str, object]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    url = str(row.get("url", "")).strip().rstrip("/").lower()
    company = str(row.get("company", "")).strip().lower()
    role = str(row.get("role", "")).strip().lower()
    if url:
        keys.add(("url", url))
    if company and role:
        keys.add(("company_role", f"{company}::{role}"))
    return keys


def workflow_status_index() -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for row in [*read_queue_rows(ROOT / "jobs" / "queue.csv"), *read_tracker_rows(ROOT / "tracker" / "applications.csv")]:
        status = str(row.get("status", "")).strip()
        if not status:
            continue
        for key in row_identity_keys(row):
            index[key] = status
    return index


def report_summary(report: dict[str, object]) -> dict[str, object]:
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    decisions = Counter()
    approved = 0
    hard_passed = 0
    ready_to_apply = 0
    completed = 0
    invalid_approved = []
    rows = []
    status_index = workflow_status_index()
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        workflow_status = ""
        for key in row_identity_keys(candidate):
            workflow_status = status_index.get(key, "")
            if workflow_status:
                break
        is_completed = workflow_status in {"submitted", "rejected", "skipped", "expired", "closed", "rejected_low_match"}
        if is_completed:
            completed += 1
        auto = candidate.get("auto_screen") if isinstance(candidate.get("auto_screen"), dict) else {}
        decision = str(auto.get("decision") or "unscreened")
        decisions[decision] += 1
        if candidate.get("approved") is True and not is_completed:
            approved += 1
        if candidate.get("hard_filters_passed") is True and not is_completed:
            hard_passed += 1
        approved_ready = (
            candidate.get("approved") is True
            and candidate.get("hard_filters_passed") is True
            and valid_match_score(candidate.get("match_score"))
            and not is_completed
        )
        if approved_ready:
            ready_to_apply += 1
        elif candidate.get("approved") is True and not is_completed:
            invalid_approved.append(
                {
                    "index": index,
                    "company": candidate.get("company", ""),
                    "role": candidate.get("role", ""),
                    "missing": [
                        item
                        for item, missing in [
                            ("hard filters", candidate.get("hard_filters_passed") is not True),
                            ("score", not valid_match_score(candidate.get("match_score"))),
                        ]
                        if missing
                    ],
                }
            )
        score = auto.get("score") if isinstance(auto.get("score"), dict) else {}
        rows.append(
            {
                "index": index,
                "decision": decision,
                "approved": candidate.get("approved") is True,
                "hard_filters_passed": candidate.get("hard_filters_passed") is True,
                "match_score": candidate.get("match_score"),
                "company": candidate.get("company", ""),
                "role": candidate.get("role", ""),
                "source": candidate.get("source", ""),
                "location": candidate.get("location", ""),
                "priority": candidate.get("priority", "medium"),
                "url": candidate.get("url", ""),
                "blockers": auto.get("blockers", []),
                "review_flags": auto.get("review_flags", []),
                "score_reasons": score.get("reasons", []),
                "verification_notes": candidate.get("verification_notes", ""),
                "workflow_status": workflow_status,
            }
        )
    return {
        "candidate_batch_id": report.get("candidate_batch_id", ""),
        "target_size": report.get("target_size", 10),
        "candidate_summary": report.get("candidate_summary", {}),
        "linkedin_searches": report.get("linkedin_searches", []),
        "is_empty_report": not rows,
        "decisions": dict(decisions),
        "approved": approved,
        "hard_filters_passed": hard_passed,
        "ready_to_apply": ready_to_apply,
        "completed": completed,
        "invalid_approved": invalid_approved,
        "candidates": rows,
        "warnings": report.get("warnings", []),
        "auto_screen_summary": report.get("auto_screen_summary", {}),
    }


def update_candidate(report_path: Path, payload: dict[str, object]) -> dict[str, object]:
    report = load_report(report_path)
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("report candidates must be an array")
    index = int(payload.get("index", 0))
    if index < 1 or index > len(candidates):
        raise ValueError("candidate index is out of range")
    candidate = candidates[index - 1]
    if not isinstance(candidate, dict):
        raise ValueError("candidate row is invalid")

    candidate["approved"] = bool(payload.get("approved", False))
    candidate["hard_filters_passed"] = bool(payload.get("hard_filters_passed", False))
    score_value = payload.get("match_score")
    if score_value in {"", None}:
        candidate["match_score"] = None
    else:
        score = int(score_value)  # type: ignore[arg-type]
        if not 0 <= score <= 100:
            raise ValueError("match_score must be 0-100")
        candidate["match_score"] = score
    candidate["priority"] = str(payload.get("priority", candidate.get("priority", "medium")) or "medium")
    candidate["verification_notes"] = str(payload.get("verification_notes", ""))
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report_summary(report)


def html_page() -> str:
    return r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Workflow Control</title>
  <style>
    :root {
      --bg: #f5f7f9;
      --panel: #fff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --accent: #0f766e;
      --danger: #a23b3b;
      --warn: #956700;
      --ok: #256b4f;
      --code: #eef2f6;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 "Segoe UI", Arial, sans-serif; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; background: var(--panel); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 2; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    .header-left { display: grid; gap: 4px; }
    #status-line { font-size: 13px; color: var(--muted); }
    #status-line.running { color: var(--warn); }
    #status-line.ok { color: var(--ok); }
    #status-line.bad { color: var(--danger); }
    main { max-width: 1500px; margin: 0 auto; padding: 18px; }
    .grid { display: grid; grid-template-columns: 330px minmax(0, 1fr); gap: 16px; align-items: start; }
    section, aside { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    h3 { margin: 18px 0 8px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0; }
    button, select, input, textarea { border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); padding: 8px 10px; font: inherit; }
    button { cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.danger { border-color: #d9a7a7; color: var(--danger); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    textarea { min-height: 58px; width: 100%; resize: vertical; }
    .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
    .metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .metric { border-left: 3px solid var(--line); border-radius: 6px; padding: 8px 10px; background: #fbfcfd; min-height: 62px; }
    .metric strong { display: block; font-size: 21px; line-height: 1.2; overflow-wrap: normal; }
    #batch-id { font-size: 18px; white-space: nowrap; }
    .muted { color: var(--muted); }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .bad { color: var(--danger); }
    .report-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
    .report-bar select { min-width: 360px; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td { border-bottom: 1px solid var(--line); padding: 8px; vertical-align: top; text-align: left; }
    th { font-size: 12px; color: var(--muted); background: #fafbfc; position: sticky; top: 58px; z-index: 1; }
    td.small, th.small { width: 76px; }
    td.approval, th.approval { width: 124px; }
    td.company { width: 250px; }
    td.url { width: 70px; }
    .table-tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 0 0 10px; padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: #fbfcfd; }
    .table-tools label { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; white-space: nowrap; }
    .table-tools select { padding: 6px 8px; min-width: 132px; }
    .approval-options { display: grid; gap: 6px; }
    .check-row { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; line-height: 1.2; }
    .check-row input { margin: 0; padding: 0; width: 14px; height: 14px; flex: 0 0 auto; }
    .tag { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; border: 1px solid var(--line); background: #f8fafc; }
    .decision-candidate { color: var(--ok); border-color: #9bd0bd; background: #edf8f3; }
    .decision-review { color: var(--warn); border-color: #ead28a; background: #fff9e6; }
    .decision-reject, .decision-fetch_failed { color: var(--danger); border-color: #e2b0b0; background: #fff1f1; }
    .workflow-status { display: inline-block; margin-top: 6px; color: var(--muted); font-size: 12px; }
    .list { margin: 0; padding-left: 18px; }
    .log { white-space: pre-wrap; background: var(--code); border: 1px solid var(--line); border-radius: 8px; padding: 12px; max-height: 240px; overflow: auto; }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .help { border: 1px solid var(--line); background: #fbfcfd; border-radius: 8px; padding: 10px 12px; margin: 0 0 12px; color: var(--muted); }
    .notice { border: 1px solid #ead28a; background: #fff9e6; color: var(--warn); border-radius: 8px; padding: 8px 10px; margin: 0 0 10px; }
    .empty-state { padding: 16px; color: var(--muted); }
    .empty-state strong { color: var(--ink); }
    .save-hint { display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }
    .outcome-box { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfcfd; display: grid; gap: 8px; }
    .outcome-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .hidden { display: none !important; }
    .workflow-actions { display: grid; gap: 8px; }
    .action-row { display: flex; gap: 8px; flex-wrap: wrap; }
    .action-note { margin: 0; color: var(--muted); font-size: 12px; }
    a { color: #0b5cad; text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 980px) {
      .grid, .split { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .report-bar select { min-width: 100%; }
      th { position: static; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-left">
      <h1>Job Workflow Control</h1>
      <div id="status-line">Ready</div>
    </div>
    <div class="controls">
      <button id="refresh">Refresh</button>
      <button id="verify">Verify</button>
      <button id="run-batch" class="danger">Run Batch</button>
    </div>
  </header>
  <main>
    <div class="grid">
      <aside>
        <h2>State</h2>
        <div class="metrics">
          <div class="metric"><span class="muted">Batch</span><strong id="batch-id">-</strong></div>
          <div class="metric"><span class="muted">Open</span><strong id="open-count">-</strong></div>
          <div class="metric"><span class="muted">Queued</span><strong id="queued-count">-</strong></div>
          <div class="metric"><span class="muted">Refill</span><strong id="refill-ready">-</strong></div>
        </div>
        <h3>Next</h3>
        <div id="next-job" class="muted">-</div>
        <h3>Current Application</h3>
        <div class="outcome-box">
          <div id="outcome-job" class="muted">No application awaiting outcome</div>
          <div class="outcome-actions">
            <button id="mark-submitted" class="primary">Submitted</button>
            <button id="mark-rejected" class="danger">Rejected</button>
          </div>
        </div>
        <h3>Reports</h3>
        <div class="workflow-actions">
          <div class="action-row">
            <button id="refill" class="primary" title="Create a new refill candidate report. Does not modify the queue.">Find Candidates</button>
            <button id="screen" title="Fetch job descriptions and calculate match scores for the selected report.">Score Report</button>
          </div>
          <p class="action-note">Find creates a review report; Score fetches JD text and writes decisions/scores.</p>
          <div class="action-row">
            <button id="csv" title="Export selected report to a CSV for spreadsheet review. Does not modify the queue.">Export CSV</button>
            <button id="apply" class="danger" title="Add approved, hard-filtered candidates from the selected report to jobs/queue.csv. Does not submit applications.">Add Approved to Queue</button>
          </div>
          <p class="action-note">Export is review-only; Add writes approved candidates into the queue.</p>
        </div>
        <h3>Tracker</h3>
        <div id="tracker-counts"></div>
      </aside>
      <section>
        <div class="report-bar">
          <h2>Refill Candidates</h2>
          <select id="report-select"></select>
          <button id="load-report">Load</button>
          <span id="report-summary" class="muted"></span>
        </div>
        <div class="help">
          <strong>approve</strong> selects a candidate for the next queue batch.
          <strong>hard filters</strong> means you verified location, pay, sponsorship, employer type, recency, and role-core fit.
          Add Approved to Queue accepts 1 to 10 approved candidates; every approved candidate must pass hard filters and have a score.
        </div>
        <div id="warnings"></div>
        <div class="table-tools">
          <label>Decision
            <select id="decision-filter">
              <option value="all">All</option>
              <option value="candidate">Candidate</option>
              <option value="review">Review</option>
              <option value="reject">Reject</option>
              <option value="fetch_failed">Fetch failed</option>
              <option value="unscreened">Unscreened</option>
            </select>
          </label>
          <label>Approval
            <select id="approval-filter">
              <option value="all">All</option>
              <option value="ready">Ready</option>
              <option value="approved">Approved</option>
              <option value="queued_pending">Queued/pending</option>
              <option value="completed">Completed</option>
              <option value="approved_incomplete">Approved incomplete</option>
              <option value="hard_filters">Hard filters</option>
              <option value="unapproved">Unapproved</option>
            </select>
          </label>
          <label>Sort
            <select id="candidate-sort">
              <option value="default">Default order</option>
              <option value="decision">Decision</option>
              <option value="approval">Approval</option>
              <option value="score_desc">Score high to low</option>
              <option value="score_asc">Score low to high</option>
              <option value="company">Company</option>
            </select>
          </label>
        </div>
        <table>
          <thead>
            <tr>
              <th class="small">Decision</th>
              <th class="approval">Approval</th>
              <th class="small">Score</th>
              <th>Company / Role</th>
              <th>Evidence</th>
              <th>Notes</th>
              <th class="url">Link</th>
            </tr>
          </thead>
          <tbody id="candidate-body"></tbody>
        </table>
        <h3>Command Log</h3>
        <div id="log" class="log muted">Ready</div>
      </section>
    </div>
  </main>
  <script>
    const state = { report: "" };
    const $ = id => document.getElementById(id);
    async function api(path, options = {}) {
      const response = await fetch(path, { cache: "no-store", ...options });
      const text = await response.text();
      let payload;
      try { payload = JSON.parse(text); } catch { payload = { ok: false, output: text }; }
      if (!response.ok) throw new Error(payload.error || text);
      return payload;
    }
    function log(payload) {
      $("log").textContent = typeof payload === "string" ? payload : (payload.output || JSON.stringify(payload, null, 2));
    }
    function setStatus(text, kind = "") {
      const status = $("status-line");
      status.textContent = text;
      status.className = kind;
    }
    function syncControls() {
      const hasQueued = state.batch && Number(state.batch.queued || 0) > 0;
      const hasOutcome = Boolean(state.outcome);
      $("run-batch").disabled = !hasQueued;
      $("run-batch").title = hasQueued ? "Run current queued jobs" : "No queued jobs. Add approved candidates to queue first.";
      $("outcome-job").textContent = hasOutcome
        ? `${state.outcome.company} - ${state.outcome.role} (${state.outcome.status})`
        : "No application awaiting outcome";
      $("mark-submitted").disabled = !hasOutcome;
      $("mark-rejected").disabled = !hasOutcome;
      $("mark-submitted").classList.toggle("hidden", !hasOutcome);
      $("mark-rejected").classList.toggle("hidden", !hasOutcome);
    }
    function setBusy(busy) {
      for (const button of document.querySelectorAll("button")) button.disabled = busy;
      $("report-select").disabled = busy;
      if (!busy) syncControls();
    }
    async function refresh() {
      setStatus("Refreshing...", "running");
      const payload = await api("/api/state");
      const batch = payload.batch;
      state.batch = batch;
      $("batch-id").textContent = batch.batch_id || "none";
      $("open-count").textContent = batch.open;
      $("queued-count").textContent = batch.queued;
      $("refill-ready").textContent = batch.refill_ready ? "yes" : "no";
      $("refill-ready").className = batch.refill_ready ? "ok" : "warn";
      $("next-job").textContent = batch.next_job ? `${batch.next_job.company} - ${batch.next_job.role}` : "No queued job";
      state.outcome = payload.outcome || null;
      syncControls();
      $("tracker-counts").innerHTML = Object.entries(payload.tracker.counts).map(([k,v]) => `<div>${k}: <strong>${v}</strong></div>`).join("");
      const select = $("report-select");
      const current = select.value || state.report;
      select.innerHTML = payload.reports.map(r => `<option value="${r.relative_path}">${escapeHtml(r.label || r.name)}</option>`).join("");
      if (current) select.value = current;
      state.report = select.value || "";
      setStatus("Ready", "ok");
    }
    async function loadReport() {
      setStatus("Loading report...", "running");
      state.report = $("report-select").value;
      if (!state.report) {
        setStatus("No report selected", "warn");
        return;
      }
      const payload = await api(`/api/report?path=${encodeURIComponent(state.report)}`);
      const summary = payload.summary;
      state.summary = summary;
      $("report-summary").textContent = `approved ${summary.approved}, ready ${summary.ready_to_apply}, completed ${summary.completed || 0}, max ${summary.target_size} | candidate ${summary.decisions.candidate || 0} | review ${summary.decisions.review || 0} | reject ${summary.decisions.reject || 0}`;
      const invalid = summary.invalid_approved || [];
      const emptyReason = summary.candidate_summary && summary.candidate_summary.empty_reason
        ? summary.candidate_summary.empty_reason
        : "";
      const emptyHtml = summary.is_empty_report
        ? `<div class="notice"><strong>This report has no ATS candidates.</strong> ${escapeHtml(emptyReason || "No candidates were written to this report.")} LinkedIn searches available: ${Number((summary.linkedin_searches || []).length)}.</div>`
        : "";
      const invalidHtml = invalid.length
        ? `<div class="notice"><strong>Approved candidates are not ready:</strong> ${invalid.map(row => `${escapeHtml(row.company)} - ${escapeHtml(row.role)} missing ${escapeHtml(row.missing.join(", "))}`).join("; ")}</div>`
        : "";
      $("warnings").innerHTML = emptyHtml + invalidHtml + (summary.warnings || []).map(w => `<p class="bad">${escapeHtml(w)}</p>`).join("");
      renderCandidates();
      setStatus("Report loaded", "ok");
    }
    function candidateReady(row) {
      const score = Number(row.match_score);
      return row.approved && row.hard_filters_passed && Number.isInteger(score) && score >= 0 && score <= 100;
    }
    function candidateCompleted(row) {
      return ["submitted", "rejected", "skipped", "expired", "closed", "rejected_low_match"].includes(row.workflow_status || "");
    }
    function candidateApprovalRank(row) {
      if (candidateReady(row)) return 0;
      if (row.approved) return 1;
      if (row.hard_filters_passed) return 2;
      return 3;
    }
    function decisionRank(row) {
      return {candidate: 0, review: 1, unscreened: 2, fetch_failed: 3, reject: 4}[row.decision] ?? 5;
    }
    function filteredCandidates() {
      const summary = state.summary || {};
      let rows = [...(summary.candidates || [])];
      const decisionFilter = $("decision-filter").value;
      const approvalFilter = $("approval-filter").value;
      if (decisionFilter !== "all") rows = rows.filter(row => row.decision === decisionFilter);
      if (approvalFilter === "ready") rows = rows.filter(candidateReady);
      if (approvalFilter === "approved") rows = rows.filter(row => row.approved && !candidateCompleted(row));
      if (approvalFilter === "queued_pending") rows = rows.filter(row => row.workflow_status && !candidateCompleted(row));
      if (approvalFilter === "completed") rows = rows.filter(candidateCompleted);
      if (approvalFilter === "approved_incomplete") rows = rows.filter(row => row.approved && !candidateReady(row));
      if (approvalFilter === "hard_filters") rows = rows.filter(row => row.hard_filters_passed);
      if (approvalFilter === "unapproved") rows = rows.filter(row => !row.approved);
      const sort = $("candidate-sort").value;
      rows.sort((a, b) => {
        if (sort === "decision") return decisionRank(a) - decisionRank(b) || Number(b.match_score || -1) - Number(a.match_score || -1);
        if (sort === "approval") return candidateApprovalRank(a) - candidateApprovalRank(b) || Number(b.match_score || -1) - Number(a.match_score || -1);
        if (sort === "score_desc") return Number(b.match_score || -1) - Number(a.match_score || -1);
        if (sort === "score_asc") return Number(a.match_score ?? 999) - Number(b.match_score ?? 999);
        if (sort === "company") return `${a.company} ${a.role}`.localeCompare(`${b.company} ${b.role}`);
        return Number(a.index) - Number(b.index);
      });
      return rows;
    }
    function renderCandidates() {
      const rows = filteredCandidates();
      if (rows.length) {
        $("candidate-body").innerHTML = rows.map(rowHtml).join("");
        return;
      }
      const emptyReport = state.summary && state.summary.is_empty_report;
      const message = emptyReport
        ? "This report has no ATS candidate rows. Use Find Candidates later after sources change, or open an older report / LinkedIn searches."
        : "No candidates match the current filters.";
      $("candidate-body").innerHTML = `<tr><td colspan="7" class="empty-state"><strong>${escapeHtml(message)}</strong></td></tr>`;
    }
    function rowHtml(row) {
      const flags = [...(row.blockers || []), ...(row.review_flags || []), ...(row.score_reasons || [])];
      const workflow = row.workflow_status ? `<span class="workflow-status">workflow: ${escapeHtml(row.workflow_status)}</span>` : "";
      return `<tr>
        <td class="small"><span class="tag decision-${row.decision}">${escapeHtml(row.decision)}</span></td>
        <td class="approval">
          <div class="approval-options">
            <label class="check-row" title="Select this candidate for the next batch"><input type="checkbox" data-autosave="true" data-field="approved" data-index="${row.index}" ${row.approved ? "checked" : ""}> approve</label>
            <label class="check-row" title="Verified location, pay, sponsorship, employer type, recency, and role-core fit"><input type="checkbox" data-autosave="true" data-field="hard_filters_passed" data-index="${row.index}" ${row.hard_filters_passed ? "checked" : ""}> hard filters</label>
          </div>
        </td>
        <td class="small"><input data-autosave="true" data-field="match_score" data-index="${row.index}" value="${row.match_score ?? ""}" style="width:64px"></td>
        <td class="company"><strong>${escapeHtml(row.company)}</strong><br>${escapeHtml(row.role)}<br><span class="muted">${escapeHtml(row.location || "")}</span><br>${workflow}</td>
        <td><ul class="list">${flags.map(item => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul></td>
        <td><textarea data-field="verification_notes" data-index="${row.index}">${escapeHtml(row.verification_notes || "")}</textarea><button data-save="${row.index}">Save notes</button><span class="save-hint">Approval and score auto-save. Notes require Save notes.</span></td>
        <td class="url"><a href="${escapeAttr(row.url)}" target="_blank" rel="noreferrer">Open</a></td>
      </tr>`;
    }
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function escapeAttr(value) { return escapeHtml(value || ""); }
    function actionLabel(name) {
      return {
        "verify": "Verify",
        "run-batch": "Run Batch",
        "refill": "Find Candidates",
        "screen": "Score Report",
        "csv": "Export CSV",
        "apply": "Add Approved to Queue",
        "mark-submitted": "Mark Submitted",
        "mark-rejected": "Mark Rejected",
      }[name] || name;
    }
    async function saveCandidate(index) {
      const fields = [...document.querySelectorAll(`[data-index="${index}"]`)];
      const payload = { report: state.report, index };
      for (const field of fields) {
        const name = field.dataset.field;
        payload[name] = field.type === "checkbox" ? field.checked : field.value;
      }
      const result = await api("/api/candidate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      log("Saved candidate " + index);
      setStatus("Saved candidate " + index, "ok");
      await loadReport();
    }
    async function action(name) {
      const needsReport = ["screen", "csv", "apply"].includes(name);
      if (needsReport && !state.report) {
        setStatus("Select a report first", "bad");
        return log("Select a report first.");
      }
      if (name === "run-batch" && state.batch && Number(state.batch.queued || 0) === 0) {
        setStatus("No queued jobs to run", "warn");
        return log("No queued jobs to process. Use Find Candidates, Score Report, approve 1 to 10 candidates, then Add Approved to Queue first.");
      }
      if (name === "apply" && state.summary) {
        const invalid = state.summary.invalid_approved || [];
        if (Number(state.summary.ready_to_apply || 0) === 0 || invalid.length) {
          const details = invalid.map(row => `${row.index}. ${row.company} - ${row.role}: missing ${row.missing.join(", ")}`).join("\n");
          setStatus("Approved candidates are not ready", "bad");
          return log(`Add Approved to Queue blocked. Every approved candidate needs hard filters and a 0-100 score.\n${details}`);
        }
      }
      if (name === "apply" && !confirm("Add approved candidates to jobs/queue.csv? This does not start applications yet.")) return;
      if (name === "run-batch" && !confirm("Run the current queued jobs now? This generates application packets/resumes but does not submit.")) return;
      if (name === "mark-submitted" && !confirm("Mark this application as submitted in queue and tracker?")) return;
      if (name === "mark-rejected" && !confirm("Mark this application as rejected in queue and tracker?")) return;
      const label = actionLabel(name);
      setStatus(`${label} running...`, "running");
      setBusy(true);
      try {
        const result = await api("/api/action", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ action: name, report: state.report })
        });
        log(result);
        await refresh();
        if (state.report) await loadReport();
        if (name === "apply" && result.ok) {
          setStatus("Queued approved candidates. Next: Run Batch.", "ok");
        } else {
          setStatus(result.ok ? `${label} completed` : `${label} failed`, result.ok ? "ok" : "bad");
        }
      } catch (error) {
        log(error.message);
        setStatus(`${actionLabel(name)} failed: ${error.message}`, "bad");
      } finally {
        setBusy(false);
      }
    }
    document.addEventListener("click", event => {
      const save = event.target.dataset && event.target.dataset.save;
      if (save) saveCandidate(Number(save));
    });
    document.addEventListener("change", event => {
      const target = event.target;
      if (target.id === "decision-filter" || target.id === "approval-filter" || target.id === "candidate-sort") {
        renderCandidates();
        return;
      }
      if (!target.dataset || target.dataset.autosave !== "true") return;
      const index = Number(target.dataset.index);
      if (!index) return;
      setStatus("Saving candidate " + index + "...", "running");
      saveCandidate(index);
    });
    $("refresh").onclick = refresh;
    $("load-report").onclick = loadReport;
    $("verify").onclick = () => action("verify");
    $("run-batch").onclick = () => action("run-batch");
    $("refill").onclick = () => action("refill");
    $("screen").onclick = () => action("screen");
    $("csv").onclick = () => action("csv");
    $("apply").onclick = () => action("apply");
    $("mark-submitted").onclick = () => action("mark-submitted");
    $("mark-rejected").onclick = () => action("mark-rejected");
    refresh().then(loadReport).catch(error => {
      log(error.message);
      setStatus(error.message, "bad");
    });
  </script>
</body>
</html>"""


def build_handler() -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def send_payload(self, payload: object, status: int = 200) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def send_html(self, content: str) -> None:
            encoded = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            self.wfile.write(encoded)

        def read_json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_html(html_page())
                    return
                if parsed.path == "/api/state":
                    self.send_payload(
                        {
                            "batch": batch_state(),
                            "tracker": tracker_state(),
                            "outcome": outcome_state(),
                            "reports": report_files(),
                        }
                    )
                    return
                if parsed.path == "/api/report":
                    params = parse_qs(parsed.query)
                    report_path = safe_report_path(params.get("path", [""])[0])
                    report = load_report(report_path)
                    self.send_payload({"path": str(report_path), "summary": report_summary(report)})
                    return
                self.send_payload({"error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001 - local dashboard should surface errors
                self.send_payload({"error": str(exc)}, status=400)

        def do_POST(self) -> None:
            try:
                payload = self.read_json()
                parsed = urlparse(self.path)
                if parsed.path == "/api/candidate":
                    report_path = safe_report_path(str(payload.get("report", "")))
                    self.send_payload({"ok": True, "summary": update_candidate(report_path, payload)})
                    return
                if parsed.path == "/api/action":
                    self.send_payload(handle_action(payload))
                    return
                self.send_payload({"error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001
                self.send_payload({"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def handle_action(payload: dict[str, object]) -> dict[str, object]:
    action = str(payload.get("action", ""))
    if action == "verify":
        return run_jobctl(["verify"])
    if action == "run-batch":
        return run_jobctl(["run-current-batch"], timeout=1800)
    if action == "refill":
        return run_jobctl(["refill-next-batch"], timeout=600)
    if action == "mark-submitted":
        job = outcome_state()
        if not job:
            raise ValueError("No current application is available to mark submitted.")
        return update_application_outcome("submitted", job)
    if action == "mark-rejected":
        job = outcome_state()
        if not job:
            raise ValueError("No current application is available to mark rejected.")
        return update_application_outcome("rejected", job)
    if action in {"screen", "csv", "apply"}:
        report = safe_report_path(str(payload.get("report", "")))
        rel_report = str(report.relative_to(ROOT))
        if action == "screen":
            return run_jobctl(["screen-refill-candidates", rel_report], timeout=1800)
        if action == "csv":
            out = str(report.with_name(f"{report.stem}_actionable.csv").relative_to(ROOT))
            return run_jobctl(["review-refill-report", rel_report, "--actionable-only", "--out", out])
        if action == "apply":
            return run_jobctl(["refill-next-batch", "--apply-reviewed", rel_report], timeout=600)
    raise ValueError(f"unsupported action: {action}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the local job workflow dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), build_handler())
    print(f"Workflow dashboard: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
