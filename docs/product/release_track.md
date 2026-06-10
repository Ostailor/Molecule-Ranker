# Pilot Release Track

Release V1.0 is a paid research-planning app for hypothesis generation,
source-backed ranking, result review, and pilot workflow management.

Release V1.0 is not a clinical product. It is not medical advice, not clinical
decision support, not a regulated medical product, not a cure finder, not a lab
protocol generator, and not a synthesis planner.

## Main Goal

Turn Dev V3.0 into a simple hosted pilot app while preserving the internal engine
and scientific guardrails. The release track should expose only the useful,
pilot-safe workflow:

Project -> New discovery run -> Result bundle -> Inspect candidates -> Export/save.

## Release V0.0: Productization Scaffold And Scope Freeze

Goals:

- Define pilot product scope.
- Separate Dev V3.0 internal engine features from Release V1.0 user-facing
  features.
- Document architecture, API boundary, UX, disclaimers, usage limits, and release
  roadmap.
- Preserve all scientific guardrails.

Success criteria:

- Product module and docs exist.
- Pilot-visible and hidden/admin-only features are explicit.
- Release defaults disable external writes, antibody generation, and full Codex
  autonomy.
- Legal and research-use disclaimer skeletons exist.

Non-goals:

- No hosted production deployment.
- No payment implementation.
- No clinical, medical, dosing, lab protocol, synthesis, or patient guidance.

## Release V0.1: Hosted App Shell And Dashboard

Goals:

- Create the Next.js app shell.
- Add landing, login placeholder, dashboard layout, and navigation.
- Render product-safe project/run mock states.

Success criteria:

- App shell can run locally and in preview.
- Dashboard reflects the Release V1.0 page model.
- Research-use disclaimers are visible in the shell.

Non-goals:

- No full auth rollout.
- No production data.
- No direct internal engine exposure.

## Release V0.2: Auth, Users, Organizations, Permissions

Status: implemented as the Auth, Users, Organizations, Permissions release.

Reference: `docs/product/v0_2_auth_users_orgs.md`

Goals:

- Add user identity, organization membership, and role checks.
- Decide Supabase Auth + Postgres or existing FastAPI auth for the pilot path.
- Enforce tenant isolation at the API boundary.

Success criteria:

- Users can sign in to a pilot organization.
- Product API resolves user, organization, role, and plan.
- Normal users cannot access admin-only surfaces.

Non-goals:

- No enterprise SSO.
- No multi-org enterprise admin.
- No unmanaged cross-tenant access.

## Release V0.3: Discovery Workflow From Web App To Engine

Status: implemented as the Bounded Discovery Workflow release.

Reference: `docs/product/v0_3_discovery_workflow.md`

Goals:

- Start a bounded discovery run from the web app.
- Connect the product API to a product-safe engine wrapper.
- Expose safe run creation and run status.

Success criteria:

- User can create a project and start a discovery run.
- Run progress is visible without raw AgentGraph, MCP, repair, governance, or
  Codex transcript internals.
- Usage limits are checked before run creation.
- Product-safe result artifacts are stored behind tenant-scoped APIs.

Non-goals:

- No external writes.
- No write-approved-live mode for normal pilot users.
- No full autonomous Codex mode.
- No production-grade worker infrastructure.
- No advanced result bundle viewer.

## Release V0.4: Result Bundle Viewer And Candidate/Evidence UI

Goals:

- Build result bundle overview.
- Build ranked candidate table and candidate detail.
- Build evidence/provenance viewer.
- Add generated hypotheses viewer if enabled and bounded.

Success criteria:

- Users can inspect result bundles, candidates, evidence, and limitations.
- Generated hypotheses are labeled as computational hypotheses.
- No claims of cure, safety, efficacy, activity, binding, manufacturability, or
  developability appear in product copy.

Non-goals:

- No raw scoring internals.
- No raw engine artifacts for normal users.
- No clinical validation claims.

## Release V0.5: Usage Limits And Stripe Billing

Goals:

- Enforce plan usage limits for projects, runs, generated hypotheses, exports,
  Codex tasks, and storage.
- Add Stripe Checkout and Stripe Billing.
- Add subscription/customer portal flow.

Success criteria:

- Pilot plan limits are enforced server-side.
- Billing status maps to internal organization plan.
- Stripe webhooks are verified.
- Billing does not weaken scientific guardrails.

Non-goals:

- No custom billing system.
- No complex enterprise sales system.
- No paid-plan access to unsafe scientific outputs.

## Release V0.6: Deployment, Background Jobs, Artifact Storage, Monitoring

Goals:

- Deploy frontend and backend on paid, non-sleeping service tiers.
- Configure background jobs, artifact storage, backups, and basic monitoring.
- Establish operational runbooks.

Success criteria:

- Frontend and backend are deployed in a pilot-ready environment.
- Artifacts are stored privately and are retrievable through product APIs.
- Logs and basic error reporting support pilot operations.

Non-goals:

- No serious paid pilot on free Render services or free Postgres.
- No unbounded job concurrency.
- No public production launch.

## Release V0.7: Onboarding, Feedback, Admin Pilot Console

Goals:

- Add onboarding checklist.
- Add feedback/contact page.
- Add admin pilot dashboard for users, organizations, runs, usage, feature flags,
  and support status.

Success criteria:

- Pilot users can complete onboarding and submit feedback.
- Admins can monitor pilot health without exposing deep internals to normal users.
- Feedback is separated from evidence, assay data, and scientific validation.

Non-goals:

- No full governance dashboard for pilot users.
- No red-team suite exposure.
- No deep policy settings in the product UI.

## Release V0.8: Security, Privacy, Legal Hardening

Goals:

- Harden auth, tenant isolation, logging, secrets handling, data retention, legal
  docs, disclaimers, and acceptable-use controls.
- Review API responses for sensitive data leakage.

Success criteria:

- Product API never exposes raw credentials, cache internals, raw traces, MCP
  internals, or full Codex transcripts.
- Legal docs are reviewed and ready for pilot use.
- Privacy and retention expectations are documented.

Non-goals:

- No regulated medical compliance claims.
- No patient-data workflow.
- No clinical decision support workflow.

## Release V0.9: Private Beta QA And Pilot Readiness

Goals:

- Run private beta QA with controlled users.
- Test end-to-end pilot workflows.
- Validate performance, support, recovery, security, billing, and export flows.

Success criteria:

- Core loop works reliably: project, run, result bundle, inspect, save/export.
- Known launch blockers are resolved or explicitly accepted.
- Pilot readiness checklist passes.

Non-goals:

- No public launch.
- No enterprise packaging.
- No expansion beyond pilot-safe scope.

## Release V1.0: Paid Pilot Release

Goals:

- Launch a paid research-planning pilot app.
- Support authenticated pilot organizations.
- Provide source-backed ranking, result bundle review, evidence/provenance review,
  bounded generated hypotheses, saved candidates/notes, exports, usage limits,
  billing, and admin pilot operations.

Success criteria:

- Paid pilot customers can complete the main workflow.
- Billing and usage limits work.
- Data remains tenant-isolated.
- Scientific guardrails remain intact.
- Support and monitoring can handle pilot incidents.

Non-goals:

- Release V1.0 is not a clinical product.
- Release V1.0 is not clinical decision support.
- Release V1.0 is not a regulated medical product.
- Release V1.0 does not provide medical advice, patient treatment guidance,
  dosing, lab protocols, or synthesis instructions.
- Release V1.0 does not claim cure, safety, efficacy, activity, binding,
  manufacturability, or developability.

## Risk List

- Scope creep exposes internal engine complexity to pilot users.
- Users misinterpret rankings or generated hypotheses as validated scientific or
  clinical claims.
- Free-tier infrastructure causes downtime, cold starts, data expiry, or weak
  operational visibility.
- Tenant isolation bugs expose project, artifact, usage, account, or billing data.
- Billing status accidentally bypasses usage limits or scientific guardrails.
- Raw Codex transcripts, tool logs, credentials, cache internals, or MCP details
  leak into user-facing responses.
- Job queue failures create stuck runs or partial result bundles.
- Artifact storage permissions expose private result bundles.
- Legal/privacy language remains skeleton-level too long.
- Support workflows treat user feedback as evidence or validation.

## Upgrade Path After Revenue

After revenue begins, prioritize reliability, security, and supportability before
expanding scope:

- Move all paid pilot services to paid, non-sleeping infrastructure tiers.
- Upgrade Postgres with backups, restore testing, retention policies, and
  connection limits.
- Harden artifact storage with private buckets, signed URLs, lifecycle policies,
  and backup expectations.
- Add Sentry or OpenTelemetry tracing for frontend, API, jobs, exports, and
  billing webhooks.
- Replace email placeholders with Resend, Postmark, or another transactional
  provider.
- Add managed background jobs if the existing queue cannot meet pilot reliability
  needs.
- Add stricter audit logs for auth, billing, exports, admin actions, and support
  access.
- Formalize security review, privacy review, legal terms, and customer support
  processes.
- Only then consider expanded workflows, integrations, enterprise SSO, advanced
  admin, or higher-scale infrastructure.
