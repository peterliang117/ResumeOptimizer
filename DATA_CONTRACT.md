# Data Contract

This repo has two layers: reusable system files that can be committed and private local files that must stay off GitHub.

## Private Local Layer

These files contain personal data, applications, search criteria, resumes, or generated artifacts. They are ignored by Git and should not be committed.

| Path | Purpose |
| --- | --- |
| `resumes/master.docx` | Master resume used for tailoring |
| `profile/facts.md` | Private source of truth for resume facts and self-ID answers |
| `profile/application_answers.json` | Private source of truth for application form answers |
| `profile/search_criteria.md` | Private job search rules and compensation targets |
| `profile/evidence_map.private.json` | Resume/profile evidence used to validate automated edits |
| `profile/resume_variants.private.json` | Local role-family resume variant selection metadata |
| `jobs/*.txt` except `jobs/job.txt` | Saved real job descriptions |
| `jobs/queue.csv` | Real local job queue |
| `data/resume_optimizer.db` and sidecars | SQLite source of truth for jobs, applications, events, variants, mappings, schedules, and local workflow-effort metadata |
| `applications/` | Per-employer application packets and tailored resumes |
| `tailored_resumes/` | Final company-specific resume copies |
| `outputs/` | Generated reports, PDFs, render checks, and scratch artifacts |
| `tracker/applications.csv` | Local application tracker |
| `.env`, `.env.*` | Local secrets and API keys |
| `browser_state/`, `screenshots/` | Browser/session artifacts |
| `backups/` and `*.rostate` | Encrypted private-state transfer archives |

## Public System Layer

These files are reusable tooling, documentation, and templates. They are safe to commit when they do not contain private facts.

| Path | Purpose |
| --- | --- |
| `README.md` | Public project overview |
| `SKILL.md` | Codex skill/workflow guidance |
| `DATA_CONTRACT.md` | This layer contract |
| `docs/` | Public workflow docs and diagrams |
| `scripts/` | Reusable local pipeline utilities |
| `requirements.txt` | Python dependencies |
| `profile/*.example.*` | Private-file templates |
| `jobs/queue.example.csv` | Queue template |
| `jobs/job.txt` | Generic sample/paste target |
| `tracker/.gitkeep`, `resumes/.gitkeep` | Directory placeholders |

## Rule

System files may be updated and pushed. Private local files are read only to run the local workflow and must not be copied into public docs, examples, commits, issues, pull requests, or terminal transcripts. Encrypted transfer archives must also remain off GitHub. Browser sessions, connector credentials, OAuth tokens, API keys, and OTP codes are machine-specific and must never be exported by this project.

Before pushing, run:

```bash
python scripts/security_check.py --fail-on-finding
python scripts/doctor.py --public-only
python scripts/verify_tracker.py
git status --short --ignored
```
