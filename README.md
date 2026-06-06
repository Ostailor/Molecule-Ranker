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

## What It Can Do Today

`molecule-ranker` currently supports governed computational discovery
operations across public/read-only biomedical sources, deterministic local
artifacts, and approved integration tools. Today it can:

- Resolve a disease name into a ranked research-planning workflow.
- Retrieve source-backed targets and existing molecules from public biomedical
  sources.
- Rank existing molecules as auditable research hypotheses, with provenance,
  warnings, and trace artifacts.
- Retrieve literature metadata and extracted claim records for review-oriented
  evidence summaries.
- Generate small-molecule analog hypotheses from source-backed seed molecules
  when generation is explicitly enabled.
- Run deterministic chemical sanity checks, novelty checks, diversity filtering,
  and heuristic developability triage on generated small-molecule hypotheses.
- Create review queues, review workspaces, campaign/portfolio planning
  artifacts, evaluation artifacts, lineage records, and end-to-end result
  bundles.
- Operate external integrations in mocked, dry-run, read-only live, and
  write-approved live modes, with dry-run as the safe default.
- Enforce governance controls for external writes, generated-molecule
  advancement, capabilities, autonomy budgets, approval gates, run controls,
  kill switches, incident handling, and repair/resume workflows.
- Expose E2E workflow operations through the CLI, hosted API routes, and hosted
  dashboard views.

Generated small molecules are computational hypotheses only. They are not
evidence of binding, activity, safety, efficacy, novelty, patentability, or
clinical utility. Exact public-database novelty checks can rule out exact
matches in checked sources, but they do not prove global novelty.

## What It Cannot Yet Do

The project does **not** yet implement a true antibody or biologics discovery
engine. It does not currently generate antibody sequences, CDR designs, protein
binders, antibody-antigen complexes, epitope-specific binders, or biologics
developability packs. Existing antibodies may appear in retrieved/ranked
candidate lists when public source data supports them, but the system is not
yet creating novel antibody candidates.

The project also does not:

- Invent missing targets, molecules, external records, citations, assay results,
  graph facts, or scientific conclusions.
- Treat generated molecules as validated leads or advance them without required
  human review.
- Treat failed QC, imported unvalidated data, or Codex-authored summaries as
  scientific support.
- Perform lab execution, campaign activation, clinical interpretation, patient
  treatment selection, dosing, synthesis planning, or experimental protocol
  design.

The intended next scientific capability is a governed biologics/antibody
discovery track with explicit sequence/structure schemas, source-backed target
and epitope context, antibody-specific novelty and developability checks,
approved model/tool integrations, lineage, review gates, and result bundles
that remain research/operations summaries rather than evidence.

## Current Version

The current version is **2.7.0**.

V2.7.0 adds agentic integration operations and reliable end-to-end workflow
execution. The system can run governed workflows from a disease or project
objective to ranked candidates, generated hypotheses, review workspaces,
portfolio and campaign plans, evaluation artifacts, lineage records, and a
result bundle. These workflows are auditable, resumable, and constrained by
policy, approval, validation, and artifact-contract checks.

The autonomous campaign co-pilot is a campaign-management assistant, not a lab
executor or source of scientific truth. Failed QC is never treated as positive or negative evidence.
Generated molecules remain computational hypotheses until supported by exact
imported evidence and human review.

Agents cannot self-certify, self-approve, self-grant capabilities, or approve
policy overrides. Higher autonomy requires active certification. External
writes, generated-molecule advancement, destructive actions, high-cost jobs,
and policy overrides require governance checks and human/admin approval where
policy requires it.

## End-to-End Workflows

V2.7 workflows run in four modes:

- `mocked`: deterministic synthetic sources for local testing.
- `dry_run`: planned actions and simulated integration changes with no external writes.
- `read_only_live`: public or configured read-only sources only.
- `write_approved_live`: live writes only when explicit approval and governance permission exist.

Codex can operate the workflow through approved tools, but it cannot fabricate
missing data, assay results, citations, molecules, graph facts, external IDs,
or scientific conclusions. Imported integration data must pass deterministic
validation before it can affect evidence or scoring. Result bundles are
research and operations summaries; they are not evidence, clinical validation,
medical advice, or proof of activity, safety, or efficacy.

Run a mocked full workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow full_discovery_loop \
  --disease "Parkinson disease" \
  --mode mocked \
  --enable-generation \
  --enable-codex-summary
```

Run a dry-run integration workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow integration_sync_loop \
  --project-id project-1 \
  --mode dry_run \
  --enable-integrations \
  --dry-run
```

Run a read-only live ranking workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow disease_to_ranked_candidates \
  --disease "Parkinson disease" \
  --project-id project-1 \
  --mode read_only_live
```

Resume a workflow:

```bash
uv run molecule-ranker e2e resume \
  --workflow-id e2e-workflow-id
```

Validate a workflow:

```bash
uv run molecule-ranker e2e validate \
  --workflow-id e2e-workflow-id
```

Generate or inspect the result bundle:

```bash
uv run molecule-ranker e2e bundle \
  --workflow-id e2e-workflow-id
```

View lineage:

```bash
uv run molecule-ranker e2e lineage \
  --workflow-id e2e-workflow-id
```

Run the deterministic V2.7 E2E eval suite:

```bash
uv run molecule-ranker e2e eval --suite default
```

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

## Roadmap

- V2.7: agentic integration operations and end-to-end workflow execution.
- V2.8: cross-program agentic discovery memory.
- V2.9: V3 readiness and autonomy validation.
- V3.0: autonomous discovery operating system with validated human-governed agentic workflows.
