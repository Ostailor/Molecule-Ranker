# V0.2 RLS Policies

Release V0.2 uses Supabase Row Level Security as the database backstop for
tenant isolation. Application route guards and product API checks are still
required, but database policies ensure authenticated users cannot read or write
rows outside organizations where they have an active membership.

## Helpers

The migration defines `is_org_member(org_id uuid)` as the main tenant-membership
helper. It also defines role and admin helpers for owner/admin checks and profile
visibility.

The policy model is intentionally organization-scoped:

- A user is inside a tenant only when `product_memberships.status = 'active'`.
- Owner/admin checks require active membership plus role `owner` or `admin`.
- Service role keys can bypass RLS and must never be used in browser code,
  browser-exposed environment variables, logs, Codex prompts, or support bundles.

## Table Policies

`product_profiles`:

- Users can select and update their own profile.
- Owners/admins can view member profiles in their organizations for V0.2 admin
  surfaces.
- Column grants limit profile updates to display name, avatar, onboarding state,
  and research-use acknowledgement.

`product_organizations`:

- Members can select their organizations.
- The owner can bootstrap a new organization.
- Only active owners/admins can update basic metadata.
- Public grants do not allow plan or owner mutation through normal authenticated
  client paths.

`product_memberships`:

- Active members can view memberships only in their organizations.
- Only owners/admins can add or update memberships.
- A trigger prevents users from escalating their own role.
- Normal users cannot see unrelated organization memberships.

`product_projects`:

- Active members can select projects in their organization.
- Owners, admins, and researchers can create projects.
- Project creators can update their own projects; owners/admins can update any
  project in their organization.
- Viewers cannot create or update projects.
- A trigger prevents project tenant identity from being moved across
  organizations.

`product_usage_events`:

- Users can insert their own usage events for active organizations where they are
  members.
- Users can read their own usage events.
- Owners/admins can read all usage events for their organization.
- No cross-organization usage access is allowed.

`product_feedback`:

- Users can insert feedback for active organizations where they are members.
- Users can view their own feedback.
- Owners/admins can view organization feedback and update feedback status.
- Normal users cannot view unrelated feedback or update submitted feedback.

## Non-Goals

V0.2 RLS does not add billing, Stripe, patient-data, PHI, HIPAA, clinical, or
enterprise SSO tables. It also does not authorize live engine job execution.

## Tenant Isolation Test Intent

Default CI uses offline mocked tests rather than a live Supabase instance. The
web test suite includes `apps/web/tests/tenant-isolation.test.mjs`, which covers
the V0.2 tenant boundaries without network access:

1. User A in Org A cannot list Org B projects.
2. User A in Org A cannot fetch an Org B project detail.
3. Viewer cannot create a project.
4. Researcher can create a project in their own organization.
5. Researcher cannot access admin summary.
6. Admin can access admin summary for their own organization.
7. Feedback rows are scoped to the active organization.
8. Usage event rows are scoped to the active organization.
9. Unauthenticated users cannot access product API behavior.
10. Web source does not reference `SUPABASE_SERVICE_ROLE_KEY`, `service_role`, or
    `serviceRole`.

These tests intentionally do not prove Supabase itself is running the policies.
They lock the product API boundary and the RLS policy intent in CI. The SQL
migration remains the source of truth for database enforcement.

## Optional Local Supabase Policy Checks

Live RLS tests are optional for V0.2 and must not be required in default CI. To
run local policy checks manually:

1. Install and start the Supabase CLI locally.
2. Run migrations from the repository root:

   ```bash
   supabase start
   supabase db reset
   ```

3. Create two auth users, two organizations, and memberships in a local-only
   seed script or SQL scratch file:

   - `user_a` in `org_a` as `researcher`.
   - `user_b` in `org_b` as `researcher`.
   - Optional `admin_a` in `org_a` as `admin`.

4. Use authenticated Supabase clients or SQL sessions with equivalent JWT claims
   to verify:

   - `user_a` can select `product_projects` rows for `org_a`.
   - `user_a` receives no rows for `org_b` projects.
   - `user_a` cannot insert projects as `viewer` but can as `researcher`.
   - `user_a` cannot select admin-only aggregate data.
   - `admin_a` can read only `org_a` feedback and usage rows.

Do not use the service role key for these checks except to seed local fixture
data. Service role keys bypass RLS, so they are invalid for proving tenant
isolation and must never be used in browser code, logs, Codex prompts, support
bundles, or browser-exposed environment variables.
