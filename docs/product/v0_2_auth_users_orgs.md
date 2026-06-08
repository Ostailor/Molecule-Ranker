# V0.2 Auth, Users, Organizations, Permissions

Release V0.2 adds the product identity and tenancy layer for the hosted
Molecule Ranker pilot app. It uses Supabase Auth, Supabase Postgres, and Row
Level Security with Next.js App Router and Supabase SSR helpers.

The app remains research-use only. V0.2 does not add billing, live discovery
execution, enterprise SSO, patient data support, PHI workflows, HIPAA
positioning, clinical claims, lab protocols, synthesis planning, or dosing
guidance.

## V0.2 Scope

Included:

- Real Supabase authentication for login, signup, logout, callback, and password
  reset flow.
- Product user profiles.
- Organizations.
- Memberships.
- Roles: owner, admin, researcher, viewer.
- Protected app routes.
- Product API auth context.
- Admin-only route and API protection.
- Tenant-scoped projects, feedback, and usage events.
- RLS-backed database isolation.
- Offline tenant isolation tests.

Not included:

- Stripe or paid subscriptions.
- Live engine job execution.
- External writes.
- Enterprise SSO, SCIM, or private deployment.
- Multi-org enterprise admin.
- Raw internal engine surfaces.
- Raw Codex transcripts.
- Patient or PHI tables.
- Clinical, medical, lab, synthesis, or dosing product positioning.

## Supabase Setup Steps

1. Create a Supabase project for the pilot environment.
2. Enable email/password auth in Supabase Auth.
3. Configure the product app callback URL:

   - Local: `http://localhost:3000/auth/callback`
   - Preview/hosted: `${PRODUCT_APP_URL}/auth/callback`

4. Copy `apps/web/.env.example` to `apps/web/.env.local`.
5. Fill only local or deployment-specific values. Do not commit real secrets.
6. Apply the V0.2 migration from `supabase/migrations`.
7. Confirm RLS is enabled and forced on all product tables.

## Environment Variables

| Variable | Scope | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Browser and server | Supabase project URL for browser and SSR clients. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Browser and server | Browser-safe publishable key. Used by browser and server SSR clients. |
| `PRODUCT_APP_URL` | Server/deployment | Canonical app URL for redirects and auth callbacks. |
| `SUPABASE_SERVICE_ROLE_KEY` | Server only, later | Privileged key. Never import into `apps/web/src`, browser code, logs, prompts, or support bundles. |
| `SUPABASE_JWT_SECRET` | Server only, optional | Only needed if backend JWT verification is implemented outside Supabase helpers. |

`apps/web/.env.local` is gitignored. Browser-exposed variables must use only
publishable values. The service role key must never be prefixed with
`NEXT_PUBLIC_`.

## Schema And Migration Setup

Migration:

- `supabase/migrations/0001_product_auth_schema.sql`

Tables:

- `product_profiles`
- `product_organizations`
- `product_memberships`
- `product_projects`
- `product_usage_events`
- `product_feedback`

The migration adds:

- Primary and foreign keys to Supabase `auth.users`.
- Role, status, and plan check constraints.
- Useful tenant and timestamp indexes.
- `updated_at` trigger support.
- Membership and project safety triggers.
- RLS enabled and forced on every product table.

The schema intentionally does not create billing, patient, PHI, HIPAA,
clinical, secret, API-key, or service-token tables.

## RLS Policy Summary

RLS is the database backstop for tenant isolation. App route guards and product
API checks are still required.

- `product_profiles`: users can read/update their own profile; owner/admin can
  view profiles for members in their organizations.
- `product_organizations`: members can read their organizations; owner/admin can
  update basic metadata.
- `product_memberships`: active members can read memberships in their org;
  owner/admin can add or update memberships; self role escalation is blocked.
- `product_projects`: active members can read projects in their org;
  owner/admin/researcher can create; creator/owner/admin can update.
- `product_usage_events`: users can insert their own org usage events; users can
  read their own events; owner/admin can read all org usage events.
- `product_feedback`: users can insert and read their own feedback; owner/admin
  can read organization feedback.

See `docs/product/v0_2_rls_policies.md` for detailed policy intent and optional
local Supabase test instructions.

## Role Model

Roles:

- `owner`: full pilot permissions, including admin access.
- `admin`: admin workspace access and user-management placeholders.
- `researcher`: can create/read/update projects and submit feedback.
- `viewer`: can read projects and submit feedback, but cannot create projects or
  access admin surfaces.

Memberships must be active to establish tenant access.

## Permission Model

Permissions:

- `project:create`
- `project:read`
- `project:update`
- `run:create` placeholder
- `run:read` placeholder
- `candidate:save` placeholder
- `export:create` placeholder
- `feedback:create`
- `admin:read`
- `admin:manage_users`

The shared helpers expose:

- `roleHasPermission`
- `requireRole`
- `requirePermission`
- `canAccessAdmin`
- `canCreateProject`
- `canViewProject`

Run, candidate, and export permissions remain placeholders until later releases.

## Protected Routes

Public routes:

- `/`
- `/login`
- `/signup`
- `/auth/callback`
- `/forgot-password`
- `/reset-password`
- Legal placeholder pages

Protected routes:

- `/onboarding`
- `/dashboard`
- `/projects`
- `/usage`
- `/account`
- `/feedback`
- `/admin`

Middleware behavior:

- Unauthenticated protected-route access redirects to `/login`.
- Authenticated users without onboarding are redirected to `/onboarding`, except
  account/logout exemptions.
- Authenticated users visiting `/login` are redirected to `/dashboard`.
- `/admin` requires owner/admin and otherwise renders a safe 403 surface.

## Product API Auth Context

Product API utilities live under `apps/web/src/lib/product`.

`getProductAuthContext()` resolves:

- Supabase user.
- Product profile.
- Active organization.
- Active membership.
- Role.
- Plan.

Additional helpers:

- `requireProductUser()`
- `requireOrganizationMember()`
- `requireProductPermission(permission)`
- `requireAdminRole()`
- `getActiveOrganizationForUser()`

API responses use standardized errors:

- `UNAUTHENTICATED`
- `ONBOARDING_REQUIRED`
- `ORGANIZATION_REQUIRED`
- `FORBIDDEN`
- `PLAN_LIMIT_EXCEEDED`
- `NOT_FOUND`
- `VALIDATION_ERROR`

Product API routes must not expose raw Supabase errors, secrets, raw engine
internals, or raw Codex transcripts.

## Tenant Isolation

Tenant access is based on active organization membership. Product routes and API
handlers query with `organization_id = context.organization.id`; RLS policies
also prevent cross-organization access.

Offline tenant isolation tests live at:

- `apps/web/tests/tenant-isolation.test.mjs`

They cover:

- User A in Org A cannot list or fetch Org B projects.
- Viewer cannot create projects.
- Researcher can create projects in their own org.
- Researcher cannot access admin summary.
- Admin can access admin summary for their own org.
- Feedback and usage rows are org-scoped.
- Unauthenticated product API access is blocked.
- Service role keys are not referenced in web client source.

Default CI does not require a live Supabase instance.

## Admin-Only Surfaces

Admin-only surfaces include:

- `/admin`
- `/api/product/admin/summary`
- Admin navigation entries.

These require owner/admin roles. Admin pages show organization-scoped member,
project, feedback, usage, and feature-flag summaries. Invite user, manage roles,
and billing actions remain disabled placeholders.

Normal researchers and viewers must receive a safe 403 response or page.

## Still Mock Or Placeholder

V0.2 still has mock or placeholder surfaces for:

- Discovery run creation.
- Discovery run status.
- Result bundles.
- Candidate viewer data.
- Evidence viewer data.
- Generated hypotheses data.
- Export actions.
- Stripe billing.
- Invite user and role management actions.
- Account page data in some V0.1 surfaces.

Placeholders must stay product-safe, visibly bounded, and separated from real
tenant data.

## Moves To V0.3

V0.3 connects the discovery workflow:

- Start a bounded discovery run from the web app.
- Connect product API to the existing engine boundary.
- Add real run creation and status.
- Keep raw engine internals hidden.
- Check usage limits before run creation.

V0.3 still must not add external writes, clinical claims, patient data, lab
protocols, synthesis instructions, or dosing guidance.

## Moves To V0.5

V0.5 adds usage limits and Stripe billing:

- Stripe Checkout.
- Stripe Billing.
- Customer portal flow.
- Subscription state mapped to organization plan.
- Server-side plan and usage enforcement.
- Billing webhooks with signature verification.

Billing must not weaken tenant isolation or scientific guardrails.

## Security Checklist

- RLS is enabled and forced on all exposed product tables.
- Service role key is never used in browser code.
- Service role key is never logged, pasted into prompts, or included in support
  bundles.
- `.env.local is ignored` by git configuration.
- Admin routes and APIs require owner/admin role.
- Cross-org project, membership, feedback, and usage data is blocked.
- Patient/PHI warnings are visible in onboarding and project creation surfaces.
- Research-use disclaimers are visible in the app shell and relevant workflows.
- Product copy avoids clinical, lab, synthesis, and dosing claims.
- Product API errors are sanitized.
- Default CI uses offline tenant-isolation tests and does not require live
  Supabase.
