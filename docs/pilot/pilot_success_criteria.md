# Pilot Success Criteria

## Decision Question

The pilot succeeds if the first team can complete representative ranking,
review, feedback, readiness, and support workflows with acceptable usability,
performance, reliability, and operational support.

## Required Outcomes

- First team can log in and access only authorized projects.
- Admin can create a project and grant team access.
- Users can inspect source-backed ranking artifacts.
- Generated molecule records are visibly labeled as computational hypotheses.
- Codex assistant output is clearly separated from evidence and decisions.
- Review queue and feedback capture are usable without admin help.
- Pilot readiness report has no unresolved blockers.
- Support bundle can be generated and contains no secrets or cache payloads.
- Golden validation workflows pass in the pilot environment.
- Job failures show actionable, redacted remediation context.

## Quantitative Targets

- Dashboard pages load within the internal target set by `ops metrics`.
- Failed job rate remains below the pilot alert threshold.
- Queue backlog does not exceed the configured warning threshold for sustained periods.
- Support bundle generation completes in a bounded time for the pilot root.
- No high-severity guardrail or redaction regression is observed.

## Stop Criteria

Pause or exit the pilot if any of these occur:

- Secrets or cache payloads are shown in the UI, API, logs, metrics, alerts, or bundle.
- Generated hypotheses are presented as evidence or decisions.
- Codex output is treated as evidence, data, scoring, benchmark output, or a decision.
- Readiness blockers remain unresolved at pilot launch.
- Admin actions cannot be audited.

## Exit Decision

Use `pilot_exit_review.md` to record the final decision:

- `continue`: proceed to broader internal rollout.
- `extend`: continue with a narrow remediation plan.
- `stop`: do not continue until blockers are fixed and revalidated.

