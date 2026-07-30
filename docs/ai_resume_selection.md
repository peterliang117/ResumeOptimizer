# AI Resume Selection Strategy

This workflow optimizes resumes for observable parser and criterion-review
behavior without claiming to reproduce a proprietary employer score.

## Observable Model

- LinkedIn Recruiter extracts explicit and implicit skills from profiles and
  resumes, and lets recruiters search or narrow applicants by title, location,
  experience, and skills.
- Ashby can evaluate a resume against employer-defined criteria, report whether
  each criterion is met, unmet, or undecided, and sort applicants by the
  percentage of criteria met.
- Workday HiredScore offers candidate grading that evaluates resumes against job
  requirements.
- Greenhouse parses resumes into candidate fields and warns that tables,
  headers, footers, text boxes, columns, graphics, and unclear sections can
  impair parsing.

Primary references:

- https://www.linkedin.com/help/recruiter/answer/a593591
- https://www.linkedin.com/help/recruiter/answer/a770588
- https://docs.ashbyhq.com/ai-assisted-application-review
- https://marketplace.workday.com/en-US/apps/421790/hiredscore
- https://support.greenhouse.io/hc/en-us/articles/200989175-Unsuccessful-resume-parse

## Local Optimization Order

1. Parse the live posting into must-have, core, and nice-to-have criteria.
2. Map every criterion to resume or private-profile evidence.
3. Label evidence as supported, transferable, or unsupported.
4. Put the strongest supported must-have evidence in the summary, technical
   capabilities, and recent experience.
5. Use exact job terminology only when the underlying claim is supported.
6. Generate from the single-column ATS-safe base and keep standard section
   headings and body-level contact information.
7. Reject edits that introduce unsupported tools, convert transferable
   workflows into hands-on claims, or repeat terms unnaturally.
8. Preserve quantified outcomes and context so skill mentions are evidence, not
   isolated keyword lists.
9. Run evidence validation, one-page validation, parser audit, and visual QA.
10. Record outcomes and use interview/rejection rates to calibrate future role
    selection; do not overfit from one application.

## Artifacts

- `resumes/master.docx`: untouched private source resume.
- `resumes/master_ats.docx`: generated private single-column base.
- `applications/<Company>_<Role>/ai_selection_report.json`: criterion coverage,
  evidence status, prominence plan, parser audit, and truth guardrails.
- `applications/<Company>_<Role>/proposed_edits.json`: candidate wording edits.
- `applications/<Company>_<Role>/accepted_edits.json`: edits that passed both
  factual evidence and selection-strategy validation.

## Prohibited Tactics

- Invisible text or white-on-white keywords.
- Fabricated tools, titles, dates, responsibilities, or metrics.
- Prompt injection aimed at an AI reviewer.
- Keyword lists without evidence or role context.
- Claiming a transferable dbt, Snowflake, Airflow, or similar workflow as direct
  hands-on experience.
