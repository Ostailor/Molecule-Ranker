# V0.2 Auth Architecture Decision

Status: accepted for Release V0.2

Date: 2026-06-07

## Context

Release V0.2 adds the identity and tenancy layer for the molecule-ranker
productization track. V0.1 established the Next.js hosted app shell with mock
data. V0.2 must introduce real authentication, user accounts, organizations,
memberships, roles, protected routes, and tenant-safe product API boundaries
without adding billing, live engine execution, enterprise administration, or
regulated clinical positioning.

The pilot app remains a research-use product surface. It must preserve existing
research-use disclaimers and avoid clinical, medical, patient-data, HIPAA, or
production compliance claims.

## Decision

Use Supabase Auth, Supabase Postgres, and Supabase Row Level Security for the
Release V0.2 pilot path.

The V0.2 implementation should use the Supabase Next.js SSR helpers for session
handling in the App Router, persist product data in Supabase Postgres, and enable
RLS on exposed product tables such as user profiles, organizations,
memberships, projects, and discovery-run metadata.

## Rationale

Supabase is the fastest practical path for the V0.2 pilot because it provides
hosted authentication and Postgres without building a custom auth service.

It works well with Next.js App Router and supports common flows needed for the
pilot, including email/password signup, login, logout, auth callbacks, and
password reset. Supabase Postgres also gives the product a real relational data
store for users, organizations, memberships, roles, projects, and later run
metadata.

Row Level Security is a good fit for tenant isolation. V0.2 should still enforce
authorization in product API code, but RLS gives a database-level backstop so
normal users cannot read or mutate rows outside organizations where they have an
active membership.

This choice is good enough for the pilot while remaining replaceable later. The
product API should resolve an internal auth context from Supabase sessions, then
apply organization and role checks behind application-owned boundaries. Future
enterprise auth can replace or augment Supabase without exposing raw auth
provider details throughout the app.

## Environment Variables

| Variable | Scope | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Browser and server | Public Supabase project URL used by the web app and server-side helpers. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Browser and server | Public publishable key for browser-safe Supabase client creation. |
| `SUPABASE_SERVICE_ROLE_KEY` | Server only | Privileged key for tightly controlled server/admin operations. Never expose to browser code. |
| `SUPABASE_JWT_SECRET` | Server only, optional | Used only if backend JWT verification is implemented outside Supabase helpers. |
| `PRODUCT_APP_URL` | Server and deployment config | Canonical product app URL for redirects, callbacks, and environment-aware links. |

Do not commit real values. Local and deployment templates should include
placeholder values only.

## Security Requirements

Service role keys must never be used in client-side code, logs, Codex prompts,
support bundles, or browser-exposed environment variables.

The service role key must not be prefixed with `NEXT_PUBLIC_`, passed into React
components, returned from API routes, included in telemetry, printed during
debugging, copied into support artifacts, or pasted into prompts. Any code that
uses the service role key must run only on the server and should be narrowly
scoped to operations that cannot be performed with the authenticated user
session.

V0.2 must enable RLS on exposed tables and enforce organization membership before
showing project, run, organization, membership, or admin data. Normal users must
not access admin-only surfaces, even if they can guess the URL or call the API
directly.

The product API should resolve every protected request into:

1. Authenticated user identity.
2. Active organization.
3. Membership status.
4. Role and permissions.
5. Tenant-scoped data access policy.

Frontend route guards improve the user experience, but server-side route handlers
and database policies remain the authority.

## Non-Goals

V0.2 does not include:

- Enterprise SSO.
- Stripe.
- Paid subscriptions or billing enforcement.
- Production compliance claims.
- PHI support.
- HIPAA positioning.
- Clinical product positioning.
- Patient-data workflows.
- Medical advice, clinical decision support, dosing guidance, lab protocols, or
  synthesis planning.

## Future Path

V0.3 connects the discovery workflow from the web app to the bounded product API
and existing engine boundary.

V0.5 adds Stripe for usage limits, checkout, subscription state, and billing
portal flows after the auth and tenant model is stable.

A later enterprise version can add SSO, SCIM, private deployment options,
advanced admin controls, and customer-specific identity provider requirements
without weakening the V0.2 tenant isolation model.
