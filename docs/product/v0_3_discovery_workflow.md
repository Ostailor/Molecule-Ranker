# V0.3 Bounded Discovery Workflow

Release V0.3 connects authenticated product projects to a bounded discovery
workflow. It does not expose raw Dev V3.0 engine internals. The shipped product
surface is run creation, run status, and a summary-level result bundle.

V0.3 connects run creation/status/result bundle summary. V0.4 builds richer
result bundle, candidate, and evidence UI. V0.6 improves deployment, artifact
storage, workers, and monitoring.

## V0.3 Scope

V0.3 adds the first product-safe discovery workflow path:

- Researchers, admins, and owners can create a discovery run from a project.
- Viewers can inspect permitted run state but cannot create runs.
- The product API validates safe run options before persisting a run.
- Usage limits are checked before run creation.
- A minimal synchronous worker executes mocked or dry-run workflows locally.
- Product-safe result artifacts are stored behind tenant-scoped APIs.
- Normal users see result bundles and summary artifacts, not raw engine traces.

V0.3 remains a bounded productization release. It is not a production-grade
background job system, billing release, or advanced scientific artifact viewer.

## Database Changes

V0.3 adds two Supabase tables:

- `product_runs`: tenant-scoped discovery run state.
- `product_run_artifacts`: tenant-scoped product-safe artifacts for runs.

`product_runs` stores:

- Organization, project, and creator identifiers.
- Run type, mode, and status.
- Disease or goal text, target focus, safe options, progress, and result
  summary.
- Safe error summary and lifecycle timestamps.

`product_run_artifacts` stores:

- Organization, project, and run identifiers.
- Product artifact type and storage kind.
- Small JSON or Markdown payloads for V0.3 database-backed storage.
- Hash, size, visibility flags, and metadata.

RLS policies keep runs and artifacts scoped to organization membership.
Researchers, admins, and owners can create runs for projects in their
organization. Viewers cannot create runs. Admin-only artifacts require owner or
admin membership.

## Run Lifecycle

Run status is persisted in `product_runs.status`:

```text
queued -> running -> succeeded
                 -> failed
                 -> partially_succeeded
                 -> cancelled
```

- `queued`: the product API accepted a guarded request and created the run row.
- `running`: the synchronous V0.3 worker is preparing product-safe output.
- `succeeded`: product-safe summary output and artifact storage completed.
- `partially_succeeded`: a safe result summary exists but artifact storage did
  not fully complete.
- `failed`: the workflow failed and only a safe error summary is shown.
- `cancelled`: the run was cancelled before completion. Active subprocess
  termination is not production-grade in V0.3.

## Product Run API

V0.3 adds project-scoped run APIs:

- `POST /api/product/projects/[projectId]/runs`
- `GET /api/product/projects/[projectId]/runs`
- `GET /api/product/projects/[projectId]/runs/[runId]`
- `GET /api/product/projects/[projectId]/runs/[runId]/status`
- `POST /api/product/projects/[projectId]/runs/[runId]/cancel`
- `GET /api/product/projects/[projectId]/runs/[runId]/result-bundle`
- `GET /api/product/projects/[projectId]/runs/[runId]/artifacts`
- `GET /api/product/projects/[projectId]/runs/[runId]/artifacts/[artifactId]`

All routes require authenticated product context. Routes scope database queries
by active `organization_id`, `project_id`, and `run_id`. Status and result
responses return product-safe fields only: status, progress, result summary,
safe error summary, and visible artifacts.

The run creation route:

1. Requires `run:create` and project read access.
2. Verifies the project belongs to the active organization.
3. Validates request text and run options.
4. Rejects external write mode, antibody generation, and unsafe requests.
5. Checks `run_discovery` usage limits before creating the run.
6. Checks `generated_hypotheses` usage when generated hypotheses are enabled.
7. Checks `codex_task` usage when Codex summary budgeting is enabled.
8. Creates a queued `product_runs` row.
9. Records usage events.
10. Executes the V0.3 mocked/dry-run worker path.

## Engine Boundary

The product app calls a product-safe engine wrapper. It does not expose the Dev
V3.0 engine, AgentGraph state, raw Codex transcripts, stdout, stderr, or raw
trace/log output to normal users.

Allowed V0.3 command boundary:

- `molecule-ranker discover`, or an equivalent reviewed end-to-end command.
- Command and arguments must be array-based.
- Shell execution must remain disabled.
- Output directories must be isolated by organization, project, and run.

The output directory shape is:

```text
PRODUCT_RUN_WORKDIR/org_<orgId>/project_<projectId>/run_<runId>/
```

Engine failures are converted into safe product errors. Redacted diagnostics may
be stored only as admin-only artifacts.

## Supported Modes

Default mode is `dry_run`.

Supported V0.3 modes:

- `mocked`: deterministic synthetic output for tests and local demos.
- `dry_run`: bounded product wrapper execution without external writes.
- `read_only_live`: optional, disabled unless explicitly enabled and reviewed.

`read_only_live` is disabled by default. `write_approved_live` is disabled.
External writes, external integrations, antibody generation, raw AgentGraph
exposure, raw Codex transcripts, raw traces, and raw logs are disabled. The
phrase raw AgentGraph exposure is an explicit blocked boundary for V0.3.

## Artifact Storage

V0.3 stores small product-safe artifacts in `product_run_artifacts` using
database-backed JSON or Markdown payloads.

Supported V0.3 artifact kinds include:

- `result_bundle_json`
- `result_bundle_markdown`
- `candidates_json`
- `generated_candidates_json`
- `evidence_json`
- `validation_json`
- `trace_redacted_json` for admin-only diagnostics
- `engine_diagnostics_redacted_json` for admin-only diagnostics
- `runtime_summary_redacted_json` for admin-only diagnostics

Every stored artifact must have:

- Type.
- Size.
- Hash when possible.
- Organization, project, and run scope.
- Visibility flags.

Normal users can only access public artifacts in their active organization,
project, and run. Admin-only artifacts require owner or admin membership.

## Artifact Filtering

The artifact filter maps known engine filenames to product artifact types and
rejects unknown files by default.

Allowed user-facing inputs include:

- `v3_result_bundle.json` or release result bundle JSON.
- `v3_result_bundle.md` or release result bundle Markdown.
- Candidate summary JSON.
- Generated hypotheses summary JSON.
- Evidence summary JSON.
- Validation summary JSON.

Admin-only inputs include:

- Redacted trace.
- Redacted engine diagnostics.
- Redacted runtime summary.

Blocked inputs include:

- Raw Codex transcripts.
- Raw stdout and stderr.
- Raw logs.
- Cache files.
- `.env` files.
- Secrets and external credential details.
- Raw external payloads.
- Raw internal traces.
- Raw policy or governance internals.
- Raw repair internals.

When a run succeeds but no full result bundle is present, V0.3 can create a
summary fallback artifact from product-safe summary artifacts.

## Usage Checks

Run creation checks usage before writing a run:

- `run_discovery`: always checked for each run.
- `generated_hypotheses`: checked when generated hypotheses are enabled.
- `codex_task`: checked when Codex summary budgeting is enabled.

After run creation, V0.3 records:

- `run_discovery` usage.
- `generated_hypotheses` requested quantity when enabled.
- `codex_task` usage when enabled.

Export usage is intentionally deferred to V0.4/V0.5. No Stripe or paid
subscriptions are included in V0.3.

## Tenant Isolation

Tenant isolation is enforced in three layers:

1. Product API auth context resolves the active organization and role.
2. API queries filter by active `organization_id`, plus project and run ids.
3. Supabase RLS policies enforce organization membership for runs/artifacts.

Runs and artifacts always carry `organization_id`, `project_id`, and `run_id`.
Cross-organization run and artifact access is blocked. Admin-only diagnostics
are still tenant-scoped and require owner/admin role.

## UI Changes

V0.3 connects the web app to real product run state:

- Project run creation form posts to the product run API.
- Run detail page loads persisted run state and polls status.
- Result page loads the product-safe result bundle endpoint.
- Dashboard shows recent runs for the current organization.
- Project detail page lists project runs and result links.
- Usage page shows monthly discovery run usage and configured limits.

The result viewer remains summary-level. Candidate, evidence, and generated
hypothesis deep viewers from real artifacts move to V0.4.

## Local Mock Runner Instructions

Use deterministic mock output for local demos and tests:

```bash
cd apps/web
PRODUCT_ENGINE_RUNNER_MODE=mock npm run dev
```

Mock mode produces synthetic artifacts using placeholder names such as
`ExampleDiseaseA`, `ExampleTargetA`, and `ExampleCandidateA`. Mock artifacts are
marked:

```json
{
  "synthetic": true,
  "for_ui_test_only": true
}
```

Generated hypotheses in the mock runner have `direct_evidence=false`.

For local engine-wrapper work, configure:

```bash
PRODUCT_ENABLE_ENGINE_RUNNER=true
PRODUCT_ENGINE_COMMAND=molecule-ranker
PRODUCT_RUN_WORKDIR=/tmp/molcreate-product-runs
PRODUCT_RUN_TIMEOUT_SECONDS=120
PRODUCT_MAX_ARTIFACT_BYTES=1000000
```

Do not enable external writes or write-approved live mode for V0.3.

## Test Commands

Web checks:

```bash
cd apps/web
npm run lint
npm run typecheck
npm test
npm run build
```

Focused V0.3 checks:

```bash
cd apps/web
node --test \
  tests/product-run-safety.test.mjs \
  tests/product-artifacts.test.mjs \
  tests/mock-engine-runner.test.mjs \
  tests/product-copy-guardrails.test.mjs \
  tests/run-worker.test.mjs
```

Product and schema checks:

```bash
python -m pytest \
  tests/test_product_module.py \
  tests/test_supabase_product_auth_schema.py \
  tests/test_product_v0_3_discovery_workflow_docs.py \
  -q
```

## Security Checklist

- External writes disabled.
- Antibody generation disabled.
- Raw logs blocked.
- Raw Codex transcripts blocked.
- Raw AgentGraph exposure blocked.
- Raw traces blocked for normal users.
- User artifacts tenant-scoped.
- Admin diagnostics redacted and admin-only.
- Run usage checked before creation.
- Safe errors only.
- No stack traces, stdout, stderr, raw prompts, secrets, or credential details in
  normal user responses.

## Product Guardrails

The V0.3 result bundle is a research-planning artifact only. It is not clinical
validation, not medical advice, not a lab protocol, not a synthesis plan, and
not patient treatment guidance. Generated hypotheses are computational only and
require expert review.

## V0.3 Limitations

- Minimal worker only; not production-grade background infrastructure.
- Database-backed artifacts only for small product-safe payloads.
- No advanced result bundle viewer.
- No full candidate/evidence/generated UI from real artifacts.
- No billing-gated quotas.
- No Stripe or paid subscriptions.
- No enterprise SSO.
- No external writes.
- No antibody generation.
- No raw engine, Codex, trace, log, governance, cache, or repair internals
  exposed to normal users.

## What Moves To V0.4

V0.4 should build richer product result UX:

- Advanced result bundle summary and navigation.
- Product-safe candidate viewer from real artifacts.
- Product-safe evidence viewer from real artifacts.
- Product-safe generated hypotheses viewer from real artifacts.
- Better artifact list and download/export flows.
- Result review workflows and clearer human-review affordances.

## What Moves To V0.6

V0.6 should harden operations:

- Production-grade background jobs or queue workers.
- Durable artifact storage beyond small database-backed payloads.
- Supabase Storage or equivalent managed object storage.
- Worker monitoring, retries, and dead-letter handling.
- Deployment configuration and runtime observability.
- Operational run dashboards and alerting.
- Better cancellation and subprocess management.
