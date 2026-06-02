# Onboarding Checklist

## First Team Setup

- Identify pilot sponsor, admin, reviewer, and ordinary user roles.
- Confirm pilot scope, expected workflows, support hours, and feedback channel.
- Create the first project workspace.
- Grant project permissions by role, not by sharing admin accounts.
- Confirm dashboard login for each pilot user.
- Confirm users can see only authorized projects.
- Walk through project overview, runs, candidates, generated hypotheses, review, and feedback.

## Before First Session

```bash
uv run molecule-ranker pilot readiness --root "$PILOT_ROOT" --db-path "$PILOT_DB" --json
uv run molecule-ranker validate graph --root "$PILOT_ROOT"
uv run molecule-ranker validate hypotheses --root "$PILOT_ROOT"
uv run molecule-ranker validate campaign --root "$PILOT_ROOT"
uv run molecule-ranker validate evaluation --root "$PILOT_ROOT"
```

## User Orientation

- Explain that generated molecules are computational hypotheses.
- Explain that model predictions and evaluation reports are artifacts.
- Explain that Codex summaries are assistant output and require artifact grounding.
- Show how to open candidate detail and review pages.
- Show how to submit pilot feedback from the dashboard or CLI.
- Show how to report a failed job with request ID and job ID.

## Access Review

- Admin users are limited to operators who need support controls.
- Reviewers receive project review access.
- Runners receive job-run access only where needed.
- Viewers receive read-only project access.
- Service tokens are scoped, time-bound where possible, and stored outside source files.

## Completion Check

Onboarding is complete when each pilot user can log in, open the expected
project, identify generated hypothesis labels, submit feedback, and find support
contact instructions.

