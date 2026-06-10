# Release V0.3 Discovery Workflow Connection Notes

Release V0.3 connects the molecule-ranker product app to a bounded discovery
workflow while keeping Dev V3.0 engine internals behind product-safe APIs.

- Version: `0.3.0`
- Name: `Release V0.3 Discovery Workflow Connection`
- Stage: `hosted_alpha_discovery_runs`

## What Changed

- Added `product_runs` and `product_run_artifacts` tables for tenant-scoped
  discovery run state and product-safe artifacts.
- Added RLS policies for organization/project-scoped run and artifact access.
- Added project-scoped product APIs for run creation, run listing, run status,
  cancellation, artifact listing, artifact retrieval, and result bundle access.
- Added a product-safe engine runner wrapper with mocked/dry-run execution
  paths and isolated per-org/project/run working directories.
- Added safe database-backed artifact storage for small JSON and Markdown
  result bundle summaries.
- Added product artifact filtering that blocks raw logs, raw traces, raw Codex
  transcripts, cache files, secrets, and unsafe engine internals.
- Connected the run creation form, run progress page, result summary page,
  dashboard, project detail page, and usage page to real run state.
- Recorded `run_discovery` usage events and enforced discovery run limits before
  run creation.
- Updated product release version to `0.3.0`.

## Still Disabled

- Stripe and paid subscriptions.
- External writes and external integrations.
- Write-approved-live mode.
- Antibody generation.
- Production deployment enablement.
- Production-grade background worker infrastructure.
- Advanced result bundle viewer.
- Full candidate, evidence, and generated UI from real artifacts.
- Raw AgentGraph, Codex transcript, trace, and log exposure.

## How To Test

```bash
cd apps/web
npm test
npm run lint
npm run typecheck
```

```bash
python -m pytest \
  tests/test_product_module.py \
  tests/test_supabase_product_auth_schema.py \
  tests/test_product_v0_3_discovery_workflow_docs.py \
  -q
```

## Local Mock Runner

Use `PRODUCT_ENGINE_RUNNER_MODE=mock` for local V0.3 demos and tests. The mock
runner creates synthetic artifacts only, marks them with `synthetic: true` and
`for_ui_test_only: true`, and avoids fake real biomedical identifiers.

## Known Limitations

- The default local worker path is synchronous and intended for mocked/dry-run
  V0.3 use.
- Result artifacts are summary-level JSON/Markdown, not a deep candidate or
  evidence viewer.
- Live Supabase RLS checks remain optional for local development.
- Rich result bundle, candidate, and evidence exploration moves to V0.4.
- Production deployment hardening, storage, workers, and monitoring move to
  V0.6.
