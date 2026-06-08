# Release V0.2 Notes

Release V0.2 is the Auth, Users, Organizations, Permissions release for the
Molecule Ranker productization track.

## What Changed

- Added Supabase Auth decision and setup documentation.
- Added Next.js Supabase browser, server, middleware, and auth helpers.
- Added email/password login, signup, logout, callback, and password reset
  surfaces.
- Added product profiles, organizations, memberships, roles, projects, usage
  events, and feedback schema.
- Enabled and forced Row Level Security on product tables.
- Added owner, admin, researcher, and viewer role checks.
- Added protected app-route behavior and admin-only surface protection.
- Added product API auth context and standardized product API errors.
- Added real tenant-scoped project creation and project detail loading.
- Added tenant-scoped usage events for project creation, feedback, and
  onboarding completion.
- Added real usage page data from authenticated organization context.
- Added admin summary surfaces scoped to the active organization.
- Added product feature flags with risky features disabled by default.
- Added offline tenant isolation tests that do not require live Supabase.

## What Remains Placeholder

- Discovery run creation and status.
- Result bundle execution data.
- Candidate, evidence, and generated hypothesis data sources.
- Export creation.
- Stripe billing and subscription state.
- Invite-user and manage-role admin actions.
- External integrations.
- Antibody generation.
- Biologics viewer.
- Live engine execution.

## How To Test

Default checks do not require network access or live Supabase:

```bash
cd apps/web
npm test
npm run lint
npm run typecheck
npm run build
```

Product and schema documentation checks:

```bash
python -m pytest \
  tests/test_product_module.py \
  tests/test_supabase_product_auth_schema.py \
  tests/test_product_v0_2_auth_users_orgs_docs.py \
  -q
```

Optional local Supabase policy checks are documented in
`docs/product/v0_2_rls_policies.md`. They are manual and must not be required in
default CI.

## Known Limitations

- No live discovery workflow is connected yet.
- Product API routes do not expose run creation, run status, result bundles,
  candidates, evidence, or generated hypotheses as real backend data.
- Usage limits are V0.2 product-action oriented and run-related actions remain
  placeholders.
- Billing is placeholder-only; Stripe is not implemented.
- Admin role management and invitations are disabled.
- Account page data still includes V0.1 placeholder surfaces.
- Default local tests use mocked/static checks rather than a live Supabase RLS
  harness.
- Supabase service role keys can bypass RLS and must never be used in browser
  code, logs, prompts, support bundles, or browser-exposed environment
  variables.

## Next Release: V0.3 Discovery Workflow Connection

V0.3 connects the discovery workflow from the web app to the product API and
existing engine boundary.

Planned V0.3 work:

- Start bounded discovery runs from authenticated projects.
- Persist real run records and status.
- Keep raw engine internals hidden.
- Keep result bundles, candidate data, evidence, generated hypotheses, and
  exports behind product-safe API boundaries.
- Check role, organization membership, feature flags, and usage limits before
  starting expensive or risky workflows.

V0.3 still does not add Stripe, external writes, patient data, PHI support,
clinical claims, lab protocols, synthesis instructions, or dosing guidance.
