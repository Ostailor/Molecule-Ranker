# User Training

## Training Goal

Users should understand how to navigate the hosted dashboard, interpret
operational labels, submit feedback, and avoid confusing artifacts with evidence
or decisions.

## Core Walkthrough

1. Log in to `/login`.
2. Open `/dashboard`.
3. Select the assigned project.
4. Open run detail.
5. Open candidate ranking table.
6. Open generated molecule table.
7. Open review queue.
8. Open feedback page.

## Key Labels

- `Computational hypothesis`: generated molecule record, not evidence.
- `Model prediction`: model output artifact.
- `Evaluation artifact`: platform evaluation output.
- `Codex output`: assistant output, separate from evidence and decisions.

## Feedback

Dashboard:

```text
/dashboard/feedback
```

CLI:

```bash
uv run molecule-ranker feedback submit \
  "Navigation issue on candidate detail page" \
  --root "$PILOT_ROOT" \
  --project-id "$PROJECT_ID" \
  --page-or-command "dashboard/projects/$PROJECT_ID"
```

Do not include credentials, tokens, private notes, or raw artifact payloads in
feedback text.

## User Completion Check

Training is complete when the user can:

- Open the assigned project.
- Explain generated hypothesis, model prediction, evaluation artifact, and Codex output labels.
- Open a review item.
- Submit pilot feedback.
- Report a failed job with a job ID or request ID.

