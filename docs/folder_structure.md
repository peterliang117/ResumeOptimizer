# Folder Structure

Portable workflow files also include:

- `automation/`: sanitized Codex automation prompts with no machine-specific
  identity or credentials
- `backups/`: ignored encrypted private-state archives used for workstation
  migration
- `.github/workflows/`: public checkout validation and security scanning

The workflow uses three clear data boundaries: tracked source, ignored private
state, and reproducible generated artifacts.

## Tracked Source

```text
ResumeOptimizer/
|-- README.md
|-- SKILL.md
|-- DATA_CONTRACT.md
|-- docs/
|   |-- folder_structure.md
|   |-- real_application_runbook.md
|   |-- research/
|   `-- archive/
|-- jobs/
|   |-- job.txt
|   `-- queue.example.csv
|-- data/
|   `-- .gitkeep
|-- profile/
|   |-- application_answers.example.json
|   |-- facts.example.md
|   |-- portals.example.yml
|   |-- resume_variants.example.json
|   `-- search_criteria.example.md
|-- resumes/
|   `-- .gitkeep
|-- scripts/
|-- tests/
`-- tracker/
    `-- .gitkeep
```

Tracked files contain reusable code, documentation, and sanitized templates.

## Local Private State

These paths are ignored by Git and are the source of truth for real workflow
data:

```text
profile/facts.md
profile/application_answers.json
profile/search_criteria.md
profile/portals.yml
profile/evidence_map.private.json
profile/resume_variants.private.json
resumes/*.docx
jobs/queue.csv
jobs/*.txt
data/resume_optimizer.db
data/resume_optimizer.db-wal
data/resume_optimizer.db-shm
tracker/applications.csv
applications/
tailored_resumes/
```

SQLite is the state authority. The queue and tracker CSV files are compatibility
exports for existing commands and the local dashboard. Do not relocate these
paths without updating every script, automation, and runbook that depends on them.

## Generated Artifacts

These paths are ignored and can be regenerated:

```text
outputs/
.playwright-cli/
__pycache__/
.pytest_cache/
```

Application packets and tailored resumes are ignored for privacy, but they are
not considered disposable. Preserve them unless the user explicitly requests
archival or deletion.

## Workspace Boundary

The Git repository and active application share one root:
`JobSearch/ResumeOptimizer`. Run commands from this directory.

Optional parent-workspace `keys.txt` and `azure_llm_request.py` files remain
local-only compatibility files for Azure connectivity.
