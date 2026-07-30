# Windows Local Automation

`scripts/local_automation.py` is the single safe entrypoint for the four-hour
local maintenance loop. It runs on this PC only and does not use Codex cloud
scheduling.

When the Codex `Job pipeline and remote approvals` heartbeat is active, do not
install this script as a second recurring task. The heartbeat already owns the
SQLite maintenance cadence and browser pipeline; running both adds duplicate
exports and creates avoidable queue races. Keep this script for manual recovery,
diagnostics, and environments where the Codex heartbeat is disabled.

It never opens a browser, fills an ATS form, clicks legal/privacy controls, or
submits an application. The entrypoint only coordinates the existing local
SQLite workflow:

1. Configures the SQLite record for the Outlook reconciliation cadence.
2. Expires stale unstarted queue items and reports rolling capacity.
3. Applies a metadata-only mailbox event file when one is present.
4. Exports SQLite state to the queue and tracker compatibility CSVs.
5. Imports newly recognizable historical barriers idempotently and refreshes
   the local workflow-optimization report.
6. Optionally runs an ATS **report only** scan; it does not add jobs to the
   queue. Screen live postings with `scripts/discovery.py` before queueing.
7. Optionally prepares packets, still stopping before any browser/application
   submission. This option is off by default.
8. Validates the tracker and refreshes the local dashboard.

## Configure and test

Create the ignored local configuration once:

```powershell
Copy-Item profile\local_automation.example.json profile\local_automation.json
```

The defaults run every 240 minutes and keep both discovery reporting and packet
preparation off. You may instead set any option with a
`RESUME_AUTOMATION_<UPPERCASE_SETTING>` environment variable; environment values
override the local JSON file. For example:

```powershell
$env:RESUME_AUTOMATION_ENABLE_ATS_DISCOVERY_REPORT = 'true'
```

Preview a run first. It creates no workflow records, exports, mailbox updates,
or application packets:

```powershell
.\.venv\Scripts\python.exe scripts\local_automation.py --dry-run
```

Run it once for real:

```powershell
.\.venv\Scripts\python.exe scripts\local_automation.py
```

Logs rotate locally at `logs\local_automation.log` (five 1 MB backups). A
lock at `tmp\local_automation.lock` prevents overlapping runs; a lock older than
the configured `stale_lock_minutes` is safely reclaimed. The Task Scheduler task
also uses the `IgnoreNew` multiple-instance policy.

The coordinator retries only the read-only ATS discovery report, because retrying
form, packet, or mailbox work could duplicate an externally visible action.
Failures in one maintenance step are recorded and the later independent tracker
export/report steps still run; the process exits non-zero when any step failed.

## Outlook metadata events

The Outlook connector or a manual review process may write confirmed metadata to
`tmp\mailbox_events.json`, using the existing `scheduled_reconcile.py` schema.
Do not place message bodies or attachments in this file. The coordinator renames
the file to a private `.processing` file while applying it, then archives it under
`tmp\` with a timestamp. If the bridge fails, the input filename is restored for
review/retry. Events must still be clear state transitions; silence and ambiguous
mail are not evidence of rejection.

This Windows task is intentionally not an Outlook client. It has no Microsoft
token and cannot invoke a Codex connector. The recurring Codex mailbox task must
run `scripts\scheduled_reconcile.py manifest`, inspect Outlook with date-filtered
message listing, and then either apply a metadata event file or run
`scripts\scheduled_reconcile.py mark-checked`. Repeated events are skipped by
message URL, and an event matching one existing company/role row preserves that
row's original source, job URL, resume, and application folder.

The same Codex heartbeat also owns LinkedIn job-alert discovery because it has
the Outlook connector and an interactive signed-in browser. It uses the
independent `alert-manifest` / `mark-alerts-checked` cursor, treats alert content
only as leads, and verifies live postings through `scripts/discovery.py` before
queueing or preparing packets. The Windows task continues to perform local
maintenance only; it does not read Outlook or control a browser.

## Install or remove the task

From the repository root in a normal PowerShell window:

```powershell
.\scripts\install_local_automation_task.ps1 -Action Install -EveryHours 4
```

The task is named `ResumeOptimizerLocalAutomation`, begins five minutes after
installation by default, and runs only while the signed-in Windows user is
logged in. That is intentional: local files and any later manual browser work
remain on this PC.

Check status or run the task immediately:

```powershell
.\scripts\install_local_automation_task.ps1 -Action Status
Start-ScheduledTask -TaskName ResumeOptimizerLocalAutomation
```

Disable it completely:

```powershell
.\scripts\install_local_automation_task.ps1 -Action Uninstall
```

To change cadence, set `interval_minutes` in the ignored configuration and
reinstall with the matching `-EveryHours` value. To change other behavior, edit
`profile\local_automation.json`; no task reinstall is needed. The task uses the
repository virtual environment directly, so recreate `.venv` before
reinstalling if its Python path changes.
