# molecule-ranker

Current version: `2.4.0`.

`molecule-ranker` is internal research software for source-backed molecule
ranking, governed Codex orchestration, review workflows, multi-agent scientific
operations, and audited workflow repair. It is an enterprise discovery operating system
for operational research workflows, not a clinical product and not a source of
scientific truth.

The project keeps a strict research boundary: no major new science is created by
agents, and the software provides no medical advice, lab protocols, synthesis
instructions, dosing guidance, patient treatment guidance, or claims that a
molecule is safe, active, effective, binding, synthesizable, or therapeutic.
Codex, subagents, and repair loops may inspect artifacts, diagnose failures,
rerun deterministic tools, request approvals, and repair workflows. They may not
invent evidence, assay results, citations, molecules, graph facts, benchmark
metrics, scientific scores, or approvals.

## Install

Python 3.11+ and `uv` are required.

```bash
uv sync --all-groups
uv run molecule-ranker version
```

## Run A Ranking

```bash
uv run molecule-ranker rank "<disease-name>" \
  --top 5 \
  --output-dir results/<disease-slug>
```

Typical outputs are written under the selected output directory:

- `candidates.json`
- `report.md`
- `trace.json`
- `generated_candidates.json` when generation is explicitly enabled

## Run Codex

Start the governed Codex runtime agent in dry-run mode:

```bash
uv run molecule-ranker agent start \
  --goal "Rank Alzheimer disease and create a review workspace" \
  --autonomy suggest_only \
  --dry-run \
  --output-dir .molecule-ranker/runtime-agent/alzheimer-review
```

List available subagent profiles:

```bash
uv run molecule-ranker subagents profiles
```

Run a subagent in dry-run mode:

```bash
uv run molecule-ranker subagents run \
  --skill diagnose_project \
  --project-id project-123 \
  --autonomy execute_with_approval \
  --dry-run
```

## Run Repair Workflows

Diagnose a failed job or tool result:

```bash
uv run molecule-ranker repair diagnose \
  --job-id job-123 \
  --output failure_diagnosis.json

uv run molecule-ranker repair diagnose \
  --tool-result tool_result.json \
  --output failure_diagnosis.json
```

Create and dry-run a repair plan:

```bash
uv run molecule-ranker repair plan \
  --diagnosis failure_diagnosis.json \
  --mode safe_only \
  --output repair_plan.json

uv run molecule-ranker repair execute \
  --repair-plan repair_plan.json \
  --dry-run \
  --output repair_execution.json
```

Run regression checks and write an auditable repair report:

```bash
uv run molecule-ranker repair regression \
  --repair-execution repair_execution.json \
  --output regression_checks.json

uv run molecule-ranker repair report \
  --repair-execution repair_execution.json \
  --output repair_report.md
```

Run the repair eval suite:

```bash
uv run molecule-ranker repair eval --suite default
```

## Run Hosted Mode

Create a local platform database and admin user:

```bash
uv run molecule-ranker db init --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker user create \
  --email admin@example.com \
  --password "change-me" \
  --role admin \
  --db-path .molecule-ranker/platform.sqlite
```

Start the hosted dashboard and API:

```bash
uv run molecule-ranker serve \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Open:

```text
http://127.0.0.1:8765/dashboard
http://127.0.0.1:8765/dashboard/repair
```

Hosted APIs are available under `/api/v2/*`.

## Verify

```bash
uv run ruff check .
uv run pyright
uv run molecule-ranker validate repair-guardrails --json
uv run molecule-ranker repair eval --suite default
uv run pytest
```

Default tests use mocked public-source and mocked Codex responses. Live public
API smoke tests are opt-in:

```bash
MOLECULE_RANKER_RUN_LIVE=1 uv run pytest -m live tests_live/
```
