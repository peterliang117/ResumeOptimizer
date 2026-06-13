# ResumeOptimizer Repository

The active application is in [`resume-optimizer/`](resume-optimizer/).

This repository intentionally keeps the application in that subdirectory to
preserve existing local automation paths, Codex project history, and script
working-directory assumptions.

## Repository Layout

- `AGENTS.md`: repository-level agent guidance.
- `resume-optimizer/`: active job-search and application workflow.
- `resume-optimizer/README.md`: setup, commands, and operating instructions.
- `resume-optimizer/docs/folder_structure.md`: source, private-data, and
  generated-artifact boundaries.

## Local Workspace Files

The parent `JobSearch` workspace may contain two local-only Azure utilities:

- `../keys.txt`: ignored credential file used by the Azure integration.
- `../azure_llm_request.py`: standalone Azure connectivity diagnostic.

They remain outside this Git repository to preserve the existing local setup.
Do not commit `keys.txt` or move private workflow data into tracked folders.
