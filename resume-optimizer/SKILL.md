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
2. Run `python scripts/tailor.py --dry-run ...` to generate `outputs/suggestions.json`.
3. Create or inspect `proposed_edits.json`, then stop for content review in chat before applying edits.
4. In the chat review, show the user a concise numbered list of each proposed change:
   - original wording
   - suggested wording
   - reason it helps for the job
   - evidence source
   - truth risk
5. Ask the user to approve all edits, approve selected edit numbers, reject edits, or request wording changes.
6. Only after explicit approval, create `accepted_edits.json` from the approved edits.
7. Run `python scripts/tailor.py --accepted-edits ...`.
8. Run `python scripts/check_one_page.py --docx ...`.
9. If page count exceeds one, shorten low-priority edits and check again.

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
