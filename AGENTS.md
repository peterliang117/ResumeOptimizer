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

## Subagent Boundaries

- Use the public playbooks in `agents/` for bounded scout, role-fit, and packet-audit work.
- Subagents may return JSON artifacts only. They must not read private profile files, control a browser, or write the queue, tracker, application packets, or resumes.
- The coordinator validates subagent artifacts and is the sole writer of local workflow state. See `docs/subagent_workflow.md`.

## Adaptive Barrier Handling

- Before a failure-prone or browser stage, consult and start
  `scripts/workflow_optimizer.py`; finish the attempt with elapsed effort and
  the actual outcome.
- Treat time and interaction count as the default token-cost proxies. Do not
  exceed the stage budget to keep investigating one posting.
- Follow active learned actions such as `use_codex`, `open_direct_ats`,
  `manual_handoff`, or `skip_candidate` instead of repeating a known failure.
- Record a barrier with `scripts/application_state.py` when it also changes the
  queue or tracker state. Then continue to the next queued job.
- See `docs/process_optimization.md` for budgets and barrier mappings.

## Verification

Before pushing public changes, run:

```bash
python scripts/security_check.py --fail-on-finding
git status --short --ignored
```
