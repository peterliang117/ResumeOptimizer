# Real Application Runbook

## Scheduled Local Maintenance

The Codex heartbeat is the single browser-owning coordinator. It wakes every 30
minutes to continue queued applications and remote exceptions. Direct-employer
and LinkedIn discovery has an independent two-hour cursor, accelerated to 30
minutes whenever no actionable jobs remain; Outlook outcome reconciliation runs
every four hours, and inbound recruiter discovery runs every 30 minutes. Do not
install a second browser-owning
cron or Windows task. `scripts/local_automation.py` may still be run manually
for local-only maintenance, but it must not overlap the heartbeat pipeline.

Use this runbook before and during each real application. It captures lessons from the Addison Group LinkedIn Easy Apply case, the Carlyle external Avature case, and the Goldman Sachs Oracle ATS case.

## Standard Sequence

1. Run rolling-queue maintenance with
   `python scripts/queue_maintenance.py --expire-stale --capacity 10 --low-watermark 3 --blocked-timeout-hours 24`.
   Treat SQLite (`data/resume_optimizer.db`) as the state authority. The queue
   and tracker CSV files remain generated compatibility exports. The maintenance
   transaction closes verified ATS wrapper duplicates, synchronizes terminal
   statuses to linked tracker rows, and skips blockers after 24 hours.
2. Continue ready jobs in calibrated-score order. Use
   `python scripts/job_queue.py next` for packet work and
   `python scripts/job_queue.py next-application` for `resume_ready` or
   `application_started` browser work. When `refill_recommended` is true,
   search enough candidates to fill `available_slots`; do not wait for a closed
   batch to be exhausted. Blocked or handoff-only jobs count toward capacity but
   do not suppress refill when the actionable queue is at or below the low
   watermark.
3. Find roles using all approved job
   platforms, with LinkedIn and direct employer ATS sources included:
   - Read private criteria from `profile/search_criteria.md`.
   - If it is missing, copy `profile/search_criteria.example.md` and fill it locally.
   - Prefer roles that match the local criteria for technical scope, locations,
     recency, pay, work mode, and sponsorship screen. Do not reject a role only
     because the title is adjacent if the live posting shows a strong technical
     stack match.
   - Open LinkedIn searches with `python scripts/linkedin_search.py --open`.
     Do not pass raw LinkedIn query URLs through `cmd.exe`, and never encode
     query separators as `%26` or `&amp;`; doing so merges every filter into the
     keyword value.
4. For ATS snapshots, run
   `python scripts/verify_discovery_snapshot.py --queue --capacity 10`. It
   saves the exact JD under `jobs/source/`, writes candidate and review records,
   and writes a decision for every input to
   `outputs/discovery_verification_report.json`. For browser or connector
   candidates, save each exact job description locally under
   `jobs/<company>_<role>.txt`, then run `scripts/discovery.py` with the
   candidate metadata. The shared gate rejects a
   candidate unless a direct-employer URL, live timestamp, location/work mode,
   compensation, role core, sponsorship screen, minimum score, and
   deduplication check pass.
5. Queue every verified candidate. Preserve its base score, outcome calibration,
   evidence, and role family. A partial refill is valid.
6. Identify unsupported requirements before writing edits.
7. Create a company-specific application packet under `applications/<Company>_<Role>/`.
8. Generate only truthful resume edits from `resumes/master.docx` and
   `profile/facts.md`. Automatically apply low-risk, fully evidenced edits;
   stop only for ambiguous, unsupported, or medium/high-risk edits.
   Use `scripts/resume_evidence.py validate` before applying machine-generated
   edits, and select the evidence-backed role-family variant in the application
   pipeline.
   Review `ai_selection_report.json` before accepting edits. The resume must
   maximize supported must-have and core criterion visibility, preserve
   transferable/unsupported gaps, and pass the parser audit. Rebuild the local
   single-column base with `scripts/build_ats_resume.py --update-manifest` after
   material master-resume changes.
9. Run the one-page check with LibreOffice before uploading.
10. Apply the same exact-fact automation policy to every verified direct
    employer, including large technology companies. Employer size alone is not
    a manual-submit gate.
11. Upload the company-specific resume, not an older generic resume and not a resume tailored for another company.
12. Use `scripts/ats_adapter.py` to map only exact, policy-authorized form
    labels from `profile/application_answers.json`. Treat every unfamiliar label
    as a manual-review gate.
13. Auto-continue through legal/privacy attestations, sensitive self-ID fields, and final submit only when the exact answer or approval is covered by `profile/facts.md` or `profile/application_answers.json`; otherwise stop for user input.
14. If autofill, upload, browser access, account login, CAPTCHA, required user
    interaction, or submit cannot be completed automatically, stop with a
    manual handoff that includes the direct application link, company and role,
    local resume path, exact blocker, completed answers, unanswered required
    fields, and current tracker status. Never hand off a blocked application
    without the link the user can open.
15. Update state through `scripts/application_state.py` after each meaningful
    change. It writes the job, application, and event in one SQLite transaction,
    then refreshes both CSV exports.
16. Reconcile Outlook messages against active applications:
    - application receipt -> `submitted`
    - interview invitation -> `interview`
    - completed interview -> keep `interview` and schedule follow-up
    - next round -> `interview` with stage `next_round`
    - offer -> `offer`
    - explicit rejection -> `rejected`
17. Store only email metadata and the Outlook link, not message bodies. Run
    `scripts/outcome_metrics.py` periodically; it calibrates source and
    role-family scores only after enough outcomes are observed.
18. Continue application work and rolling discovery until capacity is filled.

## Post-Application Monitoring

1. Read active tracker rows and build a focused list of company names, roles,
   recruiter contacts, and submission dates.
2. Use the Codex Outlook Email connector to inspect messages received after the
   date returned by `scripts/scheduled_reconcile.py manifest`.
3. Prefer focused subject filters such as `contains(subject,'Company')`.
   Personal Microsoft accounts may reject Graph full-text search.
4. Treat scheduling invitations and recruiter messages as higher-confidence
   signals than generic application portal mail.
5. Update the tracker with `scripts/mailbox_reconcile.py`.
   For recurring runs, write metadata-only events to `tmp/mailbox_events.json`
   and apply them with `scripts/scheduled_reconcile.py apply-events`. If no
   clear event exists, run `scripts/scheduled_reconcile.py mark-checked`.
6. For a completed recruiter or HR screen, use:
   - status: `interview`
   - stage: `recruiter_screen_completed`
   - next action: await feedback
   - follow-up date: seven calendar days after the call unless a different
     timeline was stated
7. Do not infer rejection from silence. Create a follow-up action instead.

## Inbound Recruiter Lead Discovery

Keep new recruiter opportunities separate from post-application monitoring.

1. Run `scripts/scheduled_reconcile.py recruiter-manifest` and list Outlook
   messages received after its `since_datetime`, ordered newest first.
2. Identify outreach that names a hiring employer and role, even when no tracker
   row exists. Verify the sender or recruiting firm and corroborate the role
   through the named employer, official ATS, or direct company/recruiter evidence.
3. A verified external recruiter representing a named direct employer is an
   eligible source. Reject contract staffing placements, unnamed-client roles,
   and cases where the intermediary is the employer of record.
4. Apply the normal truth, location, compensation, sponsorship, role-core, and
   employer-concentration gates. Queue accepted work under the named employer
   and canonical employer URL, not under the recruiting intermediary.
5. Store metadata only and do not alter mailbox state. Surface unresolved leads
   and validation failures before running
   `scripts/scheduled_reconcile.py mark-recruiters-checked`; never advance the
   cursor while a reachable recruiter message remains silently unclassified.

## Scheduled Outlook Job-Alert Discovery

Keep this path separate from post-application monitoring. A LinkedIn alert is a
discovery lead, never evidence that an application was received, advanced, or
rejected.

1. Run `scripts/scheduled_reconcile.py alert-manifest` and list Outlook messages
   received after its `since_datetime`. Filter for LinkedIn job-alert senders;
   do not use Microsoft Graph full-text search on the personal account.
2. Extract only public job metadata: LinkedIn job ID/direct URL, company, role,
   location, work mode, and alert receipt time. Do not store the email body,
   SafeLink, attachment, credential, or token, and do not alter mailbox state.
3. Deduplicate against SQLite and within the alert. Treat near-identical roles
   at the same employer as one lead unless the postings are materially distinct.
4. Open each live posting and verify the normal hard gates: direct employer,
   current availability, posted/reposted within seven days, location/work mode,
   compensation, role-core fit, employer concentration, and sponsorship. Silence
   about sponsorship remains eligible; explicit no-sponsorship wording is a
   rejection.
5. Queue every verified match, even when fewer than 10 survive. Never use alert
   receipt time as the posting timestamp and never infer missing salary.
6. Run the normal packet pipeline in calibrated-score order using local Codex.
   For verified direct-employer applications, scheduled runs may upload, fill, and
   submit without per-job approval only when every required value and consent is
   exactly covered by the private facts and enabled answer policy.
7. Apply the adaptive attempt budget at every live-posting and ATS stage. When a
   posting is stale, blocked, duplicated, or taking too long, record the reason
   and continue to the next lead.
8. After processing the alert slice, run
   `scripts/scheduled_reconcile.py mark-alerts-checked`, refresh the tracker, and
   verify it. A repeated run remains safe because SQLite deduplication rejects
   already-active URLs and company/role identities.

## Remote Approval By Outlook

When the user is away from the Codex desktop, use Outlook approval only for an
exception that is not already covered by the standing private answer policy.

1. Standard exact-fact resume upload, form fill, legal/privacy controls, self-ID,
   and final submit need no per-job approval when their corresponding policy
   flags are enabled. Never extend this to an inferred or approximate answer.
2. Inspect all reachable form steps before requesting input. For unfamiliar
   required answers, ambiguous/conflicting facts, unusual consent, or a material
   form change, create one answer-scope request per exact question. Keep all
   distinct questions for the same job pending and send them together in one
   numbered message with fact-grounded proposed answers when available.
3. The user may answer the numbered batch directly in the current Codex task or
   reply by Outlook. For Outlook, accept each non-quoted decision line only when
   it exactly matches one pending token, the sender exactly matches the
   configured address, and the token is unexpired. Record every accepted line
   separately with `remote_approval.py decide`. Ambiguous combined replies
   authorize nothing.
4. Immediately before each exceptional action, call `remote_approval.py consume`
   with the same company, role, canonical URL, scope, and exact question for
   answer requests. A mismatch, ambiguity, expiry, or previously consumed token
   blocks the action.
5. Tokens expire after 12 hours by default, are single-use, and are stored only
   as SHA-256 hashes in local SQLite. Never include private facts, application
   answers, full resume contents, or credentials in approval email bodies.
6. Accept only these first-line phone replies: `APPROVE <token>` to accept the
   proposed answer/action, `YES <token>`, `NO <token>`,
   `ANSWER <token>: <answer>`, or `SKIP <token>`. The answer variants are valid
   only for answer-scope tokens. Any other reply authorizes nothing.

Use these fixed Outlook subject prefixes:

- `[ResumeOptimizer Approval] TRANSMIT - <Company> - <Role>` for a scoped
  transmission exception token.
- `[ResumeOptimizer Approval] SUBMIT - <Company> - <Role>` for a scoped final
  submission exception token.
- `[ResumeOptimizer Input Needed] <Company> - <Role>` for an uncovered required
  answer.
- `[ResumeOptimizer Browser Action Needed] <Company> - <Role>` for login, OTP,
  CAPTCHA, or another browser-only blocker.
- `[ResumeOptimizer Submitted] <Company> - <Role>` after automatic submission.
- `[ResumeOptimizer Pipeline Alert] <Stage>` only for a validation or automation
  failure requiring attention.

Do not send an email when no action or meaningful state change exists. Keep
approval tokens in the message body, never in the subject line.

## Cadenced Full Pipeline

1. On a due cycle, acquire `scripts/pipeline_lock.py acquire`. If another run
   owns the lock, exit quietly; never run two browser actors.
2. Reconcile explicit Outlook outcomes, then maintain and process existing queue
   items before replacement discovery.
3. Refill available capacity up to 10 from LinkedIn alerts, LinkedIn live search,
   and verified direct-employer Greenhouse, Ashby, Lever, Workday, and careers
   pages. Partial verified refills are valid.
4. Screen, tailor, validate, apply, and record each role serially. Use local
   Codex, exact private facts, attempt budgets, employer-concentration rules, and
   duplicate gates.
5. Auto-submit any verified direct-employer application only when every required field and
   consent is covered by enabled private policy. Send a concise completion email
   after submission instead of requesting approval beforehand.
6. For an uncovered required field, send one answer-scope email that can be
   resolved with a short phone reply. For authentication/CAPTCHA or a changed
   form, send one actionable browser handoff with the direct link and continue
   the queue.
7. Refresh and verify the tracker, mark the pipeline cursor only after the cycle
   completes, and release the exact lock token on every exit path.

## Adaptive Attempt Budget

Before a failure-prone stage, consult the local learner and start a timed
attempt:

```powershell
.\.venv\Scripts\python.exe scripts\workflow_optimizer.py advise --stage ats_fill --platform greenhouse
.\.venv\Scripts\python.exe scripts\workflow_optimizer.py start --stage ats_fill --platform greenhouse
```

Keep the returned attempt ID and finish it after the stage. Include the actual
outcome and interaction count. When application state changes at the same time,
pass the attempt ID or barrier fields to `scripts/application_state.py` so the
queue, tracker, and effort record stay aligned.

The full budgets and barrier actions are in `docs/process_optimization.md`.
Elapsed time and interaction count are the standard proxies for token cost.
Do not exceed a stage budget to keep diagnosing one posting. If the learner
returns `use_codex`, `open_direct_ats`, `manual_handoff`, `move_on`, or
`skip_candidate`, follow that action without re-proving the failure.

## Fast Path

Use this as the default path for public ATS applications.

1. Prefer the direct ATS page over the LinkedIn wrapper as soon as you can identify it.
2. For public ATS forms, prefer Playwright CLI over Codex browser plugins:
   - `npx.cmd --yes --package @playwright/cli playwright-cli open <url> --headed`
   - use `snapshot` before each interaction block
   - use `upload` for resume files
3. Treat the browser-plugin path as optional. If Codex browser helpers are broken, do not spend more time retrying them once the failure mode is known.
4. Fill only required fields first. Leave optional fields for the end or skip them when not useful.
5. Answer custom dropdowns from private facts only. If a required option is not clearly covered, stop and ask once.
6. Before submit, take one validation snapshot and fix only the remaining required fields.
7. Update queue and tracker together with `scripts/application_state.py` instead of editing both files separately.
8. For external roles, move to the employer ATS URL as soon as it is known and prefer tracking that final URL.
9. If the fast path stops before submit for any reason, give the user the exact
   application URL plus the local resume path and blocker before moving on.

## What Worked Well

- LinkedIn browser-assisted discovery worked better than raw scraping. The logged-in Chrome session exposed salary filters, recent-post filters, job cards, and external apply links.
- For Easy Apply, LinkedIn prefilled contact fields from the profile. The main risk was that it selected an older resume by default.
- For external Avature applications, uploading the tailored DOCX through a real file chooser worked after Chrome file access was enabled.
- LibreOffice page verification was essential. The first Addison resume exceeded one page; shortening approved edits and rerunning the check caught and fixed it before upload.
- Keeping `profile/facts.md` and `profile/application_answers.json` private worked well. They became the source of truth for authorization, sponsorship, self-ID, and reusable application facts.
- Fact-bound edit validation prevented unsupported claims, especially around
  Snowflake, Databricks, dbt, Airflow, LLM/RAG, and other tools not supported by
  the resume.
- The Goldman run confirmed that the tracker should be updated at every handoff state: `analyzed`, `resume_ready`, `application_started`, `blocked_needs_user_input`, and `submitted`.
- Saving address fields in `profile/application_answers.json` reduced repeated stops once the user confirmed the correct local address.

## Inefficient Steps To Avoid

- Do not burn tokens proving the Codex browser plugins are still broken once the failure mode is known. For public ATS flows, switch directly to Playwright CLI.
- Do not diagnose a barrier that already has an active learned rule. Record the
  outcome, apply the recommended switch/handoff/skip action, and continue.
- Do not spend tokens reopening wrapper pages once the direct employer ATS URL is known.
- Do not start with a full-page exploratory dump when a targeted `snapshot` after each state change is enough.
- Do not update `jobs/queue.csv` and `tracker/applications.csv` separately for the same state change. Use `scripts/application_state.py`.
- Do not submit before validating the phone country selector on Greenhouse-style forms. It can look filled while still failing required validation.
- Do not stop on optional profile links, websites, cover letters, or demographic sections unless they are required or explicitly need user review.
- Do not rely blindly on `match_score.py` when the job description contains unsupported tools. Keyword matching can overstate fit if unsupported terms appear in "do not claim" sections or job requirements.
- Do not run the full pipeline with `--accepted-edits` when an API key is visible but quota is unavailable. If edits are already approved, run `scripts/tailor.py --accepted-edits` directly to avoid unnecessary API calls.
- Do not upload the first selected LinkedIn resume. LinkedIn may select an old file by default.
- Do not use a resume tailored for one employer on another employer's application. Create a packet and resume per company.
- Do not keep trying brittle text-entry methods on ATS fields. If normal fill/type fails, use user-visible fields when possible, but stop rather than forcing optional fields.
- Do not claim Snowflake/Databricks/dbt/Airflow/LLM/RAG experience unless those
  facts are explicitly supported by `profile/facts.md`.
- Do not automatically reject a role solely because it requires dbt or
  Snowflake. Treat those as transferable tool gaps when the underlying work is
  SQL transformation, data modeling, ETL/ELT, testing, data quality,
  orchestration, governance, or warehouse delivery supported by the private
  facts. Preserve the gap in the fit review and never convert workflow
  equivalence into a claim of hands-on tool experience.
- Do not assume ATS address autocompletes will accept free text. Oracle required selecting internal ZIP/city/state/county records; simple fill calls did not always commit values.
- Do not continue past uploaded-resume checks without verifying the exact filename. In Goldman, the wrong tailored resume was uploaded once and had to be removed.
- Do not let browser helper overlays such as Simplify hide upload controls or influence decisions. Close overlays before inspecting or filling the ATS.

## Easy Apply Pattern

Use this for LinkedIn Easy Apply roles like Addison Group.

1. Open the LinkedIn job page.
2. Click Easy Apply and inspect all fields before making changes.
3. Confirm contact fields:
   - email
   - country code
   - phone
4. On the resume step, verify the selected resume filename.
5. Upload the company-specific resume if the selected resume is old or generic.
6. Continue through each step only when fields are supported by the private profile.
7. Stop only for:
   - sponsorship questions not exactly covered by `profile/application_answers.json`
   - legal or privacy acknowledgement not covered by the private policy
   - custom essay questions that need new content
   - final submit when `allow_final_submit` is false or entered facts differ from the private profile

Addison-specific lesson: LinkedIn selected `Resume_ZICONG_LIANG_042026.docx` by default. Always replace it with the tailored resume.

## External Apply Pattern

Use this for company-site applications like Carlyle Avature.

1. From LinkedIn, click the `Apply` or `Apply on company website` button.
2. Record the external URL in the job file and tracker.
3. Inspect the ATS before filling.
4. If the ATS starts with resume upload, upload the company-specific one-page DOCX.
5. Let the ATS parse the resume, then correct parsed fields.
6. Fill only known facts:
   - contact
   - city/state/country
   - current employer and role
   - education
   - work authorization and sponsorship if in private facts
   - self-ID only if explicitly in private facts
7. Leave optional fields blank if automation is brittle and the field is not required.
8. Final submit is allowed only when `profile/application_answers.json` explicitly permits it and every required answer is covered by private facts. Otherwise stop for a concise review.

Carlyle-specific lesson: Avature parsed some fields but required manual correction. It also required privacy consent, authorization/sponsorship answers, relocation preference, compensation expectation, and self-ID. These must come from the user/private facts, not inference.

## Oracle ATS Pattern

Use this for Oracle Candidate Experience sites like Goldman Sachs.

1. Start from the LinkedIn job page, then click `Apply on company website`.
2. Save the Oracle job URL and local job description before applying.
3. Enter email and accept standard terms only when covered by `profile/application_answers.json`.
4. Expect a one-time email verification code; stop and let the user enter it.
5. On the personal-info step, always verify the attached resume filename. Remove any old or wrong file before continuing.
6. Upload the company-specific one-page DOCX manually if the browser runtime cannot set the file input.
7. Close browser helper overlays before inspecting fields.
8. Fill address fields from `profile/application_answers.json`; for Oracle comboboxes, prefer selecting the site-provided ZIP/city/state/county records over free typing.
9. If self-ID consent, gender identity, sexual orientation, transgender identity, disability, or other sensitive questions are not explicitly covered, stop and request user input.
10. After the user takes over or completes a blocked section, update the tracker immediately with the true final status.

Goldman-specific lesson: Oracle prefilled an old resume and later accepted the wrong application-specific resume when uploaded manually. The exact uploaded filename must be checked before advancing. The ATS also asked for sensitive self-ID consent and transgender identity values not covered by the private facts, so automation correctly stopped and the user took over the remaining flow.

## Optimized Decision Order

Use this order to reduce wasted tokens and avoid blocked paths:

1. Check `scripts/workflow_optimizer.py advise` before entering a previously
   unreliable stage or platform. Follow any active learned action.
2. Hard-screen before resume work:
   - save the live job description and record `screening.json` with
     `scripts/queue_screened_job.py` before adding a new queue row
   - direct employer, not a contract staffing placement or unnamed client;
     verified external recruiter outreach for a named direct employer is eligible
   - NYC, Jersey City, or remote U.S. where locally allowed
   - posted within seven days when available; prioritize the newest 72 hours
   - hybrid, in-office, or remote according to local criteria
   - no explicit "no sponsorship" language
   - sponsorship silence is an eligible, non-blocking state; do not pause the
     workflow unless the live posting explicitly rejects sponsorship or the
     application asks wording not covered by the private facts
   - technical work and salary expectations match the private criteria, even if
     the title is adjacent rather than a literal data-engineering title
   - for cyber/GRC/risk/compliance roles, confirm the core deliverable is a
     data or analytics engineering product: SQL/Python pipelines, data models,
     data quality controls, governed BI/reporting layers, KPI/KRI metric logic,
     warehouse delivery, or automated analytics/reporting
   - reject roles where the core work is compliance program operations, audit
     readiness, evidence collection, policy/procedure ownership, access-review
     administration, control attestations, POA&M tracking, framework mapping,
     or auditor coordination, even if salary/location/domain look attractive
3. Resolve the direct employer ATS URL immediately.
4. Reuse the existing application packet if the tailored resume already exists and has passed the one-page check.
5. Start a timed ATS attempt, then use Playwright CLI for the live form.
6. Fill required fields first and skip optional fields unless they add material value.
7. Stop only for required questions not covered by `profile/facts.md` or `profile/application_answers.json`.
   Any stop must include the direct application URL so the user can continue
   through a short remote answer or, when unavoidable, manually.
8. Finish the timed attempt and record state once through
   `scripts/application_state.py`. If the budget is exhausted, hand off and
   continue the queue.

## Optimization Backlog

- Add a local helper that resolves common direct-apply targets from job board pages so we stop spending time on wrapper pages.
- Add portal-specific answer maps for repeated Greenhouse custom questions where the wording recurs exactly.
- Add an ATS field-audit helper that summarizes required fields and whether each is covered by `profile/application_answers.json` before filling.
- Add a resume-upload verification step that fails closed unless the attached filename contains the target company slug.
- Add a local address parser/checker for `address_line_1`, `city`, `state`, `postal_code`, and derived county, while keeping values private.
- Improve `match_score.py` so unsupported terms from private `Do not claim` notes do not inflate fit scores.
- Extend `scripts/ats_scan.py` beyond Greenhouse to Lever, Ashby, and Workday-style feeds where public APIs are available.
- Build a richer evaluation report that separates hard filters, unsupported gaps, compensation, posting legitimacy, and interview prep.

## Private Fact Defaults

Use `profile/facts.md`, `profile/application_answers.json`, and `profile/search_criteria.md` as the active local sources of truth.

Public templates live in:

- `profile/facts.example.md`
- `profile/application_answers.example.json`
- `profile/search_criteria.example.md`
- `profile/portals.example.yml`

System/private boundaries are documented in `DATA_CONTRACT.md`.

Do not copy private search criteria, work authorization details, sponsorship status, compensation targets, demographic self-ID values, or application answers into public docs.

Ask before using:

- Any value missing from the private files.
- Any wording that differs from the private files.
- Any legal attestation, privacy acknowledgement, background-check consent, or final submit that is not explicitly approved by `profile/application_answers.json`.

## Pre-Submit Checklist

Before final submit, summarize internally or in chat:

- company and role
- application URL
- resume filename uploaded
- salary/compensation entered
- relocation answer
- authorization and sponsorship answers
- self-ID answers entered
- unsupported fit gaps that remain
- any optional fields left blank

If every item is covered by private facts and `allow_final_submit` is true, final submit may proceed. If any item is missing, ambiguous, or inconsistent, stop and ask the user.
