# Real Application Runbook

Use this runbook before and during each real application. It captures lessons from the Addison Group LinkedIn Easy Apply case, the Carlyle external Avature case, and the Goldman Sachs Oracle ATS case.

## Standard Sequence

1. Check the current batch with
   `python scripts/job_queue.py batch-status --target-size 10`.
2. If the current batch still has open jobs, continue that batch and do not
   perform replacement discovery.
3. If no batch exists or `refill_ready=yes`, find roles using all approved job
   platforms, with LinkedIn and direct employer ATS sources included:
   - Read private criteria from `profile/search_criteria.md`.
   - If it is missing, copy `profile/search_criteria.example.md` and fill it locally.
   - Prefer roles that match the local criteria for keywords, locations, recency, pay, work mode, and sponsorship screen.
   - Open LinkedIn searches with `python scripts/linkedin_search.py --open`.
     Do not pass raw LinkedIn query URLs through `cmd.exe`, and never encode
     query separators as `%26` or `&amp;`; doing so merges every filter into the
     keyword value.
4. Hard-screen and rank candidates, then immediately queue every verified match
   with a shared `batch_id` and stored `match_score`, up to 10 jobs. A partial
   batch is valid and must not be withheld while waiting for more candidates.
5. Save each exact job description locally under `jobs/<company>_<role>.txt`.
6. Process all queued jobs in descending match-score order.
7. Identify unsupported requirements before writing edits.
8. Create a company-specific application packet under `applications/<Company>_<Role>/`.
9. Generate only truthful resume edits from `resumes/master.docx` and
   `profile/facts.md`. Automatically apply low-risk, fully evidenced edits;
   stop only for ambiguous, unsupported, or medium/high-risk edits.
10. Run the one-page check with LibreOffice before uploading.
11. For FAANG-level or comparable large technology employers, stop after the
    one-page resume and application packet are ready. Give the user the direct
    job link and local resume path, mark the job `manual_apply_needed`, and wait
    for the user to submit manually. Do not fill or submit the application.
12. For other employers, upload the company-specific resume, not an older generic resume and not a resume tailored for another company.
13. Fill forms from `profile/application_answers.json`.
14. Auto-continue through legal/privacy attestations, sensitive self-ID fields, and final submit only when the exact answer or approval is covered by `profile/facts.md` or `profile/application_answers.json`; otherwise stop for user input.
15. Update `tracker/applications.csv` after each meaningful state change and move
    immediately to the next job in the batch.
16. Reconcile Outlook messages against active applications:
    - application receipt -> `submitted`
    - interview invitation -> `interview`
    - completed interview -> keep `interview` and schedule follow-up
    - next round -> `interview` with stage `next_round`
    - offer -> `offer`
    - explicit rejection -> `rejected`
17. Store only email metadata and the Outlook link, not message bodies.
18. After all queued jobs are terminal or handed off, start the next discovery
    run and queue whatever verified matches are available, up to 10.

## Post-Application Monitoring

1. Read active tracker rows and build a focused list of company names, roles,
   recruiter contacts, and submission dates.
2. Use the Codex Outlook Email connector to inspect messages received after the
   earliest active submission.
3. Prefer focused subject filters such as `contains(subject,'Company')`.
   Personal Microsoft accounts may reject Graph full-text search.
4. Treat scheduling invitations and recruiter messages as higher-confidence
   signals than generic application portal mail.
5. Update the tracker with `scripts/mailbox_reconcile.py`.
6. For a completed recruiter or HR screen, use:
   - status: `interview`
   - stage: `recruiter_screen_completed`
   - next action: await feedback
   - follow-up date: seven calendar days after the call unless a different
     timeline was stated
7. Do not infer rejection from silence. Create a follow-up action instead.

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

1. Hard-screen before resume work:
   - direct employer, not staffing
   - NYC or Jersey City
   - posted within 24 hours when available
   - hybrid or in-office
   - no explicit "no sponsorship" language
2. Resolve the direct employer ATS URL immediately.
3. If the employer is FAANG-level or comparable big tech, prepare the verified
   one-page resume, mark `manual_apply_needed`, give the user the job link and
   resume path, and stop before form filling.
4. Reuse the existing application packet if the tailored resume already exists and has passed the one-page check.
5. Use Playwright CLI for the live form.
6. Fill required fields first and skip optional fields unless they add material value.
7. Stop only for required questions not covered by `profile/facts.md` or `profile/application_answers.json`.
8. Record the state once through `scripts/application_state.py`.

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
