---
name: resume-optimizer
description: Use for the local job application pipeline: private-criteria job discovery, match scoring, truthful one-page resume tailoring, browser-assisted application filling, fact-gated approvals, and local application tracking.
---

# Resume Optimizer

Use this workflow to run the local, privacy-first job application pipeline.

## Rules

- Preserve the source DOCX as the formatting base.
- Never invent employers, dates, degrees, tools, metrics, certifications, or responsibilities.
- Only propose edits supported by the resume or `profile/facts.md`.
- Mark unsupported-but-possibly-relevant requirements as confirmation questions.
- Prefer rewriting existing bullets over adding new bullets.
- Keep the final resume to one page.

## Workflow

1. Read the resume, job description or job URL, and profile facts.
2. For full lifecycle work, add or read the job from `jobs/queue.csv` using `python scripts/job_queue.py`.
3. For real applications, read `docs/real_application_runbook.md` before applying or filling forms.
4. For job discovery, read private search rules from `profile/search_criteria.md`; if it is missing, use `profile/search_criteria.example.md` only as a template and ask the user for the missing local criteria.
5. Run `python scripts/run_application_pipeline.py ...` to create the application packet, store the job description, generate `proposed_edits.json`, create `fit_analysis.json`, and update `tracker/applications.csv` to `analyzed`. The default LLM mode is Azure-first when `AZURE_OPENAI_*` settings exist, otherwise conservative fallback for Codex/manual tailoring.
6. For direct resume-only work, run `python scripts/tailor.py --dry-run ...` to generate `outputs/suggestions.json`. Use `--llm-provider azure` to force Azure or `--llm-provider none` to force local fallback. The personal OpenAI API path is disabled for this workflow.
7. Create or inspect `proposed_edits.json`.
8. Automatically approve edits only when every edit:
   - has `truth_risk` set to `low`
   - is fully supported by `resumes/master.docx` or `profile/facts.md`
   - does not add an unsupported skill, tool, employer, date, metric,
     certification, responsibility, or domain claim
9. If any edit is medium/high risk, ambiguous, or lacks local evidence, stop and
   ask the user about only those edits.
10. Create `accepted_edits.json` from the fact-bound low-risk edits.
11. Run `python scripts/run_application_pipeline.py ... --accepted-edits ...` for full lifecycle work, or `python scripts/tailor.py --accepted-edits ...` for direct resume-only work.
12. Run or verify `python scripts/check_one_page.py --docx ...`.
13. If page count exceeds one, shorten low-priority edits and check again.
14. Update `tracker/applications.csv` as the job moves from `resume_ready` to `application_started`, `submitted`, `interview`, `offer`, `rejected`, or `closed`.
15. Reconcile recent Outlook job-update messages against active tracker rows.
    Store only message metadata and the derived state, never the email body.

## Resume Approval Policy

The user has pre-approved automatic application of low-risk, fact-bound resume
edits. Chat review is required only for edits that are ambiguous, unsupported,
or medium/high risk. Visual/layout review still happens after generation through
the LibreOffice page-count check.

## Application Packet Defaults

By default, keep each application packet lean:

- `Zicong_Liang_<Company>_<Role>_Resume.docx`
- `fit_analysis.json`
- `job_description.txt`
- `proposed_edits.json`
- `render_check/Zicong_Liang_<Company>_<Role>_Resume.pdf` after LibreOffice verification

Do not create a cover letter, recruiter message, or per-job submit checklist unless the user explicitly asks for those artifacts for that job.

Maintain one shared review checklist at `applications/APPLICATION_REVIEW_CHECKLIST.md` instead of creating a checklist in every application folder.

Use ASCII filename slugs: replace spaces and punctuation with underscores, remove duplicate underscores, and keep the candidate name prefix `Zicong_Liang`.

After generating and verifying a tailored resume, also copy the final DOCX into `tailored_resumes/` using the same standardized filename. This folder is the quick-access collection of all final tailored resumes; the application folder remains the full packet source of truth.

## Job Search Automation Scope

Use a repeating batch lifecycle:

1. Read private discovery rules from `profile/search_criteria.md`.
2. If a current batch exists, process it before performing new discovery.
3. When no current batch is open, discover and hard-screen current jobs.
4. Assign one `batch_id`, store each candidate's `match_score`, and immediately
   add every verified match to `jobs/queue.csv`, up to 10 jobs. Do not wait for
   a full batch before writing valid candidates.
5. Process the batch from highest score to lowest score.
6. For each job, read the live description, revalidate hard filters, prepare the
   packet, pass the resume review gate, fill the application, and update state.
7. A submitted, skipped, expired, rejected, blocked, or manual-handoff job counts
   as iterated. Do not let one ATS blocker prevent moving to the next job.
8. Do not perform replacement discovery while open jobs remain. After every job
   in the current batch has been iterated, start another discovery run and add
   whatever verified matches are available, up to 10.
9. Legal/privacy/self-ID/final-submit gates may proceed only when explicitly
   covered by private facts and `answer_policy`; otherwise stop and ask.
10. After application work, check Outlook for clear application receipts,
    interview invitations, next-round notices, offers, and rejections. Apply
    only unambiguous state transitions and preserve the message link.

Do not bypass job-board login, anti-bot controls, or custom employer questions. Do not submit when any required answer, attestation, or approval is missing, ambiguous, or inconsistent with the private facts.

## Mailbox Reconciliation

- Use the Codex Outlook Email connector; never store Microsoft credentials in
  the repo.
- Match active applications by company, role, sender domain, and subject.
- For personal Microsoft accounts where full-text search is unsupported, use
  recent-message listing with date and subject filters.
- Record events with `python scripts/mailbox_reconcile.py`.
- Store subject, received date, contact, event type, and Outlook link only.
- Do not store message bodies or attachments.
- Do not mark an application rejected or advanced when the message is
  ambiguous.

## Browser-Assisted Form Filling

Use `profile/application_answers.json` as the private source of truth for form filling. If it does not exist, ask the user to create it from `profile/application_answers.example.json`.

For real application sessions, follow `docs/real_application_runbook.md`.

Allowed automatic prefills:

- Standard contact fields from `standard_fields`.
- Work authorization fields only when the form wording exactly matches `work_authorization`.
- Race, gender, veteran, and disability self-identification only from explicit values in `self_identification`; never infer or guess these values.

Review-required fields:

- Unusual sponsorship wording not exactly covered by `work_authorization`.
- Custom essay or short-answer questions not answerable from `custom_answer_facts`, resume facts, and job context.
- Legal attestations, certifications, background check consent, terms, privacy acknowledgements, and final submit actions not explicitly covered by `legal_attestations` and `answer_policy`.

Respect `answer_policy`:

- If `allow_click_legal_attestations` is false, do not click legal attestation boxes.
- If `allow_final_submit` is false, stop before final submission.
- If `auto_approve_only_when_covered_by_private_facts` is true, proceed only when every required answer and approval is explicitly covered by `profile/facts.md` or `profile/application_answers.json`.
- If a field is missing from the private fact file, ask the user instead of guessing.

## Suggested Commands

```bash
python scripts/tailor.py \
  --resume resumes/master.docx \
  --job jobs/job.txt \
  --profile profile/facts.md \
  --out outputs/tailored.docx \
  --dry-run
```

```bash
python scripts/tailor.py \
  --resume resumes/master.docx \
  --job-url "https://example.com/job-post" \
  --profile profile/facts.md \
  --out outputs/tailored.docx \
  --dry-run
```

```bash
python scripts/check_one_page.py --docx outputs/tailored.docx
```
