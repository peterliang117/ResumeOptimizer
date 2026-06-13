# Folder Structure

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
|-- profile/
|   |-- application_answers.example.json
|   |-- facts.example.md
|   |-- portals.example.yml
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
resumes/*.docx
jobs/queue.csv
jobs/*.txt
tracker/applications.csv
applications/
tailored_resumes/
```

Do not relocate these paths without updating every script, automation, and
runbook that depends on them.

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

The parent `JobSearch/keys.txt` and `JobSearch/azure_llm_request.py` remain
local-only compatibility files for Azure connectivity.
