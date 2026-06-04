# molecule-ranker

Internal research software for source-backed molecule ranking, generated
molecule hypotheses, review workflows, governed Codex orchestration, and V2.3
multi-agent scientific operations with specialized Codex subagents.

Current release: `2.3.0`.

V2.3 adds multi-agent scientific operations while preserving the current release boundary:
v2.0 enterprise discovery operating system, no major new science, and no medical advice.

## Purpose

`molecule-ranker` helps internal research teams:

- Resolve diseases to public biomedical entities.
- Retrieve source-backed targets, molecules, and literature.
- Rank existing molecules with transparent scoring and provenance.
- Optionally generate in-silico molecule hypotheses.
- Review developability, experiments, structures, knowledge graph context,
  portfolios, campaigns, and validation artifacts.
- Run Codex only through approved tools, policies, approvals, guardrails, and
  audit trails.
- Delegate V2.3 work to role-scoped Codex subagents that can critique, revise,
  form consensus, and escalate to human review.

## Safety Boundary

This is internal research software, not a clinical product. It does not provide
medical advice, lab protocols, synthesis instructions, dosing, patient treatment
guidance, or claims that a molecule is safe, active, effective, binding,
synthesizable, or therapeutic.

Codex and subagents are tool-using operational specialists, not scientific truth
sources. They cannot invent evidence, assay results, citations, molecules, graph
facts, model metrics, docking scores, campaign outcomes, or benchmark results.
Human approval remains required for stage gates, external writes, generated
molecule advancement, destructive actions, and policy overrides.

## Install

Python 3.11+ and `uv` are required.

```bash
uv sync --all-groups
uv run molecule-ranker version
```

## Run A Ranking

Generation, docking, external writes, Codex, review workflows, and experimental
evidence are disabled unless explicitly configured or enabled.

```bash
uv run molecule-ranker rank "<disease-name>" \
  --top 5 \
  --output-dir results/<disease-slug>
```

Common outputs:

- `candidates.json`
- `report.md`
- `trace.json`
- `generated_candidates.json` when generation is enabled

## Run Codex And Subagents

Start the governed Codex runtime agent in dry-run mode:

```bash
uv run molecule-ranker agent start \
  --goal "Rank Alzheimer disease and create a review workspace" \
  --autonomy suggest_only \
  --dry-run \
  --output-dir .molecule-ranker/runtime-agent/alzheimer-review
```

List built-in V2.3 subagents:

```bash
uv run molecule-ranker subagents profiles
```

Run a dry-run project diagnosis:

```bash
uv run molecule-ranker subagents run \
  --skill diagnose_project \
  --project-id project-123 \
  --autonomy execute_with_approval \
  --dry-run
```

Run generated-candidate improvement:

```bash
uv run molecule-ranker subagents run \
  --skill improve_generated_candidates \
  --project-id project-123 \
  --autonomy execute_with_approval \
  --output-dir .molecule-ranker/subagents
```

Inspect a subagent session:

```bash
uv run molecule-ranker subagents session show <session_id>
uv run molecule-ranker subagents session messages <session_id>
uv run molecule-ranker subagents critique --result-id <result_id> --critic guardrail_sentinel
uv run molecule-ranker subagents consensus --session-id <session_id>
```

Run multi-agent evals:

```bash
uv run molecule-ranker subagents eval --suite default
```

## Run Hosted Mode

Create a local platform database, admin user, and dashboard:

```bash
uv run molecule-ranker db init --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker user create \
  --email admin@example.com \
  --password "change-me" \
  --role admin \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker serve \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Open:

```text
http://127.0.0.1:8765/dashboard
http://127.0.0.1:8765/dashboard/subagents/sessions
```

Hosted subagent APIs are under `/api/v2/subagents/*`.

## Verify

```bash
uv run ruff check .
uv run pyright
uv run molecule-ranker subagents eval --suite default
uv run pytest
```

Default tests use mocked public-source and mocked Codex responses. Live public
API smoke tests are opt-in:

```bash
MOLECULE_RANKER_RUN_LIVE=1 uv run pytest -m live tests_live/
```
