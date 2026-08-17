# Stable Commands

Use these commands when you want the workflow to keep moving without relying on
Codex to remember the exact script sequence.

From PowerShell:

```powershell
Set-Location C:\path\to\ResumeOptimizer
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\jobctl.ps1 status
```

The PowerShell wrapper loads the local LLM environment for the current shell and
uses `.venv\Scripts\python.exe` when the virtual environment exists.

## Interactive Dashboard

Start the centralized local control dashboard:

```powershell
.\scripts\workflow_dashboard.ps1
```

Open:

```text
http://127.0.0.1:8770/
```

The dashboard can refresh status, generate refill reports, screen candidates,
export review CSVs, edit candidate approval fields in the JSON report, apply a
reviewed refill batch, run the current batch, and run verification. Queue writes
still go through the same `jobctl.py` guarded commands.

## Commands

Check current state:

```powershell
.\scripts\jobctl.ps1 status
```

Run local checks:

```powershell
.\scripts\jobctl.ps1 verify
```

Refresh learned barrier rules and the local optimization report:

```powershell
.\scripts\jobctl.ps1 optimize
```

Process the current queue without discovering or refilling jobs:

```powershell
.\scripts\jobctl.ps1 run-current-batch
```

Generate a guarded refill review report after the current batch is exhausted:

```powershell
.\scripts\jobctl.ps1 refill-next-batch
```

If you only want LinkedIn browser-assisted search URLs and no ATS feed fetches:

```powershell
.\scripts\jobctl.ps1 refill-next-batch --skip-ats
```

Auto-screen the generated ATS candidates by fetching each job description,
scoring the role, and adding blocker/review flags to the report:

```powershell
.\scripts\jobctl.ps1 screen-refill-candidates outputs\refill_candidates_YYYY-MM-DD-01.json
```

Export the screened report to CSV for Excel or spreadsheet review:

```powershell
.\scripts\jobctl.ps1 review-refill-report outputs\refill_candidates_YYYY-MM-DD-01.json
```

To export only rows that still need attention:

```powershell
.\scripts\jobctl.ps1 review-refill-report outputs\refill_candidates_YYYY-MM-DD-01.json --actionable-only
```

After reviewing the generated report, mark 1 to 10 candidates with
`approved: true`, `hard_filters_passed: true`, and an integer `match_score`.
Then apply the reviewed batch:

```powershell
.\scripts\jobctl.ps1 refill-next-batch --apply-reviewed outputs\refill_candidates_YYYY-MM-DD-01.json
```

Prepare an application packet for the next queued job only:

```powershell
.\scripts\jobctl.ps1 prepare-next-packet
```

Select the next prepared job for browser application work without regenerating
its packet or resume:

```powershell
python scripts\job_queue.py next-application
```

Generate a browser/agent handoff file for a tracker row:

```powershell
.\scripts\jobctl.ps1 generate-handoff `
  --company "Example Co" `
  --role "Senior Data Engineer"
```

The handoff file is written to the application folder as `agent_handoff.json`
when the tracker row has an `application_folder`; otherwise it is written under
`outputs/`.

## Safety Boundary

These commands keep the existing workflow rules:

- `run-current-batch` starts with `job_queue.py batch-status --target-size 10`.
- It does not discover replacement jobs or refill an exhausted batch.
- `refill-next-batch` refuses to run unless the latest batch is exhausted and no
  queued jobs remain.
- `refill-next-batch` defaults to a review report. It does not write the queue
  until `--apply-reviewed` receives 1 to 10 approved, hard-filtered, scored
  candidates. Do not force the batch to 10 when the verified candidate pool is
  smaller.
- `screen-refill-candidates` can score and flag candidates automatically, but it
  does not set `approved: true` or `hard_filters_passed: true`.
- `review-refill-report` exports a CSV for review only. The JSON report remains
  the source for `--apply-reviewed`.
- Resume packet preparation defaults to `--llm-provider codex`, which makes no
  external LLM request. Azure or a local model server are explicit opt-ins.
- Workflow attempts are bounded by the budgets in
  `docs/process_optimization.md`; repeated failures and overruns become local
  learned rules instead of repeated troubleshooting.
- `generate-handoff` includes blocked actions for final submit, legal
  attestations, unsupported claims, and inferred sensitive answers.
- Application answers still come from `profile/application_answers.json`; profile
  truth still comes from `profile/facts.md`.

If Codex usage is unavailable, continue from PowerShell with these commands and
bring Codex back only for review, unusual blockers, or code changes.
