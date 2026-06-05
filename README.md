# molecule-ranker

`molecule-ranker` is an agent-first research prototype for transparent
existing-molecule ranking and campaign workflow management. It resolves a
disease through public biomedical data sources, retrieves evidence-backed
targets and existing molecules, ranks candidates as research hypotheses, and
adds a V2.5 autonomous campaign co-pilot for human-governed planning work.

The project is for research planning only. It does not discover cures; it
provides no medical advice, lab execution, protocols, synthesis instructions,
dosing guidance, patient guidance, or procedural wet-lab steps.

## Current Version

V2.5.0 adds an autonomous campaign co-pilot. The co-pilot monitors active
campaigns, detects events, routes triggers, proposes safe next actions, executes
safe low-risk follow-ups when autonomy allows, requests approvals for risky
actions, generates campaign status updates, exposes a hosted dashboard/API
surface, and runs deterministic eval and guardrail validation suites.

The co-pilot is a campaign-management assistant, not a lab executor. Human
approval remains required for campaign advancement, stage gates, external
writes, generated-molecule assay advancement, destructive actions, high-cost
jobs, and policy overrides. Codex and the co-pilot cannot approve their own
actions.

Scientific integrity rules are enforced:

- No fabricated evidence, assay results, citations, molecules, graph facts,
  metrics, scores, or approvals.
- Failed QC is never treated as positive or negative evidence.
- Generated molecules remain computational hypotheses unless exact imported
  results support direct evidence, and advancement still requires human
  approval.
- Scores are prioritization aids, not validated predictions of activity,
  safety, efficacy, binding, therapeutic value, or synthesizability.

## Install

Python 3.11+ is required. Install with `uv`:

```bash
uv sync
```

Check the installed CLI and version:

```bash
uv run molecule-ranker --help
uv run molecule-ranker version
```

## Run Ranking

Run an existing-molecule ranking job:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Useful options:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --max-targets 25 \
  --max-molecules-per-target 10 \
  --verbose
```

Print a JSON summary:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10 --json
```

Ranking outputs are written under:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/trace.json
```

The ranking CLI uses live public biomedical APIs. If required real data cannot
be retrieved, it fails instead of inventing fallback targets, molecules,
evidence, citations, or scores.

## Run The Co-Pilot

Start the co-pilot in observe-only mode:

```bash
uv run molecule-ranker copilot start \
  --campaign-id campaign-123 \
  --autonomy observe_only
```

Run one monitoring cycle:

```bash
uv run molecule-ranker copilot check --campaign-id campaign-123
```

Inspect co-pilot state:

```bash
uv run molecule-ranker copilot events --campaign-id campaign-123
uv run molecule-ranker copilot triggers --campaign-id campaign-123
uv run molecule-ranker copilot actions --campaign-id campaign-123
```

Approve or reject a proposed action after human review:

```bash
uv run molecule-ranker copilot approve-action \
  --action-id action-123 \
  --reviewer-id reviewer-456 \
  --rationale "Reviewed source-grounded planning action."

uv run molecule-ranker copilot reject-action \
  --action-id action-123 \
  --reviewer-id reviewer-456 \
  --rationale "Insufficient support for this planning action."
```

Generate a grounded status update:

```bash
uv run molecule-ranker copilot status-update \
  --campaign-id campaign-123 \
  --output copilot_status_update.md
```

Run co-pilot evals and guardrail validation:

```bash
uv run molecule-ranker copilot eval --suite default
uv run molecule-ranker validate copilot-guardrails
```

In a hosted deployment with the co-pilot API mounted, view the dashboard at:

```text
GET /copilot
```

## Verify

Run the full test suite:

```bash
uv run pytest
```

Run lint and type checking:

```bash
uv run ruff check .
uv run pyright
```
