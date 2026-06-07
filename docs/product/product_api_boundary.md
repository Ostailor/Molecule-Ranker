# Product API Boundary

The Release V1.0 web app uses a narrow product API over the Dev V3.0 engine.
The product API is responsible for authentication, tenant isolation, usage-limit
enforcement, feature flags, safe response shaping, and disclaimer-aware exports.

The web app must not call internal engine surfaces directly.

## Boundary Principles

- Product API hides internal engine complexity.
- Product API enforces authentication on every tenant-scoped request.
- Product API enforces organization/workspace authorization.
- Product API enforces usage limits before creating projects, runs, Codex tasks,
  generated hypotheses, exports, or storage-heavy artifacts.
- Product API enforces feature flags before exposing optional or risky surfaces.
- Product API returns product-shaped resources, not raw engine objects.
- Product API must not expose raw credentials, cache internals, internal traces,
  raw tool logs, MCP internals, policy engine internals, repair internals, or full
  Codex transcripts.
- Advanced artifacts are admin-only and must be redacted before support use.

## Pilot-Facing Endpoints

### `POST /api/product/projects`

Purpose: Create a pilot project from a research goal, disease area, or project
context.

Uses from engine: Project/workspace creation, safe disease or goal normalization,
and guardrail validation.

Response shape: Project ID, name, goal summary, created timestamp, owner/org
context, and next action.

Rules: Enforce auth, project limits, tenant isolation, safe-input validation, and
research-use acknowledgement.

### `GET /api/product/projects`

Purpose: List the user's visible projects.

Uses from engine: Workspace/project index.

Response shape: Project summaries, recent run status, saved candidate count, and
last updated timestamp.

Rules: Return only projects visible to the authenticated user.

### `GET /api/product/projects/{id}`

Purpose: Load one project overview.

Uses from engine: Project metadata, run summaries, saved candidates, and notes.

Response shape: Project details, recent runs, usage context, and next available
actions.

Rules: Hide internal run graph, repair traces, raw prompts, and tool routing.

### `POST /api/product/runs`

Purpose: Start a bounded discovery run for a project.

Uses from engine: Release-safe discovery workflow, result-bundle generation, job
queue, feature flags, and guardrails.

Response shape: Run ID, project ID, status, created timestamp, selected workflow,
and status URL.

Rules: Enforce auth, project access, run limits, generated-hypothesis limits,
feature flags, safe workflow mode, and required disclaimers. External writes must
remain disabled by default.

### `GET /api/product/runs/{id}`

Purpose: Load product-safe run metadata.

Uses from engine: Run record, project link, status, timestamps, and output
artifact references.

Response shape: Run ID, status, project summary, high-level workflow stage, and
available result links.

Rules: Do not expose raw AgentGraph state, Codex transcripts, internal retries,
repair internals, or tool logs.

### `GET /api/product/runs/{id}/status`

Purpose: Poll run progress.

Uses from engine: Job queue state and coarse workflow status.

Response shape: Status, stage label, timestamps, safe error summary, and whether
results are available.

Rules: Return safe errors only. Internal stack traces, data-source credentials,
tool invocations, and raw logs are not product data.

### `GET /api/product/runs/{id}/result-bundle`

Purpose: Load the product-safe result bundle overview.

Uses from engine: Result bundle contract, ranked candidate summary, evidence
coverage, guardrail notices, and export metadata.

Response shape: Bundle summary, candidate counts, evidence summary, limitations,
disclaimers, and links to candidate/evidence views.

Rules: Result bundles are research artifacts only. Do not expose internal
certification mechanics, raw artifact registry data, or advanced artifacts unless
the user is an admin and the artifacts are redacted.

### `GET /api/product/runs/{id}/candidates`

Purpose: Load ranked candidates for a run.

Uses from engine: Candidate ranking output and evidence/provenance summaries.

Response shape: Candidate rows with ID, display name, rank, score summary,
evidence count, limitation flags, saved status, and note count.

Rules: Do not expose raw scoring internals, hidden model diagnostics, or
unreviewed internal hypotheses.

### `GET /api/product/candidates/{id}`

Purpose: Load one product-safe candidate detail view.

Uses from engine: Candidate detail, ranking context, evidence/provenance, saved
state, notes, and generated-hypothesis links if enabled.

Response shape: Candidate identifiers, rank, score summary, evidence references,
limitations, saved status, notes, and disclaimers.

Rules: No claims of cure, safety, efficacy, activity, binding, manufacturability,
or developability. Candidate detail is not medical advice or clinical decision
support.

### `POST /api/product/candidates/{id}/save`

Purpose: Save or unsave a candidate for the authenticated user.

Uses from engine: User workspace state and candidate reference.

Response shape: Candidate ID, saved state, timestamp, and user ID.

Rules: Saving is a user preference, not evidence, approval, validation, or
advancement.

### `POST /api/product/candidates/{id}/note`

Purpose: Add a user note to a candidate.

Uses from engine: Notes/collaboration storage and candidate reference.

Response shape: Note ID, candidate ID, body, author, timestamp, and visibility.

Rules: Notes are user annotations. They are not evidence, assay results, clinical
guidance, or scientific validation.

### `GET /api/product/usage`

Purpose: Show usage limits and current usage.

Uses from engine: Usage counters, plan limits, feature flags, and storage/export
counts.

Response shape: Plan, projects used, runs used, Codex tasks used, generated
hypotheses used, exports used, storage used, and reset period.

Rules: Usage counters must be tenant-scoped. Billing status must not weaken
scientific guardrails.

### `GET /api/product/account`

Purpose: Show account, organization, role, plan, and acknowledgement status.

Uses from engine/platform: Auth identity, organization membership, role mapping,
feature flags, and plan state.

Response shape: User profile, organization summary, role, plan, status,
acknowledgements, and enabled product features.

Rules: Do not expose auth provider internals, session secrets, tokens, role
calculation internals, or other users' private account data.

### `POST /api/product/feedback`

Purpose: Capture pilot feedback or support requests.

Uses from engine/platform: Feedback/support record storage and optional project
or run reference.

Response shape: Feedback ID, category, related object reference, status, and
created timestamp.

Rules: Feedback is not evidence, assay data, clinical guidance, or scientific
validation.

## Later Billing Endpoints

These endpoints are planned for the paid pilot path and should remain disabled in
Release V0.0.

### `POST /api/billing/create-checkout-session`

Purpose: Create a Stripe Checkout session for an organization plan.

Rules: Requires authenticated organization owner/admin. Server must validate plan
IDs and never trust client-provided prices.

### `POST /api/billing/customer-portal`

Purpose: Create a Stripe customer portal session for subscription management.

Rules: Requires authenticated organization owner/admin. Must map the internal
organization to the Stripe customer server-side.

### `POST /api/webhooks/stripe`

Purpose: Receive Stripe billing events and update internal plan/subscription
state.

Rules: Verify webhook signatures. Treat Stripe as payment state, not as the
authorization engine. Internal plan and feature flags remain the enforcement
surface.

## Admin-Only Advanced Artifacts

Advanced artifacts include internal traces, raw Codex transcripts, repair
artifacts, policy decisions, governance diagnostics, red-team outputs, tool logs,
MCP details, and external integration details.

Rules:

- Advanced artifacts are admin-only.
- Advanced artifacts must be redacted before support sharing.
- Advanced artifacts must never be included in normal result bundle responses.
- Product users should see safe summaries, limitations, and support-oriented error
  messages instead.

## Data That Must Never Cross The Product Boundary

- Raw credentials, API keys, service tokens, webhook secrets, and integration
  secrets.
- Cache internals and private cache keys.
- Internal traces, raw tool logs, raw MCP requests, and raw AgentGraph state.
- Full Codex transcripts.
- Policy engine internals and deep policy settings.
- Repair internals and red-team suite details.
- Other tenants' projects, users, runs, artifacts, usage, billing, or audit data.
- Clinical, dosing, patient treatment, lab protocol, or synthesis guidance.
