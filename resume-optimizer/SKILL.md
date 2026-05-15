---
name: resume-optimizer
description: Use when tailoring a DOCX resume to a pasted job description or job URL while preserving the original resume format, keeping the result to one page, and avoiding unsupported or exaggerated claims.
---

# Resume Optimizer

Use this workflow to tailor a resume locally.

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
3. Run `python scripts/run_application_pipeline.py ...` to create the application packet, store the job description, generate `proposed_edits.json`, create `fit_analysis.json`, and update `tracker/applications.csv` to `analyzed`.
4. For direct resume-only work, run `python scripts/tailor.py --dry-run ...` to generate `outputs/suggestions.json`.
5. Create or inspect `proposed_edits.json`, then stop for content review in chat before applying edits.
6. In the chat review, show the user a concise numbered list of each proposed change:
   - original wording
   - suggested wording
   - reason it helps for the job
   - evidence source
   - truth risk
7. Ask the user to approve all edits, approve selected edit numbers, reject edits, or request wording changes.
8. Only after explicit approval, create `accepted_edits.json` from the approved edits.
9. Run `python scripts/run_application_pipeline.py ... --accepted-edits ...` for full lifecycle work, or `python scripts/tailor.py --accepted-edits ...` for direct resume-only work.
10. Run or verify `python scripts/check_one_page.py --docx ...`.
11. If page count exceeds one, shorten low-priority edits and check again.
12. Update `tracker/applications.csv` as the job moves from `resume_ready` to `application_started`, `submitted`, `interview`, `rejected`, or `closed`.

## Required Review Gate

Do not skip the chat-based content review. Never apply proposed edits immediately after generating them unless the user has already explicitly said to apply all proposed edits for that specific job.

The content review is for truthfulness, role fit, wording, and unsupported claims. Visual/layout review happens after the DOCX is generated through Word or the LibreOffice page-count check.

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

Use a staged automation lifecycle:

1. LinkedIn or job-board discovery adds promising jobs to `jobs/queue.csv`.
2. The pipeline reads the job URL or pasted description.
3. The resume optimizer proposes truthful edits.
4. The chat review gate decides what can be applied.
5. The pipeline generates and validates the tailored resume.
6. Browser-assisted application filling may be used only after explicit user request for a specific application.
7. Final submit remains manual by default.

Do not auto-submit applications. Do not bypass job-board login, anti-bot controls, or custom employer questions.

## Browser-Assisted Form Filling

Use `profile/application_answers.json` as the private source of truth for form filling. If it does not exist, ask the user to create it from `profile/application_answers.example.json`.

Allowed automatic prefills:

- Standard contact fields from `standard_fields`.
- Work authorization fields only when the form wording exactly matches `work_authorization`.
- Race, gender, veteran, and disability self-identification only from explicit values in `self_identification`; never infer or guess these values.

Review-required fields:

- Unusual sponsorship wording.
- Custom essay or short-answer questions. Draft from `custom_answer_facts`, resume facts, and job context, then ask the user to approve or edit.
- Legal attestations, certifications, background check consent, terms, privacy acknowledgements, and any final submit action.

Respect `answer_policy`:

- If `allow_click_legal_attestations` is false, do not click legal attestation boxes.
- If `allow_final_submit` is false, stop before final submission.
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
