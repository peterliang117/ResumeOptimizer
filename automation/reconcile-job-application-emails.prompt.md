# ResumeOptimizer Reconciliation Automation

Use this prompt when recreating the Codex heartbeat on another workstation.
Run it from the repository root every 30 minutes.

## Prompt

Run the serialized ResumeOptimizer workflow in the current repository.

On every wake:

1. Run `scripts/remote_approval.py pending`. Inspect Outlook approval replies
   only when pending requests exist, and accept only exact, unexpired decision
   lines from the configured profile email.
2. Run `scripts/scheduled_reconcile.py due`. If no task is due and no approval
   changed state, remain quiet.
3. Acquire `scripts/pipeline_lock.py` before browser or state-changing work.
   Exit quietly on `active_run`, and release the exact token on every exit path.
4. For Outlook reconciliation, use date-filtered message listing ordered by
   `receivedDateTime` descending. Apply only explicit outcomes with clear
   application context. Store metadata only.
5. When `inbound_recruiter_discovery` is due, run `recruiter-manifest` and
   inspect new recruiter outreach even when no application exists. A verified
   external recruiter representing a named direct employer is eligible; reject
   contract staffing placements, unnamed clients, and cases where the
   intermediary is the employer of record. Queue accepted work under the named
   employer and canonical employer URL. Surface every unresolved lead before
   running `mark-recruiters-checked`; never advance this cursor silently.
6. For application processing, follow `docs/real_application_runbook.md`, the
   private profile files, SQLite state, and workflow-optimizer advice. Process
   existing queued work before discovery and never invent evidence or answers.
7. Discover only when the discovery cursor is due. Read
   `outputs/ats_discovery_snapshot.json` before any direct ATS scan. Accept only
   schema version 1 snapshots generated within the last 150 minutes. Treat
   `jobs` as candidate inputs that still require live verification; enforce the
   private geography, compensation, sponsorship, freshness, duplicate,
   concentration, and role-core rules. For a fresh snapshot, run
   `python scripts/verify_discovery_snapshot.py --queue --capacity 10` before
   using a browser fallback. Treat its
   `outputs/discovery_verification_report.json` as the decision ledger: every
   snapshot candidate must have an eligible or rejected record, and only jobs
   with a saved exact JD, live publication timestamp, factual score, and passed
   hard gates may be queued. A missing, stale, or `unavailable`
   snapshot is a discovery-channel failure, not evidence that there are no
   candidates. Record the validation failure and switch to the signed-in
   browser or internet connector fallback. Do not retry local ATS sockets after
   `network_access_denied` from the Codex execution environment.
8. Batch unfamiliar required questions. Use scoped approval only for true
   exceptions, and stop only the affected application for browser-only
   blockers.
9. Confirm every submission before updating state. Run `tracker_report.py` and
   `verify_tracker.py`, mark only completed cursors, and release the lock.

Report only meaningful outcome changes, inbound recruiter leads, queued jobs,
prepared packets, submissions, exception batches, handoffs, ambiguities, and
validation failures.
Never include private answer values, raw approval tokens, credentials, message
bodies, OTP codes, or attachments in notifications.

## Machine-Specific Setup

Do not put an email address, private profile value, raw token, browser profile,
or credential in this prompt. The destination workstation must supply those
through ignored profile files and its own Outlook/Chrome connections.
