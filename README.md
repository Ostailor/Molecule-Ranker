# molecule-ranker

`molecule-ranker` is an internal research platform for source-backed molecule
ranking. Given a disease name, V0.9 resolves the disease through public
biomedical data sources,
discovers evidence-backed targets, retrieves existing molecules linked to those
targets, retrieves real literature evidence, ranks the molecules as transparent
research hypotheses, and can optionally generate target-conditioned in-silico
molecule hypotheses from those retrieved structures. V0.4 added computational
developability triage for existing and generated molecules. V0.5 added a local
expert review workspace for human-in-the-loop triage, dossiers, follow-up
requests, validation handoffs, feedback ingestion, and audit trails. V0.6 added
an experimental feedback loop and active-learning prioritization from
user-imported assay result files. V0.7 added a controlled Codex CLI provider as
the orchestration LLM layer for project planning, artifact inspection,
multi-run comparison summaries, review-assistant workflows, follow-up planning,
and engineering automation. V0.8 kept CLI/local mode working and added hosted
internal-platform primitives: user accounts, organizations, teams, RBAC,
project sharing, an authenticated dashboard, SQLite/PostgreSQL platform
metadata, job queueing, controlled Codex worker orchestration, audit logs, operational
health, admin controls, and data export/delete/retention controls. V0.9 adds
guarded external research-system integrations for ELN/LIMS, compound registry,
assay providers, generic REST APIs, generic file/SFTP-style drops, data
warehouses, and signed webhooks. Benchling is the first concrete connector.
Codex CLI can authenticate through local ChatGPT sign-in, so local LLM workflows do not
require an OpenAI API key when Codex CLI is already authenticated locally.

The app does not discover cures, does not claim generated molecules treat or are
active against a disease, does not provide medical advice, and does not provide
synthesis instructions, lab protocols, dosage, or patient treatment
instructions. V0.9 is an internal research platform MVP, not a regulated
clinical product. Ranked molecules and generated structures are research
hypotheses that require independent validation.
Generated molecules are computational hypotheses only: they are not known
actives, gain direct experimental evidence only from exact linked imported
results for the tested structure, and are ranked separately from existing
evidence-backed molecules unless explicitly requested otherwise.

## Current Scope Through V0.9

V0.9 implements existing-molecule ranking, opt-in generated hypotheses,
developability-aware computational triage, expert review workflows, and an
experimental feedback loop from user-imported assay result files, with Codex CLI
available as a guarded orchestration layer, hosted-mode platform services, and
external integration primitives:

- Resolve disease names to public biomedical disease entities with ambiguity handling.
- Retrieve real disease-associated targets with richer target identifiers and metadata.
- Retrieve existing molecules associated with those targets from ChEMBL mechanism,
  activity, assay, indication, and warning records where available.
- Retrieve PubMed literature records through NCBI E-utilities and extract
  citation-backed conservative claims from source-provided titles, abstracts,
  and snippets.
- Optionally enrich literature records with OpenAlex citation, open-access, and
  retraction metadata.
- Score candidates with a transparent component breakdown. Literature evidence
  is used as a research-prioritization modifier, not as proof of therapeutic
  efficacy and not as a replacement for database evidence.
- Generate target-conditioned novel candidate structures only when
  `--enable-generation` is passed or the `molecule-ranker generate` command is
  used. Generation is disabled by default.
- Use SELFIES mutation/crossover as the first generation backend over real
  retrieved seed molecules.
- Use RDKit for generated-structure validation, canonicalization, descriptors,
  fingerprints, similarity, and coarse chemistry filters.
- Assess physicochemical descriptors, drug-likeness heuristics, chemistry
  alerts, rule-based ADMET triage, toxicity-risk flags, synthetic-accessibility
  heuristics, synthesizability scoring, and chemical liability flags for
  existing and generated molecules when parseable structures are available.
- Apply a bounded developability adjustment to evidence-backed ranking scores.
- Optionally retrieve target structure metadata and optionally run docking only
  when explicitly enabled. Docking is disabled by default.
- Record optional structure-aware filter pass/fail state without claiming that a
  molecule is safe, binds a target, or is practically synthesizable.
- Rank generated structures separately from evidence-backed molecules.
- Write `candidates.json`, `generated_candidates.json`,
  `generation_trace.json`, `developability.json`,
  `developability_assessments.json`, `developability_report.md`, `report.md`,
  and `trace.json`.
- Cache real public API responses with source provenance and TTL.
- Provide adapter health checks and opt-in live smoke tests.
- Import user-supplied assay result CSV/JSON files, validate and normalize
  result records, link results to candidates and exact generated structures,
  summarize experimental evidence, adjust scores only when linked and
  QC-appropriate, and suggest active-learning batches for expert triage.
- Register local runs in a `ProjectWorkspace`, track artifacts through an
  `ArtifactRegistry`, compare multiple runs, generate a project dashboard, and
  expose a local JSON project API.
- Use `CodexCLIProvider` to call Codex CLI through subprocess execution with
  working-directory isolation, timeouts, dry-run and disabled modes, structured
  prompts, JSON validation, artifact manifests, guardrails, audit logs, and
  status capture.
- Run Codex-backed assistant commands for project planning, report
  summarization, candidate/run comparison summaries, review questions,
  active-learning explanations, and follow-up computational task planning.
- Run engineering automation commands for lint, typecheck, tests, and
  Codex-backed engineering plans.
- Store Codex-backed outputs in separate `codex_backbone` artifacts so they do
  not become biomedical evidence, assay results, generated molecules, review
  decisions, or score updates.
- Run Codex guardrails and evals for JSON validity, artifact grounding,
  citation fabrication, forbidden biomedical claims, and safe command planning.
- Run hosted mode with bearer-token authentication, user accounts,
  organizations, teams, project-level owner/editor/viewer permissions, audit
  logs, `/dashboard`, `/ops/health`, admin user controls, data export/delete,
  retention policies, and a central SQLite/PostgreSQL platform database.
- Queue hosted Codex work as allowlisted project jobs. Hosted API callers cannot
  submit arbitrary Codex prompts or shell-capable tasks; a `CodexWorker` builds
  server-side tasks from registered project artifacts.
- Configure external integration connectors for ELN/LIMS, compound registry,
  assay providers, Benchling, generic REST, generic CSV/SFTP-style staging,
  PostgreSQL-compatible warehouses, optional Databricks SQL and Snowflake
  connectors, signed webhooks, and a SiLA metadata-only adapter placeholder.
- Ingest webhooks and connector imports with source system, source record ID,
  sync job ID, timestamps, raw metadata, data-contract validation, sync audit
  logs, health checks, and an integration dashboard.
- Export curated molecule-ranker warehouse tables for candidates, generated
  molecules, targets, evidence, literature claims, developability assessments,
  assay results, review decisions, active-learning suggestions, sync jobs, and
  artifact manifests.
- Keep integration credentials as environment/vault references or hashed
  platform secrets; plaintext credentials are not stored or surfaced.
- Let Codex suggest external-ID mappings only as assistant output. Deterministic
  validation against observed source records must confirm mappings before use.

V0.9 does not:

- Create placeholder molecules.
- Use fixture biomedical data in production.
- Use hardcoded generated molecules.
- Invent fallback targets, molecules, evidence, citations, or scores.
- Invent evidence for generated molecules.
- Use LLMs to invent citations, paper claims, or biomedical relationships.
- Create fake citations or placeholder papers.
- Create synthesis protocols, retrosynthesis, synthesis planning, wet-lab,
  dosage, patient-treatment, or clinical guidance.
- Run docking unless it is explicitly enabled.
- Store full copyrighted articles.
- Claim that a molecule cures, treats, or is active against a disease.
- Claim ADMET predictions prove clinical safety.
- Claim docking scores prove binding.
- Claim synthetic-accessibility heuristics prove practical synthesizability.
- Provide synthesis routes, reagents, reaction conditions, or synthesis
  instructions.
- Make patient-specific recommendations.
- Fabricate assay results, infer assay outcomes from model scores, or treat
  surrogate model predictions as experimental evidence.
- Treat Codex CLI as a biomedical source of truth.
- Let Codex invent targets, molecules, assay results, citations, evidence, or
  scores.
- Let Codex directly alter scores without calling molecule-ranker scoring
  modules.
- Expose cache files, secrets, bearer tokens, API keys, or hidden environment
  files through the hosted API, dashboard, audit logs, artifacts, or Codex
  prompts.
- Run lab instruments, control devices, provide lab protocols, provide synthesis
  instructions, provide dosing, or provide patient-treatment guidance.
- Write to external systems by default. Connector modes default to dry-run,
  read-only, or sandbox; writes/exports require explicit config and permission.

Unit tests use mocked data only to test behavior deterministically. Production
code uses real public biomedical data adapters and fails if required data cannot
be retrieved.

## V0.9 Hosted Mode

Start the local API surface as before:

```bash
uv run molecule-ranker project serve --root . --host 127.0.0.1 --port 8765
```

Start the hosted MVP surface for internal use:

```bash
uv run molecule-ranker project serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Use `--platform-database-url sqlite:////absolute/path/platform.sqlite` when a
database URL is easier to inject from deployment configuration. For hosted
production, use a PostgreSQL URL such as
`postgresql+psycopg://user:password@host:5432/molecule_ranker`.

Hosted mode is intended for internal research teams. It requires bearer-token
authentication for project, artifact, audit, job, dashboard, and admin routes.
Create/bootstrap users through the CLI/API or by initializing the platform
database in application code. Artifact files may stay on local/object storage;
the platform database stores paths, hashes, and provenance metadata.

Database lifecycle commands:

```bash
uv run molecule-ranker db init --db-path .molecule-ranker/platform.sqlite
uv run molecule-ranker db migrate --database-url "$MOLECULE_RANKER_DATABASE_URL"
uv run molecule-ranker db check --database-url "$MOLECULE_RANKER_DATABASE_URL"
```

The platform database never stores plaintext passwords, API keys, Codex/ChatGPT
credentials, or unredacted secret-like audit metadata.

Authentication commands:

```bash
uv run molecule-ranker user create \
  --email admin@example.com \
  --password 'Strong-password-1' \
  --admin \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker user list --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker auth token create \
  --name automation \
  --user-id user-... \
  --created-by-user-id user-... \
  --scope project:read \
  --scope run:create \
  --db-path .molecule-ranker/platform.sqlite

uv run molecule-ranker auth token revoke \
  --token-id sat-... \
  --actor-user-id user-... \
  --db-path .molecule-ranker/platform.sqlite
```

### V0.9 Platform Controls

Authentication modes:

- `local_password`: internal/self-hosted deployments with strong password
  hashing and expiring bearer tokens.
- Service account tokens: automation tokens are hashed at rest, scoped, and
  shown only at creation time.
- OIDC: configurable issuer/client placeholders are present, but OIDC is
  optional and disabled cleanly when not configured.

RBAC is enforced for hosted API routes, dashboard pages, artifact downloads,
jobs, Codex tasks, and admin controls. Organization roles are `owner`, `admin`,
`scientist`, `reviewer`, `viewer`, and `service_account`. Project roles are
`project_owner`, `editor`, `reviewer`, `viewer`, and `runner`. Permission checks
cover `project:create`, `project:read`, `project:update`, `project:delete`,
`run:create`, `run:read`, `run:cancel`, `artifact:read`, `artifact:export`,
`review:read`, `review:write`, `experiment:import`, `experiment:read`,
`codex:run`, `codex:read`, `admin:manage_users`, `admin:manage_org`, and
`admin:view_audit`. Viewers are read-only, reviewers can review/comment without
changing project config, runners can enqueue/run jobs without managing users,
and Codex work requires explicit `codex:run` permission. Admin actions still
write audit events.

The hosted dashboard is server-rendered and login-gated. It includes project and
run views, ranking tables, generated molecule tables, developability,
experimental results, active learning, review queues, candidate dossiers, Codex
assistant output, audit logs, notifications, and admin pages. Every dashboard
view keeps research-use disclaimers visible, labels generated molecules as
computational hypotheses, separates experimental evidence from model
predictions, and shows Codex output separately from evidence.

Codex CLI remains the LLM backbone, but hosted mode never invokes arbitrary
API-triggered shell execution. Hosted Codex work is queued as a project job and
executed by a controlled `CodexWorker` with scoped artifact context, isolated
working directories, allowlisted task types, transcript redaction, and
pre/post-guardrails. Codex outputs are not evidence, assay results, molecules,
review decisions, or scores.

Observability includes structured JSON logs, request IDs, job IDs,
project/run IDs, health/readiness/version endpoints, audit logs, and metrics
such as `pipeline_runs_total`, `jobs_queued_total`, `jobs_failed_total`,
`codex_tasks_total`, `codex_guardrail_failures_total`,
`artifacts_written_total`, `auth_failures_total`, and
`api_request_duration_seconds`. Logs and metrics must not include passwords,
API keys, service tokens, Codex credentials, full imported assay files, or full
copyrighted article text.

Data governance controls include project export packages, user/project export
metadata, soft delete by default, hard delete only with explicit project-ID
confirmation, artifact retention, Codex transcript retention, audit retention,
cache retention, and assay-result retention. Exports exclude secrets and cache
files and include artifact manifests with hashes.

Deployment options:

- Local CLI mode: regular `molecule-ranker rank`, project, review, experiment,
  and Codex CLI commands still work without hosted services.
- Local web mode: run `molecule-ranker serve` on `127.0.0.1` with SQLite.
- Docker Compose internal deployment: use the provided compose files with
  mounted artifact/project storage and secrets supplied through environment
  variables.
- Optional Kubernetes manifests: deployment examples are provided under
  `deployment/k8s/` for teams that already operate Kubernetes.

### V0.9 Usage Examples

Create an admin user:

```bash
uv run molecule-ranker user create \
  --email admin@example.com \
  --password 'Strong-password-1' \
  --display-name "Platform Admin" \
  --admin \
  --db-path .molecule-ranker/platform.sqlite
```

Start the hosted server on localhost:

```bash
uv run molecule-ranker serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path .molecule-ranker/platform.sqlite
```

Start a background worker:

```bash
uv run molecule-ranker worker run \
  --db-path .molecule-ranker/platform.sqlite
```

Create a project in local CLI mode:

```bash
uv run molecule-ranker project create \
  --root ./research/parkinsons \
  --workspace-id parkinsons \
  --name "Parkinson Research"
```

Run a source-backed project job locally and register the output:

```bash
uv run molecule-ranker rank "Parkinson disease" \
  --top 10 \
  --output-dir ./research/parkinsons/results

uv run molecule-ranker project run \
  ./research/parkinsons/results/parkinson-disease \
  --root ./research/parkinsons \
  --run-id run-001
```

Open the hosted dashboard after login:

```bash
open http://127.0.0.1:8765/dashboard
```

Create a scoped service account token:

```bash
uv run molecule-ranker auth token create \
  --name automation \
  --user-id user-service-account \
  --created-by-user-id user-admin \
  --scope project:read \
  --scope run:create \
  --scope artifact:read \
  --db-path .molecule-ranker/platform.sqlite
```

Queue a hosted Codex-backed project summary. The API enqueues the job; a worker
runs Codex later through `CodexWorker`.

```bash
TOKEN="$(curl -s http://127.0.0.1:8765/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"Strong-password-1"}' \
  | jq -r '.access_token')"

curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8765/projects/parkinsons/codex/summarize
```

Export a project package:

```bash
uv run molecule-ranker platform export-project parkinsons \
  --output ./exports/parkinsons-export.zip \
  --actor-user-id user-admin \
  --db-path .molecule-ranker/platform.sqlite
```

Run retention cleanup. Defaults perform no automatic deletion unless retention
days are configured.

```bash
uv run molecule-ranker platform retention run \
  --actor-user-id user-admin \
  --cache-retention-days 7 \
  --codex-transcript-retention-days 90 \
  --db-path .molecule-ranker/platform.sqlite
```

Deploy with Docker Compose:

```bash
cp .env.example .env
# edit .env with real secrets and database settings
docker compose up --build
```

## V0.9 External Integrations

V0.9 adds a guarded external integration framework for internal research-system
handoffs and imports. Supported connector categories are:

- ELN/LIMS systems.
- Compound registries.
- Assay result providers.
- Generic REST APIs.
- Generic local/mounted file drops and SFTP-style staging interfaces.
- PostgreSQL-compatible data warehouses.
- Signed webhooks.

Benchling is the first concrete ELN/LIMS and registry connector. Generic REST
and generic file connectors cover systems that do not yet have a custom
connector. Warehouse exports support curated molecule-ranker tables such as
`mr_candidates`, `mr_generated_molecules`, `mr_targets`,
`mr_evidence_items`, `mr_literature_claims`,
`mr_developability_assessments`, `mr_assay_results`,
`mr_review_decisions`, `mr_active_learning_suggestions`, `mr_sync_jobs`, and
`mr_artifacts`. Databricks SQL and Snowflake connectors are optional dependency
paths. SiLA is present only as a metadata adapter placeholder; V0.9 does not
control instruments or devices.

Integration safety boundaries:

- Default connector mode is `read_only` or `dry_run`.
- External writes and exports require explicit `write_enabled` mode and
  permission.
- Credentials are stored as secret references, such as `env:BENCHLING_API_KEY`,
  not plaintext secret values.
- Secrets are redacted from logs, audit records, dashboards, artifacts, exports,
  and Codex prompts.
- Codex can assist with schema-mapping suggestions, sync-failure explanation,
  mapping-review questions, and export summaries, but it cannot activate
  mappings, write to external systems, invent external IDs, invent Benchling
  records, invent assay runs/results, or create evidence.
- Imported assay data must pass validation before it can become experimental
  evidence or affect scores.
- Export packages and integration outputs do not include lab protocols,
  synthesis instructions, dosing, patient treatment guidance, or clinical
  advice.

### Integration CLI Examples

Create an external system:

```bash
uv run molecule-ranker integration system create \
  --name "Benchling Dev" \
  --system-type eln \
  --vendor benchling \
  --base-url https://benchling.example \
  --mode dry_run \
  --db-path .molecule-ranker/platform.sqlite
```

Create a credential reference. The secret value stays in the environment; the
database stores only `env:BENCHLING_API_KEY`.

```bash
export BENCHLING_API_KEY="..."

uv run molecule-ranker integration credential create \
  --external-system-id ext-benchling-dev \
  --credential-type api_key \
  --secret-env-var BENCHLING_API_KEY \
  --root . \
  --json
```

Health check an integration configuration:

```bash
uv run molecule-ranker integration system health ext-benchling-dev \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Run a dry-run sync. This records a guarded sync job and does not write to an
external system.

```bash
uv run molecule-ranker integration sync run \
  --external-system-id ext-benchling-dev \
  --direction import \
  --object-type assay_results \
  --project-id project-1 \
  --dry-run \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Benchling registry mapping is handled as a pending mapping review item. Codex
may suggest a mapping, but deterministic validation and a human approval step
are required before activation.

```bash
uv run molecule-ranker integration mapping list \
  --project-id project-1 \
  --status pending_review \
  --db-path .molecule-ranker/platform.sqlite \
  --json

uv run molecule-ranker integration mapping approve mapping-123 \
  --db-path .molecule-ranker/platform.sqlite
```

Prepare a Benchling assay-result import in dry-run mode:

```bash
uv run molecule-ranker integration benchling import-assay-results \
  --external-system-id ext-benchling-dev \
  --project-id project-1 \
  --dry-run \
  --json
```

Import assay results from a generic file drop. The file connector reads only
inside its configured root, blocks path traversal, and validates imported assay
results before they can affect scoring.

```bash
uv run molecule-ranker integration sync run \
  --external-system-id ext-generic-file \
  --direction import \
  --object-type assay_results \
  --project-id project-1 \
  --dry-run \
  --db-path .molecule-ranker/platform.sqlite
```

Export curated warehouse tables as local CSV artifacts:

```bash
uv run molecule-ranker integration warehouse export \
  --project-id project-1 \
  --external-system-id ext-warehouse \
  --tables candidates,assay_results,review_decisions \
  --format csv \
  --dry-run \
  --output-dir .molecule-ranker/warehouse-export \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Inspect a sync job:

```bash
uv run molecule-ranker integration sync show sync-... \
  --db-path .molecule-ranker/platform.sqlite \
  --json
```

Generate a signed webhook test payload:

```bash
uv run molecule-ranker integration webhook test \
  --external-system-id ext-benchling-dev \
  --secret "$WEBHOOK_SIGNING_SECRET" \
  --event-type assay_result.created \
  --json
```

API authentication uses bearer tokens. `POST /auth/login` returns a short-lived
access token plus a refresh token; `POST /auth/refresh` issues a new access
token; `POST /auth/logout` invalidates the session. Browser cookie sessions are
not used in V0.9, so CSRF protection is not part of the API bearer-token flow.
OIDC settings are present as placeholders and `/auth/oidc/*` routes return a
clean disabled response unless configured.

## V0.9 Codex CLI Orchestration

V0.9 keeps Codex CLI as the primary LLM agent backbone for molecule-ranker. Codex
CLI is used because OpenAI documents Codex as included with ChatGPT Plus, Pro,
Business, Enterprise/Edu, and other eligible plans, and the CLI can run through
local ChatGPT authentication instead of requiring project-specific OpenAI API
keys. Usage is still subject to the user's ChatGPT/Codex plan limits and local
Codex configuration. See OpenAI's Codex plan documentation:
<https://help.openai.com/en/articles/11369540-codex-in-chatgpt>.

Codex CLI orchestrates, summarizes, explains, compares, and plans. It can
inspect local molecule-ranker artifacts, draft source-grounded summaries, build
review-assistant questions, explain uncertainty, compare runs or candidates,
plan safe follow-up CLI tasks, and help with engineering test/lint/typecheck
loops.

Codex CLI is not the biomedical truth layer. Deterministic source-backed tools
remain authoritative for disease resolution, target evidence, molecule
retrieval, literature evidence, generated hypotheses, developability triage,
experimental result ingestion, assay-result linking, and score recalibration.
Codex-backed outputs are stored separately as `codex_backbone` artifacts and do
not directly alter candidates, evidence, assay results, generated molecules,
review decisions, or score fields.

Codex CLI must not:

- Create biomedical evidence.
- Directly change scores.
- Fabricate citations, assay results, targets, molecules, evidence, or paper
  claims.
- Provide medical advice, synthesis instructions, lab protocols, animal or
  human dosing, or patient treatment guidance.
- Claim that a molecule cures, treats, binds, is active, is safe, is effective,
  or is synthesizable unless that claim is explicitly present in source-backed
  artifacts and still framed as artifact context rather than a new conclusion.

Guardrails and evals are included. Prompt and output checks redact secrets,
exclude cache/secret artifacts, reject fabrication requests, flag fake
citations and assay results, block protocol/synthesis/dosing/treatment content,
validate JSON, and evaluate artifact grounding and safe command planning.

Register runs in a local project workspace:

```bash
uv run molecule-ranker project init --root .
uv run molecule-ranker project register-run results/example-condition --run-id run-1
uv run molecule-ranker project list
```

Compare registered runs and generate a dashboard:

```bash
uv run molecule-ranker project compare --run-id run-1 --run-id run-2
uv run molecule-ranker project dashboard --output-dir .molecule-ranker/dashboard
```

Start the local JSON API server:

```bash
uv run molecule-ranker project serve --host 127.0.0.1 --port 8765
```

Check local Codex CLI status without printing credentials:

```bash
uv run molecule-ranker codex status --json
```

Summarize a completed run with Codex:

```bash
uv run molecule-ranker codex summarize-run \
  results/alzheimer-disease/ \
  --dry-run \
  --json
```

Explain a candidate using existing artifacts only:

```bash
uv run molecule-ranker codex explain-candidate \
  results/alzheimer-disease/ \
  --candidate "ExampleCandidate" \
  --dry-run \
  --json
```

Plan a safe follow-up run:

```bash
uv run molecule-ranker codex plan-followup \
  results/alzheimer-disease/ \
  --dry-run \
  --json
```

Compare two run directories:

```bash
uv run molecule-ranker codex compare-runs \
  results/alzheimer-disease-run-a/ \
  results/alzheimer-disease-run-b/ \
  --dry-run \
  --json
```

Run Codex evals:

```bash
uv run molecule-ranker codex eval \
  --cases tests/fixtures/codex_eval_cases.json \
  --json
```

Run an engineering test-loop analysis with Codex:

```bash
uv run molecule-ranker codex test-loop \
  --test-output test_output.txt \
  --json
```

## V0.5 Expert Review Workflow

V0.5 adds an optional expert review workflow and human-in-the-loop triage system.
The ranking pipeline still works without review enabled. When enabled, V0.5
creates a local `ReviewWorkspace` backed by SQLite, writes `review_queue.json`,
and can optionally generate a local static HTML dashboard.

The review workflow is local tooling, not a multi-user production system. The
SQLite review database defaults to `.review/molecule-ranker-review.sqlite` and
stores local reviewer identity metadata only; it does not add authentication,
authorization, collaboration controls, or a SaaS deployment model.

Review decisions are stored separately from scientific evidence and model
scores. They are expert triage labels, not biomedical evidence, clinical
conclusions, or proof of safety, efficacy, binding, or synthesizability.
Expert feedback can inform future prioritization only when
`enable_feedback_prior` or `--enable-feedback-prior` is explicitly enabled, and
it remains labeled as expert review feedback rather than experimental evidence.

Candidate dossiers summarize evidence, risks, uncertainty, source provenance,
limitations, reviewer decisions, comments, and follow-up requests. Validation
handoff packets are high-level expert-planning packets: they can name broad
validation categories such as target engagement, cellular pathway, phenotype,
or toxicology triage, but they do not include lab protocols, operational steps,
synthesis instructions, reagents, reaction conditions, temperatures, dosing, or
patient treatment instructions. No clinical advice, dosage, or treatment
instructions are provided anywhere in the review workflow.

Generated molecules remain computational hypotheses. They have no direct
experimental evidence, are not claimed to be active, and remain labeled as
generated throughout review queues, dossiers, comparisons, handoffs, exports,
reports, and dashboards.

Review objects include `ReviewWorkspace`, `ReviewQueue`, `ReviewItem`,
`ReviewerDecision`, `ExpertFeedback`, `CandidateDossier`, `ValidationHandoff`,
`ReviewAuditLog`, `FeedbackIngestionAgent`, and `DossierWriterAgent`.

Run ranking with the review workflow enabled:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-name "Local Reviewer" \
  --reviewer-role medicinal_chemist \
  --max-review-items 100 \
  --generate-review-dashboard
```

Create a review workspace from existing run artifacts:

```bash
uv run molecule-ranker review create \
  --from-run results/alzheimer-disease/ \
  --db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-name "Local Reviewer" \
  --reviewer-role medicinal_chemist
```

List review workspaces:

```bash
uv run molecule-ranker review list \
  --db-path .review/molecule-ranker-review.sqlite
```

Show a review item:

```bash
uv run molecule-ranker review item \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite
```

Make a review decision:

```bash
uv run molecule-ranker review decide \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite \
  --decision needs_more_data \
  --rationale "Expert triage label only; request more disease-specific evidence." \
  --reviewer-id reviewer-1 \
  --confidence 0.7 \
  --factor weak_literature
```

Add a reviewer comment:

```bash
uv run molecule-ranker review comment \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  --db-path .review/molecule-ranker-review.sqlite \
  --comment "Check whether the target rationale is disease-specific." \
  --comment-type evidence_question \
  --reviewer-id reviewer-1
```

Compare candidates side by side:

```bash
uv run molecule-ranker review compare \
  workspace-run-1-alzheimer-disease \
  review-item-run-1-chembl123 \
  review-item-run-1-generated-maob-001 \
  --db-path .review/molecule-ranker-review.sqlite
```

Generate a candidate dossier:

```bash
uv run molecule-ranker review dossier \
  --workspace results/alzheimer-disease/review_queue.json \
  --item-id review-item-run-1-chembl123 \
  --output results/alzheimer-disease/dossiers/chembl123.md
```

Create a validation handoff packet:

```bash
uv run molecule-ranker review handoff \
  --workspace results/alzheimer-disease/review_queue.json \
  --item-id review-item-run-1-chembl123 \
  --reviewer-id reviewer-1 \
  --output results/alzheimer-disease/validation_handoffs/chembl123.json
```

Export a review package:

```bash
uv run molecule-ranker review export \
  workspace-run-1-alzheimer-disease \
  --db-path .review/molecule-ranker-review.sqlite \
  --format zip \
  --output results/alzheimer-disease/review_export.zip
```

Generate a local static dashboard:

```bash
uv run molecule-ranker review dashboard \
  workspace-run-1-alzheimer-disease \
  --db-path .review/molecule-ranker-review.sqlite \
  --output results/alzheimer-disease/review_dashboard/
```

## V0.6 Experimental Feedback Loop

V0.6 adds a local experimental feedback loop and active-learning prioritization
from assay result files. Assay results are imported by the user from CSV or JSON
files; the software does not fabricate assay results, infer assay outcomes from
model scores, or treat missing result data as evidence.

Experimental evidence is kept separate from database evidence, literature
evidence, expert review feedback, and model predictions. Imported assay results
can affect candidate scores only when they are linked to the candidate or exact
generated structure and satisfy the configured QC policy. Failed-QC and
inconclusive results are recorded for provenance, summaries, and audit trails,
but they do not add score support.

In-vitro, biochemical, cellular, safety, and developability assay results do
not imply clinical efficacy, clinical safety, cure, treatment, or patient
benefit. Generated molecules gain direct experimental evidence only from exact
linked imported results for the tested structure; seed-molecule and analog
results are not generalized to generated molecules. Active-learning suggestions
are expert-triage suggestions only, not instructions to run experiments.

V0.6 does not provide lab protocols, synthesis instructions, synthesis routes,
reagent recipes, animal or human dosing, or patient treatment guidance. Optional
surrogate models are local estimates for prioritization only and are not
experimental evidence.

Safe neutral import templates are available under `templates/`:

```bash
templates/assay_results_template.csv
templates/assay_results_template.json
templates/README.md
```

Import an assay CSV:

```bash
uv run molecule-ranker experiment import templates/assay_results_template.csv \
  --db-path .experiments/results.sqlite \
  --imported-by researcher-1
```

Dry-run an import without writing the database:

```bash
uv run molecule-ranker experiment import templates/assay_results_template.csv \
  --db-path .experiments/results.sqlite \
  --dry-run \
  --json
```

List imported assay results:

```bash
uv run molecule-ranker experiment list \
  --db-path .experiments/results.sqlite \
  --target-symbol EXAMPLE \
  --outcome-label positive
```

Summarize imported results for a candidate:

```bash
uv run molecule-ranker experiment summarize \
  --candidate-name ExampleCandidateA \
  --db-path .experiments/results.sqlite
```

Link imported results to a saved run artifact:

```bash
uv run molecule-ranker experiment link \
  --from-run results/example-condition/ \
  --db-path .experiments/results.sqlite \
  --json
```

Rank with experimental evidence enabled:

```bash
uv run molecule-ranker rank "Example condition" \
  --top 10 \
  --enable-experimental-evidence \
  --experimental-db-path .experiments/results.sqlite \
  --strict-experimental-linking \
  --require-qc-passed-for-score
```

Generate an active-learning batch for expert triage:

```bash
uv run molecule-ranker experiment active-learning \
  --from-run results/example-condition/ \
  --db-path .experiments/results.sqlite \
  --strategy balanced \
  --batch-size 10 \
  --include-generated \
  --json
```

Export experimental results:

```bash
uv run molecule-ranker experiment export \
  --db-path .experiments/results.sqlite \
  --output results/example-condition/experimental_results_export.json
```

Create a high-level experimental report:

```bash
uv run molecule-ranker experiment report \
  --db-path .experiments/results.sqlite \
  --from-run results/example-condition/
```

## Install

Python 3.11+ is required. The repository is configured for `uv`:

```bash
uv sync
```

To verify the command is available:

```bash
uv run molecule-ranker --help
uv run molecule-ranker rank --help
```

## CLI Usage

Run normal ranking without generated molecules:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-generation
```

Generation is disabled by default, so the same behavior is used when no
generation option is supplied:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Normal V0.6 ranking includes PubMed literature retrieval and developability
triage by default:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-literature \
  --literature-source pubmed \
  --openalex-enrichment
```

Run normal ranking with developability controls made explicit:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-developability \
  --developability-filter-mode filter_generated_only \
  --reject-critical-alerts
```

Run without literature evidence when you only want database-derived ranking:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-literature
```

Disable developability triage when you only want the V0.1-V0.3 evidence and
generation behavior:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --disable-developability
```

Use strict literature mode when PubMed availability is required for the run:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --strict-literature
```

Write the normal report files and print a JSON summary:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --json
```

Run ranking with target-conditioned generated molecule hypotheses:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --enable-developability \
  --max-generation-objectives 3 \
  --generated-per-objective 10 \
  --max-retained-generated 10 \
  --generation-random-seed 123
```

Run ranking with the optional V0.5 review workflow:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-role medicinal_chemist \
  --generate-review-dashboard
```

Run the generation-focused command. It still runs disease, target, molecule,
and literature retrieval first, but focuses the terminal output on generated
hypotheses:

```bash
uv run molecule-ranker generate "Alzheimer disease" \
  --top 10 \
  --max-retained-generated 25 \
  --generation-random-seed 123
```

Run developability assessment later from an existing candidate artifact without
rerunning disease, target, molecule, or literature retrieval:

```bash
uv run molecule-ranker assess-developability \
  --input results/alzheimer-disease/generated_candidates.json
```

Print a JSON CLI summary for a run that includes generated molecules. The
summary includes generated counts and output paths; generated structures are
written to `generated_candidates.json`.

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-generation \
  --max-retained-generated 10 \
  --json
```

Inspect the retained generated structures from the generated JSON artifact:

```bash
jq '.retained_generated_molecules[] | {generated_id, canonical_smiles, inchi_key, generation_score}' \
  results/alzheimer-disease/generated_candidates.json
```

Benchmark a generated-molecule artifact with internal V0.3 quality metrics:

```bash
uv run molecule-ranker benchmark-generation \
  --input results/alzheimer-disease/generated_candidates.json
```

Benchmark a V0.4 developability artifact with internal coverage and calibration
metrics:

```bash
uv run molecule-ranker benchmark-developability \
  --input results/alzheimer-disease/developability.json
```

Enable optional target structure metadata retrieval. This retrieves structure
metadata for computational triage; it does not require docking:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-structure-retrieval \
  --max-structures-per-target 5
```

Enable optional docking only when you explicitly want the docking plugin path.
Docking is disabled by default, docking inputs must be reviewed, and docking
scores do not prove binding:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --enable-structure-retrieval \
  --enable-docking \
  --max-docked-molecules 5
```

Useful options:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --use-cache \
  --cache-dir .cache/molecule-ranker \
  --cache-ttl-hours 24 \
  --max-targets 25 \
  --max-molecules-per-target 10 \
  --max-activity-records-per-target 10 \
  --max-indications-per-molecule 20 \
  --max-warnings-per-molecule 20 \
  --max-literature-queries 100 \
  --max-papers-per-query 10 \
  --max-targets-for-literature 10 \
  --max-candidates-for-literature 20 \
  --enable-generation \
  --generation-method selfies_mutation \
  --max-seed-molecules 20 \
  --max-generation-objectives 10 \
  --generated-per-objective 50 \
  --max-retained-generated 50 \
  --generation-random-seed 123 \
  --include-generated-in-main-ranking \
  --reject-basic-alerts \
  --enable-developability \
  --strict-developability \
  --developability-filter-mode filter_generated_only \
  --reject-critical-alerts \
  --reject-high-toxicity-risk \
  --enable-structure-retrieval \
  --max-structures-per-target 5 \
  --enable-review-workflow \
  --review-db-path .review/molecule-ranker-review.sqlite \
  --reviewer-id reviewer-1 \
  --reviewer-role medicinal_chemist \
  --max-review-items 100 \
  --include-generated-in-review \
  --generate-review-dashboard \
  --enable-experimental-evidence \
  --experimental-db-path .experiments/results.sqlite \
  --strict-experimental-linking \
  --require-qc-passed-for-score \
  --ncbi-email researcher@example.org \
  --ncbi-api-key-env NCBI_API_KEY \
  --max-retries 3 \
  --retry-backoff-seconds 0.5 \
  --verbose
```

By default, ranking requests use the live public APIs first and write successful
real JSON responses to the configured cache directory. Cached responses are not
read as an offline substitute unless `--use-cache` is explicitly passed; that
mode is a cached-real-data fallback for previously retrieved successful
responses. Use `--no-cache` to bypass cache reads and writes.

Important V0.6 configuration options map to typed `RankerConfig` fields:

- `results_dir`, `cache_dir`: output and successful-real-response cache locations.
- `use_cache`: enables cache writes; disabled by `--no-cache`.
- `allow_cached_real_data`: enables cached-real-data fallback; enabled by `--use-cache`.
- `cache_ttl_seconds`: TTL for cached successful real responses.
- `default_top`: ranked candidates retained.
- `default_target_limit`: evidence-backed targets retained after target discovery.
- `target_source_limit`: Open Targets source retrieval size before local filtering.
- `max_molecules_per_target`: ChEMBL mechanism/molecule records retained per target.
- `max_activity_records_per_target`: ChEMBL activity records retained per target.
- `max_indications_per_molecule`, `max_warnings_per_molecule`: ChEMBL clinical and warning context retained per molecule.
- `enable_literature`: includes or skips literature retrieval.
- `strict_literature`: if `false`, source failures are warned and the run
  continues when database evidence is sufficient; if `true`, literature source
  failures stop the run.
- `literature_sources`: currently supports `pubmed`; PubMed is the primary
  literature evidence source.
- `enable_openalex_enrichment`: enables optional OpenAlex citation,
  open-access, topic, and retraction enrichment.
- `max_literature_queries`, `max_papers_per_query`: literature query and paper limits.
- `max_targets_for_literature`, `max_candidates_for_literature`: entity limits for literature query generation.
- `ncbi_tool`, `ncbi_email`, `ncbi_api_key`: NCBI E-utilities identification and optional API-key configuration.
- `literature_request_timeout_seconds`, `literature_max_retries`, `literature_cache_ttl_seconds`: literature adapter request behavior.
- `request_timeout_seconds`, `max_retries`, `retry_backoff_seconds`: live API request behavior.
- `strict_enrichment`: records strict enrichment intent for runs that should treat optional enrichment more conservatively.
- `enable_generation`: opt-in switch for generated molecule hypotheses.
- `strict_generation`: fails when enabled generation cannot produce retained
  hypotheses; default mode warns and continues.
- `include_generated_in_main_ranking`: optionally includes generated hypotheses
  in the main ranking while preserving `origin="generated"` and no direct
  evidence.
- `generation_method`: generated molecule backend; V0.3 supports
  `selfies_mutation`.
- `generation_random_seed`: optional deterministic random seed.
- `max_seed_molecules`, `max_generation_objectives`, `generated_per_objective`,
  `max_generated_before_filtering`, `max_retained_generated`: generation size
  and retention controls.
- `duplicate_similarity_threshold`, `near_duplicate_similarity_threshold`,
  `distant_similarity_threshold`, `reject_distant_generated`: novelty and
  target-conditioning filters using Morgan-fingerprint Tanimoto similarity.
- `reject_basic_alerts`, `allowed_generation_elements`: coarse chemistry sanity
  filters for generated structures.
- `enable_developability`: enables V0.4 developability assessment by default.
- `strict_developability`: fails the run when developability assessment fails
  for a molecule instead of recording an unknown-risk assessment.
- `assess_existing_molecules`, `assess_generated_molecules`: choose which
  molecule classes receive developability triage.
- `developability_filter_mode`: action mode; existing evidence-backed
  molecules are not silently removed by default, while generated molecules may
  be filtered more aggressively.
- `reject_critical_alerts`, `reject_high_toxicity_risk`, `alert_mode`: control
  how chemistry alerts and toxicity-risk flags affect recommendations and
  filtering.
- `enable_rule_based_admet`, `enable_local_admet_models`,
  `allow_rule_based_admet_fallback`: ADMET triage controls. The default
  rule-based ADMET baseline is a computational triage heuristic and does not
  prove safety.
- `enable_synthesizability`: enables coarse synthesizability scoring. It does
  not provide synthesis routes or practical synthesis instructions.
- `enable_structure_retrieval`: optionally retrieves target structure metadata.
- `enable_docking`: optionally runs docking plugin paths. It is disabled by
  default and docking scores do not prove binding.
- `strict_structure_mode`, `write_docking_artifacts`,
  `max_structures_per_target`, `max_docked_molecules`: optional
  structure/docking behavior controls.
- `enable_structure_filtering`: records structure-aware developability filter
  pass/fail fields.
- `filter_developability_failures`: optionally removes candidates that fail the
  configured developability threshold.
- `min_developability_score`: threshold for optional structure-aware filtering.
- `enable_tdc_benchmark`, `tdc_data_dir`: optional benchmark controls for local
  ADMET model evaluation if TDC is installed and explicitly enabled.
- `enable_review_workflow`: opt-in switch for local expert review workspace
  creation during ranking or generation.
- `review_db_path`: local SQLite path for review workspaces. This is local
  persistence, not a multi-user production database.
- `reviewer_id`, `reviewer_name`, `reviewer_role`: optional local reviewer
  identity metadata.
- `max_review_items`: maximum review queue size.
- `include_generated_in_review`: includes generated hypotheses in the review
  queue while preserving `candidate_origin="generated"`.
- `generated_high_priority_allowed`: controls whether generated hypotheses may
  receive high-priority review buckets; disabled by default.
- `review_priority_policy`: review queue prioritization policy; default is
  conservative.
- `enable_feedback_prior`: explicitly enables expert feedback as future
  prioritization context. Disabled by default.
- `feedback_db_path`, `feedback_weight`: local feedback store and weighting for
  feedback-prior behavior.
- `generate_review_dashboard`, `review_dashboard_dir`: optional static local
  dashboard generation.
- `enable_experimental_evidence`: opt-in switch for imported assay result
  linking and scoring.
- `experimental_db_path`: local SQLite path for imported assay results.
- `experimental_result_source_filter`: optional filter for imported result
  source labels such as `csv_import` or `json_import`.
- `require_qc_passed_for_score`: requires QC-passed imported results before
  score support is added. Failed-QC results remain auditable but do not improve
  scores.
- `include_inconclusive_results`: records inconclusive imported results in
  experimental summaries while keeping them non-score-promoting.
- `strict_experimental_linking`: requires exact candidate, InChIKey, canonical
  SMILES, generated ID, or review-item links by default.
- `enable_local_admet_models` and the optional `surrogate` dependency group:
  local surrogate models may estimate assay outcomes for prioritization, but
  those estimates are not experimental evidence and are not converted into
  `EvidenceItem` objects.

The effective config is serialized into `trace.json` so a run can be audited
with the limits, cache policy, and request policy that produced it. Defaults are
chosen for a first real run and do not reduce target discovery to a single
target.

Check live public adapter reachability without running a ranking job:

```bash
uv run molecule-ranker health
```

The health command probes Open Targets, ChEMBL, PubChem, PubMed, and OpenAlex with short request
timeouts and prints source, status, latency, endpoint, and any error. Health
checks are only run when this command is requested.

Run live public API smoke tests explicitly:

```bash
MOLECULE_RANKER_RUN_LIVE=1 uv run pytest -m live tests_live/
```

JSON summary output:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10 --json
```

Files are written under:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/generated_candidates.json
results/<disease_slug>/generated_molecules.json
results/<disease_slug>/generation_trace.json
results/<disease_slug>/developability.json
results/<disease_slug>/developability_report.md
results/<disease_slug>/developability_assessments.json
results/<disease_slug>/experimental_results.json
results/<disease_slug>/experimental_evidence.json
results/<disease_slug>/active_learning_batch.json
results/<disease_slug>/experimental_report.md
results/<disease_slug>/trace.json
```

`generated_candidates.json` is written when generation is enabled.
`generated_molecules.json` is a compatibility alias with the same payload. The
payload includes objectives, selected seeds, retained generated molecules,
rejected generated molecules with rejection reasons, generation warnings,
generation config, and limitations. Generated structures include SMILES and
InChIKey when available, but no synthesis instructions and no generated
`EvidenceItem` claims.

`developability.json` is written when developability is enabled or explicitly
disabled. When disabled it clearly reports `enabled=false` and `success=false`.
When enabled, it includes assessed counts, retained/deprioritized/rejected
counts, risk distribution, alert distribution, ADMET endpoint coverage,
individual assessments, warnings, limitations, config, and generation time.
Candidate artifacts include compact developability summaries; generated
candidate artifacts also include rejection reasons.

No static example biomedical result is included in this README because example
rankings should only be copied from an actual successful live run with its
retrieval timestamp and source provenance.

## Internet and API Assumptions

The CLI uses public internet APIs at runtime. A successful run assumes:

- Network access is available.
- Public sources are reachable and not rate-limited.
- Source schemas still match the adapter expectations.
- The queried disease can be resolved to a public disease entity.
- Evidence-backed targets and molecules exist in the queried sources.

If real data cannot be retrieved, the app fails instead of inventing results.

## Data Sources Used

Production adapters are isolated under `molecule_ranker/data_sources/`:

- Open Targets: disease resolution and disease-target association evidence.
- ChEMBL: target-linked existing molecules, mechanisms, activities, assays,
  indications, drug warnings, and development status where available.
- PubChem: molecule identifier and chemical metadata enrichment where available.
- PubMed: real paper records and abstracts via NCBI E-utilities.
- OpenAlex: optional citation count, open-access, and retraction metadata.
- RCSB PDB: optional target structure metadata when explicitly enabled.
- AlphaFold DB: optional predicted target structure metadata when explicitly
  enabled.

HTTP requests are made only inside adapter classes. Tests may mock adapter
responses, but production code does not import test fixtures or ship fixture
biomedical knowledge.

## Generated Molecule Hypotheses

V0.3 added target-conditioned novel molecule generation as an opt-in workflow.
Generation is off for ordinary ranking runs unless the user passes
`--enable-generation` or uses `molecule-ranker generate`.

The generation pipeline:

1. Selects real retrieved existing molecules as seeds.
2. Builds generation objectives for evidence-backed targets with selected seeds.
3. Uses SELFIES mutation, insertion, deletion, and seed-seed crossover as the
   first backend.
4. Decodes generated SELFIES into structures and validates them with RDKit.
5. Canonicalizes SMILES, computes InChIKey when possible, descriptors,
   fingerprints, and Tanimoto similarity.
6. Filters invalid, duplicate, near-duplicate, distant, and chemically
   unreasonable structures using coarse generation rules.
7. Scores retained generated molecules separately from existing
   evidence-backed molecules.

Generated molecules are computational structures and research hypotheses. They
are not known actives, do not have direct experimental evidence, and are not
claimed to bind targets, modulate targets, treat disease, or be safe. Their
scores are generation-prioritization scores based on seed and target context,
not efficacy predictions. No fake evidence is generated for them.

V0.4 adds developability triage after generation and before evidence scoring.
The triage uses physicochemical descriptors, drug-likeness heuristics,
chemistry alerts, rule-based ADMET triage, toxicity-risk flags,
synthetic-accessibility heuristics, synthesizability scoring, chemical liability
flags, and optional structure-aware filter state. These outputs are
computational risk flags only and require medicinal chemistry, toxicology,
pharmacology, synthesis, and domain expert review. ADMET predictions do not
prove safety. Synthesizability scoring does not provide synthesis routes and
does not prove practical synthesizability. V0.4 does not implement
retrosynthesis, synthesis planning, wet-lab prediction, dosage, patient
treatment, or clinical guidance. No synthesis instructions are provided.

Structure retrieval and docking are optional. Structure retrieval is metadata
only unless additional structure-aware filtering is enabled. Docking is disabled
by default, must be explicitly requested, and docking scores do not prove
binding. Docking results are weak computational heuristics, not experimental
evidence.

Existing evidence-backed molecules are not silently removed by default because
disease/target evidence remains separate from developability risk. Generated
molecules may be filtered more aggressively because they have no direct
experimental evidence unless future real evidence is retrieved.

## Literature Evidence Policy

PubMed is the primary literature source. The literature module searches
PubMed, retrieves paper metadata and source-provided abstracts through NCBI
E-utilities, deduplicates papers, extracts citations, and applies conservative
rule-based claim extraction. OpenAlex enrichment is optional and is used for
citation count, open-access, concept/topic, landing-page, and retraction
metadata; it is not the primary biomedical evidence source.

Literature source failures are configurable:

- Default mode: warn and continue when literature retrieval or optional
  enrichment fails and database evidence is sufficient.
- Strict mode: fail the run when a required literature source is unavailable.

Scientific-integrity rules:

- No fake citations, papers, PMIDs, DOIs, or OpenAlex IDs are created.
- No full copyrighted articles are stored.
- Claims are extracted only from title, abstract, metadata, and
  source-provided snippets.
- Mention-only evidence is labeled as mention-only and is not treated as proof.
- Clinical literature is distinguished from preclinical, review, computational,
  in-vitro, animal, case-report, and unknown evidence.
- Safety and contradictory literature can reduce scores or confidence.
- A citation is never described as proving therapeutic efficacy unless the
  extracted claim is `clinical_support` and the queried molecule and disease
  are both present.

## Agent Architecture

The orchestrator runs agents in this order:

1. `DiseaseResolverAgent`
2. `TargetDiscoveryAgent`
3. `MoleculeRetrievalAgent`
4. `LiteratureEvidenceAgent`
5. `NovelMoleculeAgent`
6. `DevelopabilityAssessmentAgent`
7. `EvidenceScoringAgent`
8. `ReportWriterAgent`

Each successful agent appends an `AgentTrace`. Critical data failures stop the
pipeline and prevent a normal success report from being written.

Core schemas are Pydantic models:

- `Disease`
- `EvidenceItem`
- `LiteratureQuery`
- `LiteraturePaper`
- `Citation`
- `EvidenceClaim`
- `LiteratureEvidenceItem`
- `LiteratureEvidenceBundle`
- `Target`
- `MoleculeCandidate`
- `GeneratedMoleculeHypothesis`
- `ScoreBreakdown`
- `AgentTrace`
- `RankingRun`

## Scoring Formula

V0.4 uses a deterministic transparent heuristic over retrieved evidence. Without
supported literature evidence or developability assessment, the base formula is:

```text
final_score =
  0.25 * disease_target_relevance +
  0.20 * molecule_target_evidence +
  0.20 * mechanism_plausibility +
  0.10 * clinical_precedence +
  0.10 * safety_prior +
  0.10 * data_quality +
  0.05 * novelty_or_repurposing_value
```

Every component is bounded between 0 and 1. Components are derived only from
retrieved target scores, molecule evidence, mechanisms, activity potency, assay
metadata, indications, warnings, development status, source diversity,
identifiers, and provenance. When conservative literature claims are present,
they act as bounded modifiers to existing components:

- Disease-target literature may modestly increase disease-target relevance.
- Molecule-target and mechanism literature may modestly increase
  molecule-target evidence and mechanism plausibility.
- Clinical literature can increase clinical precedence only when the queried
  molecule and disease are both present; review articles alone do not count as
  clinical precedence.
- Mention-only literature has minimal effect.
- Safety or contradictory literature can lower safety prior and confidence.
- Retracted records do not improve scores.

When developability assessment is available, V0.4 applies a bounded adjustment
using the heuristic developability score and conservative penalties for
review/high-risk/insufficient-structure flags. This is a computational
triage adjustment, not a safety conclusion.

Scores are prioritization aids, not validated predictions of efficacy or safety.

Generated molecule hypotheses are scored separately by seed similarity,
target-relevance context, and basic RDKit descriptor fit. This score is a
generation-prioritization heuristic only. It is not evidence of disease
activity, target engagement, safety, efficacy, practical synthesizability, or clinical
utility. Generated molecules have no direct experimental evidence attached, and
no fake evidence records are created for them.

Every ranked candidate includes:

- Final score.
- Confidence.
- Component-level score breakdown.
- Human-readable explanation.
- Evidence summaries.
- Literature evidence summaries with citations or explicit absence labels.
- Source provenance.
- Warnings for missing or heuristic evidence dimensions.

## Fail-Fast Behavior

The pipeline stops on:

- Disease resolution failure.
- Target discovery failure.
- Molecule retrieval failure.
- No evidence-backed candidates.
- External API unavailability.
- Missing real retrieved evidence for ranked candidates.

On failure, the CLI prints a clear error and exits with a non-zero status. It
does not write a normal `report.md` that looks successful.

## Limitations

- Public databases may be incomplete, stale, unavailable, or rate-limited.
- Source records may use inconsistent identifiers and terminology.
- Scores are heuristic and not experimentally validated.
- No wet-lab validation is performed by this software.
- No clinical recommendation, diagnosis, prescription, dosage, or treatment
  guidance is provided.
- Approved status does not imply safety or relevance for the queried disease.
- Absence of evidence is not evidence of absence.
- Literature evidence can be absent for a candidate; absence is labeled rather
  than filled with inferred claims.
- Mention-only literature is not proof of disease relevance, target engagement,
  efficacy, or safety.
- Clinical literature evidence is reported separately from preclinical and
  review evidence.
- Generated molecule hypotheses are in-silico only and have no attached
  invented evidence.
- Generated molecule hypotheses are not known actives and are ranked separately
  from existing molecules by default.
- V0.4 implements heuristic developability triage and rule-based ADMET risk
  flags, not validated ADMET prediction, default docking, retrosynthesis,
  synthesis planning, or wet-lab prediction.
- V0.5 review workspaces are local SQLite artifacts and static files, not a
  multi-user production system.
- Review decisions and expert feedback are not biomedical evidence.
- Validation handoff packets are high-level planning artifacts and do not
  include lab protocols.
- No synthesis instructions are provided.

## Roadmap

- V0.1: stronger live biomedical adapters and source normalization.
- V0.2: literature evidence retrieval and citation extraction.
- V0.3: target-conditioned novel molecule generation.
- V0.4: developability, ADMET, toxicity, synthesizability, and optional
  structure-aware filters.
- V0.5: expert review workflow and human-in-the-loop triage.
- V0.6: experimental feedback loop and active learning from assay results.
- V0.7: Codex CLI LLM backbone, project workspace, multi-run management, and
  local API server.
- V0.8: production deployment, authentication, roles, team collaboration, and
  hosted dashboard.
- V0.9: external integrations for ELN/LIMS, compound registry, assay providers,
  and data warehouse.
- V1.0: validated internal research platform MVP.

## Development

CI runs the same default checks on pull requests and pushes to `main`:

```bash
uv sync --all-groups --frozen
uv run ruff check .
uv run pyright
uv run pytest
```

Run normal unit tests:

```bash
uv run pytest
```

Normal unit tests use mocked public-source responses and do not require network
access. Live public API smoke tests live under `tests_live/` and are excluded
from the default pytest test path.

Run live public API smoke tests explicitly:

```bash
uv run pytest -m live tests_live/
```

Live tests are intentionally not deterministic. They depend on current Open
Targets, ChEMBL, PubChem, PubMed, and OpenAlex availability, rate limits,
schemas, and records. They assert structural properties only, not exact
biomedical targets, molecules, PMIDs, citation counts, or scores. The default
GitHub Actions CI does not run live network tests; the workflow includes a
manual `workflow_dispatch` live smoke job for maintainers.

Run lint:

```bash
uv run ruff check .
```

Run type checking:

```bash
uv run pyright
```
