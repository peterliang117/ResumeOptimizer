# Resume Optimizer

Local, privacy-first job application pipeline for browser-assisted discovery, match scoring, truthful resume tailoring, fact-gated application filling, and local application tracking.

The project keeps private job-search criteria, profile facts, application answers, resumes, application packets, and tracker data out of Git. Public files contain reusable tooling and templates; local private files drive the actual search and application decisions.

![Resume Optimizer Workflow](docs/resume_optimizer_workflow.svg)

See [docs/folder_structure.md](docs/folder_structure.md) for the tracked,
private, and generated directory boundaries.

## Setup

Install LibreOffice for PDF export.

macOS:

```bash
brew install --cask libreoffice
```

Windows: install LibreOffice from https://www.libreoffice.org/download/. The page-check script looks for the standard `Program Files\LibreOffice\program\soffice.exe` install path, or any `soffice`/`soffice.exe` on `PATH`.

Create a virtual environment and install Python dependencies:

```bash
cd ResumeOptimizer
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

Optional Azure OpenAI analysis:

```bash
export AZURE_OPENAI_ENDPOINT="https://your-resource.cognitiveservices.azure.com"
export AZURE_OPENAI_DEPLOYMENT="your-deployment-name"
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_API_VERSION="2025-04-01-preview"
```

On Windows PowerShell, use `$env:NAME = "value"` instead of `export`.

The pipeline can also read the Azure endpoint and key from a private
`keys.txt` file in the repository or one of its parent directories, or from an
explicit `AZURE_OPENAI_KEYS_FILE` path. Keep that file ignored and local. The
file can be either:

```text
https://your-resource.cognitiveservices.azure.com
your-api-key
your-deployment-name
```

or:

```text
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
```

If the deployment name is not in `keys.txt`, set it in PowerShell:

```powershell
$env:AZURE_OPENAI_DEPLOYMENT = "your-deployment-name"
```

Check whether the workflow is Azure-ready without printing secrets:

```powershell
python scripts/azure_status.py
```

Optional local LLM analysis:

```powershell
winget install -e --id Ollama.Ollama
ollama pull qwen3:8b
ollama pull qwen3:14b
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
. .\scripts\use_local_llm.ps1
python scripts\local_llm_status.py --check-server
```

See [docs/local_llm_setup.md](docs/local_llm_setup.md) for the full local LLM
setup and task routing table.

`tailor.py` defaults to `--llm-provider codex`: it makes no external LLM call
and writes a local, fact-bound evidence packet for Codex review and tailoring.
Azure, a configured local OpenAI-compatible endpoint, or Azure-first `auto`
routing are opt-in with `--llm-provider azure`, `local`, or `auto`. The personal
OpenAI API path is disabled for this local workflow.

The scripts call the LLM API over HTTPS with `requests`, so they do not need an
OpenAI Python SDK.

## Privacy And Secret Safety

Keep private job-search data out of Git:

```text
resumes/master.docx
profile/facts.md
profile/application_answers.json
profile/search_criteria.md
applications/
outputs/
tracker/applications.csv
data/resume_optimizer.db
.env
```

Use `profile/facts.example.md`, `profile/application_answers.example.json`, `profile/search_criteria.example.md`, and `profile/portals.example.yml` as templates, then keep the real files local.

`DATA_CONTRACT.md` defines the public system layer versus the private local layer. Treat it as the rule for what can be pushed.

Before committing or pushing, run:

```bash
python scripts/security_check.py --fail-on-finding
python scripts/doctor.py
python scripts/verify_tracker.py
git status --short --ignored
```

The security check scans tracked and unignored files for common OpenAI keys, GitHub tokens, bearer tokens, and private-key blocks. It does not replace GitHub secret scanning, but it catches common local mistakes before push.

Recommended GitHub repository settings:

- Keep the repository private if it contains job-search automation, resume content, or personal workflow details.
- Enable secret scanning and push protection.
- Require pull-request review before merging to `main` if others get write access.
- Do not store API keys in GitHub Actions secrets unless a workflow genuinely needs them.
- Rotate any API key immediately if it is pasted into a tracked file, terminal transcript, issue, PR, or commit.

## Core Workflow

The production workflow uses a local SQLite source of truth with CSV exports
for compatibility. It is a rolling queue rather than a closed batch: stale,
unstarted postings expire after the saved freshness window, and discovery
refills when the active queue reaches its low-water mark.

1. Read private search rules from `profile/search_criteria.md`.
2. Expire stale unstarted jobs and check queue capacity.
3. Search LinkedIn, direct employer career sites, and supported ATS platforms
   for candidates with a live posting timestamp.
4. Normalize each candidate, then apply deterministic gates for direct employer,
   freshness, location/work mode, verified compensation, role core, explicit
   no-sponsorship wording, and duplicates.
5. Queue every verified match, preserving the base score, calibration adjustment,
   evidence, and source metadata in SQLite.
6. Process queued jobs in calibrated-score order through packet preparation,
   evidence-backed resume selection, fact-gated application filling, and atomic
   state tracking.
7. Reconcile Outlook job updates as metadata-only events. Use known outcomes to
   calibrate source and role-family scoring after enough observations exist.
8. Record stage effort and barriers in local SQLite. Repeated failures or budget
   overruns become temporary learned rules that switch, hand off, or skip the
   known-bad path on later runs.

Initialize the local store once after updating the code. Import does not modify
the current CSV files:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_to_sqlite.py --import-csv
.\.venv\Scripts\python.exe scripts\resume_evidence.py init
.\.venv\Scripts\python.exe scripts\scheduled_reconcile.py configure --interval-minutes 240
```

The discovery coordinator can use bounded Codex subagents for public scouting,
role-fit review, and packet auditing. They return advisory JSON only; the local
coordinator remains the sole writer of private state and the only browser actor.
See `docs/subagent_workflow.md`.

The adaptive effort loop is documented in
[`docs/process_optimization.md`](docs/process_optimization.md). Run
`scripts\jobctl.ps1 optimize` to import recognizable historical barriers and
refresh the local recommendations report.

## Windows Local Automation

Use `scripts/local_automation.py` as the Windows-local maintenance entrypoint.
Its default wake interval is 30 minutes, with independent two-hour discovery
and four-hour Outlook cursors. It coordinates SQLite maintenance, metadata-only
Outlook events, CSV export, optional read-only discovery reporting, tracker
validation, and dashboard refresh. It has no browser or submit action. See
[`docs/local_automation.md`](docs/local_automation.md) for the ignored local
configuration, dry-run, logging, concurrency lock, retry policy, and Task
Scheduler install/status/uninstall commands.

## Resume Inputs

Put your master resume here:

```text
resumes/master.docx
```

Build the local single-column ATS-safe base resume after changing the master:

```powershell
.\.venv\Scripts\python.exe scripts\build_ats_resume.py --update-manifest
.\.venv\Scripts\python.exe scripts\check_one_page.py --docx resumes\master_ats.docx --outdir outputs\master_ats_check
```

The original master remains untouched. Future application packets use the
ATS-safe role-family base selected by the private manifest. Each packet also
contains `ai_selection_report.json`, which records parser risk, must-have/core/
nice-to-have criteria, supported versus transferable evidence, weighted
coverage, and placement priorities. This report optimizes observable ATS and
AI-review behavior; it does not claim to reproduce a proprietary vendor score.

Put job text in:

```text
jobs/job.txt
```

Run a suggestion-only pass from pasted job text:

```bash
python scripts/tailor.py \
  --resume resumes/master.docx \
  --job jobs/job.txt \
  --profile profile/facts.md \
  --out outputs/tailored.docx \
  --dry-run
```

Or run from a job post link:

```bash
python scripts/tailor.py \
  --resume resumes/master.docx \
  --job-url "https://example.com/job-post" \
  --profile profile/facts.md \
  --out outputs/tailored.docx \
  --dry-run
```

Some job boards block automated fetching or require login. If that happens, paste the job description into `jobs/job.txt` and use `--job jobs/job.txt`.

## Job Search Queue

SQLite is the local source of truth at `data/resume_optimizer.db`; it is ignored
by Git. `jobs/queue.csv` and `tracker/applications.csv` are generated compatibility
exports for existing scripts and the dashboard. Run the rolling maintenance step
before discovery:

```powershell
.\.venv\Scripts\python.exe scripts\queue_maintenance.py --expire-stale --capacity 10 --low-watermark 3
```

Use `refill_recommended: true` or `available_slots` to decide how many verified
candidates to find. Existing `batch-status` commands remain available for legacy
batch runs.

Keep jobs you want to evaluate in a local queue:

```bash
python scripts/job_queue.py add \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --source "LinkedIn" \
  --url "https://example.com/job" \
  --priority high \
  --batch-id "2026-06-06-01" \
  --match-score 86
```

List or inspect the queue:

```bash
python scripts/job_queue.py list
python scripts/job_queue.py next
python scripts/job_queue.py batch-status --target-size 10
```

The real queue lives at `jobs/queue.csv` and is ignored by Git. Use `jobs/queue.example.csv` as the portable template.

For real candidates, use the role-core queue gate after saving the live job
description locally. It writes a private screening record and refuses to queue
staffing posts, explicit no-sponsorship posts, GRC/compliance-operations roles,
and roles without clear data/analytics engineering work:

```bash
python scripts/queue_screened_job.py \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --source "Greenhouse" \
  --url "https://example.com/job" \
  --job jobs/example_co_senior_data_engineer.txt \
  --batch-id "2026-07-10-01"
```

The regular `automation_pipeline.py` repeats the same role-core gate before
match scoring and tailoring, so legacy or manually-added queue rows cannot
reach resume generation without a screening artifact.

When one application state change should update both the queue and tracker, use:

```bash
python scripts/application_state.py \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --status application_started \
  --source "LinkedIn" \
  --url "https://example.com/job"
```

## Automated Search Pipeline

The project supports a staged automation loop:

1. Build LinkedIn search URLs from local private criteria.
2. Review LinkedIn results in a logged-in browser and add promising job URLs to `jobs/queue.csv`.
3. Score queued jobs by resume/profile keyword evidence, detected base pay, seniority, and post recency.
4. Prepare high-match application packets and proposed resume edits.
5. Review proposed resume edits in chat before generating the tailored resume.
6. Use browser-assisted form filling for the application, with gated auto-approval from private facts.
7. Track status in `tracker/applications.csv`.

Generate a LinkedIn search URL:

```bash
python scripts/linkedin_search.py
```

Open the generated searches directly without passing ampersands through
PowerShell or `cmd.exe`:

```powershell
.\.venv\Scripts\python.exe .\scripts\linkedin_search.py --open
```

Do not replace query separators (`&`) with `%26` or `&amp;`. That makes
LinkedIn interpret location and filters as part of the keyword phrase.

The command reads `profile/search_criteria.md` when that private file exists. Use CLI flags only when you want to override the local criteria for a one-off run.

LinkedIn discovery is intentionally browser-assisted. Use your own logged-in browser session, respect site controls, and add job URLs to the local queue instead of scraping around login or anti-bot protections.

Scan supported ATS feeds into the local queue:

```bash
cp profile/portals.example.yml profile/portals.yml
python scripts/ats_scan.py
```

The scanner supports Greenhouse, Ashby, Lever, and Workday public job feeds.
Greenhouse and Ashby use `board` slugs, Lever uses a `site` slug, and Workday
requires the employer's public CXS `api` URL plus its career-site `site` name.
Keep the real `profile/portals.yml` local because it can reveal target companies
and search strategy.

Score one job description or URL:

```bash
python scripts/match_score.py \
  --resume resumes/master.docx \
  --job-url "https://example.com/job-post"
```

Process queued jobs through the high-match gate:

```bash
python scripts/automation_pipeline.py \
  --resume resumes/master.docx \
  --min-score 75
```

When `profile/search_criteria.md` exists, `linkedin_search.py`, `match_score.py`, and `automation_pipeline.py` read local discovery filters and target pay from that private file. You can still override them with CLI flags.

High-match jobs are moved to `analyzed` and get an application packet under `applications/`. Low-match jobs are marked `rejected_low_match` in the queue and `rejected` in the tracker with the score reason.

## Tracker Maintenance

Inspect tracker status:

```bash
python scripts/tracker_tools.py status
```

Normalize common status aliases, remove duplicate rows, set follow-up dates, and list overdue follow-ups:

```bash
python scripts/tracker_tools.py normalize
python scripts/tracker_tools.py dedup
python scripts/tracker_tools.py set-followups --days 7
python scripts/tracker_tools.py overdue
```

Validate tracker structure and local file references:

```bash
python scripts/verify_tracker.py
```

Export the current normalized state back to compatibility CSVs after a repair or
manual SQLite inspection:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_to_sqlite.py --export-csv
.\.venv\Scripts\python.exe scripts\outcome_metrics.py
```

Archive stale, unsubmitted queue and tracker items before starting a new search
cycle. Submitted applications and active interview or offer records are not
changed by the default status set:

```bash
python scripts/archive_unsubmitted.py --apply
```

## Application Pipeline

Prepare an application packet from a job URL:

```bash
python scripts/run_application_pipeline.py \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --job-url "https://example.com/job" \
  --resume resumes/master.docx \
  --llm-provider codex
```

This creates the application folder, stores the job description, runs fit analysis, writes `proposed_edits.json`, updates the tracker to `analyzed`, and stops for Codex review. The default makes no Azure request; actual rewrite tailoring stays fact-bound in the local Codex session.

To force local models after running `. .\scripts\use_local_llm.ps1`, pass:

```bash
--llm-provider local
```

After you approve edits, save them as an accepted-edits JSON file and run:

```bash
python scripts/run_application_pipeline.py \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --job-url "https://example.com/job" \
  --resume resumes/master.docx \
  --accepted-edits applications/Example_Co_Senior_Data_Engineer/accepted_edits.json
```

That generates the tailored resume, runs the one-page check, copies the final DOCX into `tailored_resumes/`, and updates `tracker/applications.csv` to `resume_ready`.

Apply accepted edits from a JSON file:

```bash
python scripts/tailor.py \
  --resume resumes/master.docx \
  --job jobs/job.txt \
  --profile profile/facts.md \
  --accepted-edits outputs/accepted_edits.json \
  --out outputs/tailored.docx
```

Convert and check one-page PDF:

```bash
python scripts/check_one_page.py --docx outputs/tailored.docx
```

Run the tracker as a live local dashboard:

```bash
python scripts/live_tracker.py
```

Open `http://127.0.0.1:8765/`. The page remains local and refreshes
automatically when `tracker/applications.csv` changes. The dashboard includes
active applications, interview stages, follow-up actions, mailbox-linked
updates, filters, search, and inline local editing.

## Outlook Status Reconciliation

The Codex Outlook Email connector is the mailbox access layer. The local repo
does not store Microsoft credentials or mailbox tokens.

The Windows maintenance task does not read Outlook by itself. A Codex task with
the Outlook connector performs the mailbox read and passes only confirmed event
metadata to the local bridge. The bridge is idempotent by Outlook message URL,
updates the existing application row when company and role match, and skips a
repeated event instead of duplicating history.

For each run:

1. Run `python scripts/scheduled_reconcile.py manifest` to read active company
   and role names plus the last reconciliation state.
2. Search or list recent Outlook messages using company names, recruiter
   domains, and job-status terms.
3. Classify only clear outcomes: application received, interview invitation,
   interview completed, next round, offer, or rejection.
4. Record subject, received date, sender/contact, Outlook message link, and the
   resulting tracker state. Do not copy message bodies into the repo.
5. Leave ambiguous messages unchanged and surface them for review.

Record a confirmed mailbox event:

```bash
python scripts/mailbox_reconcile.py \
  --company "Example Co" \
  --role "Senior Data Engineer" \
  --event next_round \
  --received-date "2026-06-06" \
  --subject "Next interview" \
  --message-url "https://outlook.live.com/..." \
  --contact-name "Recruiter Name"
```

For scheduled reconciliation, configure the local cadence once, then have the
Codex Outlook connector pass only confirmed event metadata to the local bridge:

```powershell
.\.venv\Scripts\python.exe scripts\scheduled_reconcile.py due
.\.venv\Scripts\python.exe scripts\scheduled_reconcile.py manifest
.\.venv\Scripts\python.exe scripts\scheduled_reconcile.py apply-events --events tmp\mailbox_events.json
.\.venv\Scripts\python.exe scripts\scheduled_reconcile.py mark-checked
```

Use `mark-checked` only when the connector completed a mailbox scan and found no
clear state changes. It records a successful check without inventing an event.

`tmp/mailbox_events.json` may contain company, role, event type, received date,
subject, Outlook URL, contact, follow-up date, and notes. It must not contain an
email body. The bridge updates SQLite and refreshes both CSV exports atomically.

Personal Microsoft accounts may not support Graph full-text mailbox search.
When that occurs, use date-filtered message listing plus subject filters.

## Resume-Only Workflow

Use this narrower path when you only need a tailored resume and are not running the full search/application pipeline:

1. Run `tailor.py --dry-run`.
2. Review `outputs/suggestions.json`.
3. Copy only edits you accept into `outputs/accepted_edits.json`.
4. Run `tailor.py` with `--accepted-edits`.
5. Run `check_one_page.py`.
6. If it exceeds one page, shorten low-priority bullets instead of shrinking text aggressively.

## Application Packet Layout

Use one folder per application under `applications/`:

```text
applications/company-role/
  <Candidate_Name>_<Company>_<Role>_Resume.docx
  fit_analysis.json
  job_description.txt
  proposed_edits.json
  render_check/
    <Candidate_Name>_<Company>_<Role>_Resume.pdf
```

Cover letters and recruiter messages are optional. Create them only when explicitly requested.

Use the shared checklist instead of creating one checklist per job:

```text
applications/APPLICATION_REVIEW_CHECKLIST.md
```

Keep `outputs/` for temporary scratch files, such as `suggestions.json`, not final application packets.

## Tailored Resume Collection

Final tailored resumes are also copied into:

```text
tailored_resumes/
```

Use this folder when you just want the final resume files in one place. Each application folder remains the complete packet with fit analysis, job description, proposed edits, and render-check output.

## Tracker

The tracker source of truth is local SQLite at:

```text
data/resume_optimizer.db
```

It exports a local compatibility CSV at:

```text
tracker/applications.csv
```

Statuses:

```text
queued
analyzed
resume_ready
application_started
submitted
interview
offer
rejected
closed
outdated
```

Use `scripts/tracker.py` to update it manually when you start or submit an application.
Use `scripts/application_state.py` when the same state change should also update `jobs/queue.csv`.

## Browser-Assisted Form Filling

Create a private application-answer file from the example:

```bash
cp profile/application_answers.example.json profile/application_answers.json
```

Fill `profile/application_answers.json` with only facts you are comfortable reusing in job applications. The real file is ignored by Git.

The browser-assisted flow may prefill:

- standard contact fields
- work authorization fields when the wording exactly matches your private facts
- race, gender, veteran, and disability self-ID only from explicit values in the private file
- legal/privacy acknowledgements and final submit only when explicitly approved by `profile/application_answers.json` and consistent with `profile/facts.md`

The flow must stop for review on:

- custom essay or short-answer questions
- unusual sponsorship wording
- legal/privacy/background-check wording not covered by the private policy
- any final-submit case where entered facts are missing, ambiguous, or inconsistent with the private profile

Current policy: legal/privacy/self-ID/final-submit gates are auto-approved only when the exact answer or approval is covered by `profile/facts.md` or `profile/application_answers.json`. If a form asks for anything not covered there, stop and ask before continuing.

Generate an ATS-safe form plan from exact visible field labels before browser
filling. The plan never prints answer values and refuses missing facts, policy-
disabled categories, unfamiliar question wording, login, CAPTCHA, upload, and
submit actions:

```powershell
.\.venv\Scripts\python.exe scripts\ats_adapter.py --url "https://job-boards.greenhouse.io/example/jobs/1" --fields tmp\visible_fields.json
```

For real applications, follow the reusable runbook:

```text
docs/real_application_runbook.md
```

It captures the effective sequence, known friction points, and submit-gate checklist from the first LinkedIn Easy Apply and external ATS applications.

## Portable Setup

The GitHub repository contains only reusable code, documentation, and example
configuration. Personal facts, resumes, application history, and generated
packets stay in ignored local paths.

Bootstrap a clean Windows workstation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap_workstation.ps1
```

To move your own private state, create an authenticated encrypted archive on the
old workstation and import it after bootstrapping the new one:

```powershell
.\scripts\export_private_state.ps1
.\scripts\verify_private_state.ps1 -Archive .\backups\resume_optimizer_state_<timestamp>.rostate
.\scripts\import_private_state.ps1 -Archive D:\Transfer\resume_optimizer_state.rostate
```

See `docs/workstation_migration.md` for the complete sharing, migration,
credential-reconnection, and verification procedure. The reusable Codex
heartbeat prompt is in
`automation/reconcile-job-application-emails.prompt.md`.
