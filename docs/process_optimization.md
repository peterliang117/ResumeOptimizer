# Adaptive Process Optimization

The workflow records local effort metadata and learns when a repeated path
should be retried once, switched, handed off, or skipped. This prevents a
single job or broken integration from consuming the rest of an application
run.

The learner stores only workflow metadata in the ignored SQLite database:
stage, platform, outcome, barrier, elapsed time, interaction count, an optional
token estimate, and the action taken. It does not store credentials, mailbox
bodies, application answers, or browser contents.

## Decision Order

Use the cheapest decisive check first:

1. Deduplicate by canonical URL and company/role before opening a posting.
2. Verify that the direct employer posting is live before scoring or tailoring.
3. Apply role-core, location, compensation, employer-type, and explicit
   sponsorship gates before generating a resume.
4. Reuse an existing verified role-family resume and application packet when
   the exact company/role packet is already complete.
5. Resolve the direct ATS URL before browser filling.
6. Audit required fields before filling; skip optional fields unless useful.
7. Fill required fields, verify the uploaded filename once, and validate once
   before submission.
8. At the first hard barrier, preserve a complete handoff and continue the
   queue.

## Default Budgets

| Stage | Time | Interactions | Attempts | On exhaustion |
| --- | ---: | ---: | ---: | --- |
| Discovery | 180 sec | 8 | 1 | Skip candidate and continue |
| Live posting verification | 60 sec | 5 | 1 | Expire or skip candidate |
| Hard screening | 90 sec | 5 | 1 | Skip candidate |
| Resume tailoring | 240 sec | 8 | 1 | Use local Codex evidence path |
| Resume rendering | 90 sec | 3 | 1 | Use fallback renderer |
| ATS open/login | 60 sec | 5 | 1 | Direct URL or handoff |
| ATS required-field fill | 360 sec | 14 | 1 | Handoff and continue queue |
| Resume upload | 90 sec | 5 | 2 | One alternate path, then handoff |
| Final validation/submit | 120 sec | 5 | 1 | Handoff and continue queue |

Elapsed time and interaction count are the default effort proxies because the
repo cannot read Codex token consumption directly. Record a token estimate only
when the runtime exposes one; the process never depends on it.

## Barrier Actions

| Barrier | Immediate action |
| --- | --- |
| Malformed or unavailable Azure response | Use local Codex; do not retry Azure |
| LinkedIn wrapper | Open the direct employer ATS |
| Browser native-host failure | Switch browser path once; do not repair during an application |
| Closed posting, duplicate, employer saturation, staffing post, role mismatch | Skip before resume work |
| Explicit no-sponsorship wording | Skip candidate |
| CAPTCHA, login, email code, or missing required fact | Produce manual handoff immediately |
| Upload or ATS widget failure | Try one grounded alternate interaction, then hand off |
| Render timeout | Stop the renderer and use the fallback verification path |
| Any stage over budget | Preserve state, move on, and let the learner escalate future advice |

Two failed or handed-off attempts for the same stage, platform, and barrier
create an active learned rule for 45 days. Repeated budget overruns do the same.
Successful future attempts remain recorded so a recovered path can be evaluated
instead of being disabled permanently.

## Commands

Before a failure-prone stage, ask for current guidance and start its timer:

```powershell
.\.venv\Scripts\python.exe scripts\workflow_optimizer.py advise `
  --stage ats_fill --platform ashby

.\.venv\Scripts\python.exe scripts\workflow_optimizer.py start `
  --stage ats_fill --platform ashby
```

Keep the returned `attempt_id`. Finish it after the stage:

```powershell
.\.venv\Scripts\python.exe scripts\workflow_optimizer.py finish `
  --attempt-id 123 --outcome success --interaction-count 8
```

When a barrier also changes application state, record both atomically through
the normal state command:

```powershell
.\.venv\Scripts\python.exe scripts\application_state.py `
  --company "Example Co" --role "Senior Data Engineer" `
  --status manual_apply_needed --workflow-stage ats_open `
  --platform workday --barrier login_required `
  --workflow-outcome handoff --action-taken manual_handoff
```

Refresh the aggregate report manually:

```powershell
.\scripts\jobctl.ps1 optimize
```

The report is local at `outputs/workflow_optimization_report.json`. The Windows
local automation also imports newly recognizable historical barriers and
refreshes this report on every scheduled run. Importing history is idempotent.

## Operating Rule

Do not investigate a known barrier twice in the same application. Follow the
learned action, update state once, give the user the direct URL and resume path
when a handoff is required, and continue with the next queued job.
