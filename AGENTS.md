# Repository Instructions

This repository supports a local, privacy-first job application pipeline.

## Operating Rules

- Read `SKILL.md` before changing or running the pipeline.
- Use `docs/real_application_runbook.md` for real application sessions.
- Keep private data local. Do not commit resumes, application packets, tracker data, API keys, private profile facts, application answers, or search criteria.
- Treat these files as local-only sources of truth when present:
  - `profile/facts.md`
  - `profile/application_answers.json`
  - `profile/search_criteria.md`
- Use the matching public templates only as examples:
  - `profile/facts.example.md`
  - `profile/application_answers.example.json`
  - `profile/search_criteria.example.md`

## Automation Boundaries

- Generate LinkedIn search URLs from private local criteria.
- Respect job-board login, rate-limit, and anti-bot controls.
- Do not scrape around access controls.
- Do not submit applications unless every required answer and approval is explicitly covered by the private fact files and answer policy.
- Stop and ask the user when any required answer is missing, ambiguous, or inconsistent with the private facts.

## Verification

Before pushing public changes, run:

```bash
python scripts/security_check.py --fail-on-finding
git status --short --ignored
```
