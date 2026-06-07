# Architecture Decision Record: Release V1.0 Paid Pilot Architecture

Status: proposed for Release V0.x productization

Date: 2026-06-06

## Context

Molecule Ranker Dev V3.0 is the internal research engine. Release V1.0 is the paid
pilot web application that exposes a narrower, researcher-facing product surface:
project setup, disease or project-goal driven discovery runs, run status, result
bundles, ranked candidates, evidence/provenance, bounded generated hypotheses,
notes, favorites, exports, usage visibility, account management, and subscription
placeholder flows.

Release V1.0 must preserve the scientific guardrails from Dev V3.0. It must not
be positioned as a clinical decision tool, cure finder, lab protocol generator,
synthesis planner, regulated medical product, or autonomous drug-discovery claim
engine.

## Decision

Use a low-cost, mainstream hosted web architecture for V1.0, while keeping the
existing FastAPI engine and Codex runtime integration as the backend authority.

Recommended V1.0 stack:

| Area | Decision |
| --- | --- |
| Frontend | Next.js |
| Backend/API | Existing FastAPI server |
| Database/Auth | Supabase Auth + Postgres, or existing FastAPI auth if faster and safer for V0.x |
| Payments | Stripe Checkout + Stripe Billing |
| Artifact storage | Supabase Storage or S3-compatible storage |
| Background jobs | Existing job queue first; upgrade later |
| Deployment | Vercel for frontend; Render, Fly, or Railway for backend |
| Monitoring | Platform logs first; basic Sentry or OpenTelemetry later |
| Email | Resend or Postmark later; transactional placeholder for V0.x |
| Codex | Continue local/runtime Codex integration as configured |

## Rationale

The V1.0 goal is a paid pilot, not a platform rewrite. The chosen stack minimizes
new infrastructure while providing enough product scaffolding for authenticated
users, organizations, usage tracking, billing, and hosted result review.

Next.js gives a fast path to a hosted researcher-facing UI on Vercel. The existing
FastAPI server remains the API and workflow boundary, which avoids duplicating
scientific workflow logic. Supabase can cover authentication, Postgres, and
artifact storage for early pilots, while Stripe covers checkout and billing
without building a custom payments system.

Free and low-cost tools are acceptable during V0.x because the productization
track still needs rapid iteration, staging environments, and preview deployments.
They reduce operational drag while the team validates the pilot scope, usage
limits, result-bundle UX, disclaimer placement, and support workflows.

## Free-Tier Limits

Free tiers are acceptable for preview and staging only. They are not a serious
paid-pilot foundation.

In particular, free Render services and free Postgres are okay for preview or
staging, but not for a paid pilot because free web services spin down and free
Postgres expires after 30 days. A paid pilot needs persistent services, explicit
backup/restore expectations, stable response times, and supportable data
retention.

Other free-tier risks:

- Cold starts can make discovery status pages and API calls feel broken.
- Shared or limited databases can hit storage, connection, or retention limits.
- Logs and metrics may be too shallow for customer support and incident response.
- Artifact storage limits can silently block result bundle exports.
- Email placeholders cannot support reliable invitation, password reset, billing,
  or operational notices.
- Hobby deployment plans may lack required access control, team auditability,
  support response, or uptime expectations.

## Upgrade Triggers

Before accepting paid pilot customers, upgrade the following:

- Backend hosting: use a paid service tier with no spin-down and enough CPU/memory
  for expected discovery and result-bundle workloads.
- Database: use a paid Postgres tier with backups, retention, connection limits,
  and a documented restore path.
- Storage: use durable artifact storage with private buckets, signed URLs, object
  lifecycle policies, and backup expectations.
- Auth: require reliable invitation, password reset, session management, and
  account recovery.
- Billing: use Stripe Checkout and Stripe Billing only after pilot plans, usage
  limits, cancellation behavior, and tax/legal ownership are defined.
- Monitoring: add error tracking and basic tracing for API failures, job failures,
  export failures, auth failures, and billing webhook failures.
- Email: replace placeholders with a transactional provider before any real
  customer lifecycle emails are required.
- Jobs: keep the existing queue for V1.0 if it is reliable enough, but move to a
  managed queue or worker platform if run volume, retries, or concurrency exceed
  pilot bounds.

## Data Boundaries

The release app must never expose:

- Secrets, API keys, access tokens, service credentials, or webhook signing
  secrets.
- Raw Codex transcripts unless explicitly redacted and approved for support.
- Internal tool marketplace configuration.
- MCP server administration details.
- Policy engine internals, governance implementation details, or kill switches.
- Red-team suite outputs except summarized, non-sensitive safety status.
- External integration credentials or write scopes.
- Other tenants' projects, runs, artifacts, users, usage, billing data, or audit
  events.
- Fabricated evidence, fabricated citations, fabricated assay results, fabricated
  molecules, or fabricated approvals.
- Patient data, clinical guidance, dosing guidance, lab protocols, synthesis
  instructions, or wet-lab procedural content.

Result bundles may expose only user-authorized project data, ranked candidates,
evidence/provenance summaries, bounded generated hypotheses, guardrail notices,
and export metadata appropriate for researcher review.

## Auth, Billing, and Usage Limits

Authentication establishes the user identity and organization membership. Each API
request should resolve:

1. User identity.
2. Organization or workspace.
3. Role and permissions.
4. Plan or subscription status.
5. Usage limits and current usage.
6. Feature flags for the requested action.

Billing should map Stripe customer and subscription records to an internal
organization plan. The internal plan remains the source used by the application to
enforce limits; Stripe is the payment processor, not the authorization engine.

Usage limits should gate:

- Number of projects.
- Discovery runs per month.
- Codex tasks per month.
- Generated hypotheses per run.
- Result bundle exports per month.
- Storage usage.

Feature flags and usage limits must be checked before starting expensive or risky
workflows. Billing status should not weaken scientific guardrails. A paid plan can
increase limits, but it cannot enable clinical claims, lab protocols, synthesis
instructions, external writes without approval, or unbounded Codex autonomy.

## V1.0 Request Flow

1. User signs in through Supabase Auth or the existing FastAPI auth path.
2. Frontend calls the FastAPI API with the authenticated session.
3. API resolves tenant, role, feature flags, plan, and usage limits.
4. API creates or reads projects, runs, and artifacts in Postgres.
5. Background job queue executes approved discovery workflows.
6. Artifacts are written to private storage and indexed in Postgres.
7. Frontend displays run status, result bundles, ranked candidates, evidence, and
   bounded generated hypotheses.
8. Exports require disclaimers and are limited by plan usage.
9. Billing pages use Stripe Checkout/Billing once enabled; V0.x uses placeholders.

## Non-Goals

V1.0 does not include:

- A production deployment during V0.0.
- Payments implementation during V0.0.
- Enterprise sales tooling.
- Enterprise SSO or multi-org enterprise admin.
- Full governance dashboard exposure to pilot users.
- Raw tool marketplace exposure.
- External write integrations enabled by default.
- Write-approved-live mode for normal pilot users.
- Antibody generation controls for pilot users.
- Clinical, medical, dosing, lab protocol, synthesis, or patient guidance claims.

## Consequences

This architecture keeps productization focused and affordable, while preserving
the Dev V3.0 engine and guardrails. The tradeoff is that free-tier infrastructure
cannot be treated as production-ready. The team must explicitly upgrade hosting,
database, storage, monitoring, email, and billing operations before onboarding
serious paid pilot customers.

The architecture also keeps risk concentrated at the FastAPI boundary: auth,
tenant isolation, feature flags, usage limits, workflow execution, and export
permissions must be enforced server-side, not only in the frontend.
