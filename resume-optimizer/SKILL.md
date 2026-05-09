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
3. Ask the user to approve specific edits, or create `outputs/accepted_edits.json` from clearly approved suggestions.
4. Run `python scripts/tailor.py --accepted-edits outputs/accepted_edits.json ...`.
5. Run `python scripts/check_one_page.py --docx outputs/tailored.docx`.
6. If page count exceeds one, shorten low-priority edits and check again.

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
