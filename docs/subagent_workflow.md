# Subagent Workflow

This workflow delegates bounded analysis while keeping private facts and all
side effects under one coordinator.

## Roles

| Role | Input | Output | May write state? |
| --- | --- | --- | --- |
| Coordinator | Private local sources and public role data | Decisions and workflow actions | Yes; sole writer |
| Job scout | Sanitized public search brief | Candidate JSON | No |
| Role fit judge | Role payload and minimized evidence inventory | Fit JSON | No |
| Packet auditor | Sanitized packet manifest and evidence IDs | Audit JSON | No |

## Contract

1. The coordinator reads private sources locally and never places personal
   facts, search criteria, answers, resumes, secrets, or private paths in a
   playbook, prompt, JSON artifact, log, or public document.
2. The coordinator gives each subagent only the minimum input needed. Unknown
   facts remain unknown; agents must not infer them.
3. Each subagent returns exactly one valid JSON artifact matching its playbook.
   Artifacts use short public excerpts and opaque evidence IDs instead of
   private values. The coordinator validates JSON before relying on it.
4. Subagents are read-only analysts. They must not directly write
   `jobs/queue.csv`, `tracker/applications.csv`, packet files, profiles,
   resumes, or browser state. They must not operate a browser, fill forms,
   attest, upload, or submit.
5. The coordinator is the only process allowed to write queue/tracker state,
   generate packets, or control browser actions. It applies normal user
   approvals and private-fact checks before every state-changing action.
6. A subagent recommendation is advisory. The coordinator revalidates live
   posting evidence, private hard filters, truth support, and any required
   legal, privacy, self-ID, or submission gate.

## Orchestration Sequence

1. Coordinator creates a sanitized discovery brief and calls the job scout.
2. Coordinator validates candidate URLs and applies private criteria locally.
3. For candidates under consideration, coordinator supplies a minimal evidence
   inventory to the role fit judge and reviews its JSON.
4. Only the coordinator may score, batch, deduplicate, and write accepted jobs
   to the queue.
5. After coordinator prepares a packet, it sends a sanitized manifest to the
   packet auditor. Any unknown, unsupported, or unapproved required item blocks
   progression.
6. Coordinator alone performs final verification, user handoff or approval,
   browser work, and queue/tracker updates.

## Failure Handling

An agent returns `blocked` or `needs_user_review` when evidence is unavailable,
ambiguous, or outside its allowed inputs. The coordinator records only the
appropriate local workflow state and asks the user when private facts or
approval are required. Silence, missing evidence, or a favorable fit score
never authorizes a claim, attestation, or submission.
