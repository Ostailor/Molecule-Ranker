# molecule-ranker

`molecule-ranker` is a research-planning tool for transparent existing-molecule
ranking, campaign workflow management, and governed Codex runtime-agent
operations. It resolves a disease through public biomedical data sources,
retrieves evidence-backed targets and existing molecules, ranks candidates as
research hypotheses, and keeps autonomous agent actions inside approved policy,
tool, artifact, budget, certification, and safety boundaries.

The project is for research planning and operational oversight only. It does not provide medical advice.
It does not provide lab protocols, synthesis
instructions, dosing guidance, patient guidance, clinical claims, fabricated
evidence, fabricated assay results, fabricated citations, fabricated molecules,
fabricated graph facts, fabricated metrics, or fabricated approvals.

## Current Version

The current version is **2.6.0**.

V2.6.0 adds enterprise agent governance for runtime agents, subagents, Codex
workers, campaign co-pilots, tools, campaigns, and autonomous actions.
Governance controls autonomy policies, capability grants, budgets,
certifications, risk profiles, incidents, approval requirements, run controls,
kill switches, policy simulation, red-team evals, and governance reports.

The autonomous campaign co-pilot is a campaign-management assistant, not a lab
executor or source of scientific truth. Failed QC is never treated as positive or negative evidence.
Generated molecules remain computational hypotheses until supported by exact
imported evidence and human review.

Agents cannot self-certify, self-approve, self-grant capabilities, or approve
policy overrides. Higher autonomy requires active certification. External
writes, generated-molecule advancement, destructive actions, high-cost jobs,
and policy overrides require governance checks and human/admin approval where
policy requires it.

## Install

Python 3.11+ is required.

```bash
uv sync
```

Check the CLI:

```bash
uv run molecule-ranker --help
uv run molecule-ranker version
```

## Run Ranking

Run an existing-molecule ranking job:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Write outputs under `results/`:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --max-targets 25 \
  --max-molecules-per-target 10
```

Typical outputs:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/trace.json
```

The ranking workflow uses live public biomedical APIs. If required real data
cannot be retrieved, the workflow fails instead of inventing targets,
molecules, evidence, citations, or scores.

## Run Governance

Validate current governance behavior:

```bash
uv run molecule-ranker validate agent-governance
```

Run the default governance red-team eval suite:

```bash
uv run molecule-ranker governance eval --suite default
```

Simulate a governed action before enabling policy:

```bash
uv run molecule-ranker governance simulate \
  --agent-id runtime-agent-1 \
  --agent-type runtime_agent \
  --tool run_external_sync_write \
  --action run_external_sync_write \
  --tool-category integration \
  --side-effect-level external_write \
  --project-id project-1
```

Generate governance oversight artifacts:

```bash
uv run molecule-ranker governance report \
  --project-id project-1 \
  --output-dir .molecule-ranker/agent-governance/reports
```

Governance reports are operational oversight artifacts, not scientific
evidence.

## Run Tests

Run linting, type checking, and the full test suite:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

Run the deterministic release validation workflows:

```bash
uv run molecule-ranker validate release
```
