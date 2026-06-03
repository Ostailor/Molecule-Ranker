# Admin Training

Audience: enterprise administrators managing V2.0 organizations, users,
permissions, policies, support bundles, and validation packages.

## Interpretation Boundaries

- Internal research use only.
- Not a clinical product.
- No medical advice, patient treatment guidance, dosing guidance, lab
  protocols, or synthesis instructions.
- Admin controls govern software access and process readiness; they do not
  validate molecule safety, activity, efficacy, binding, or synthesizability.

## Checklist

- Confirm version `2.0.0` and `/api/v2` availability.
- Create organization tenant and teams.
- Assign least-privilege platform and project roles.
- Create scoped service accounts and record token IDs, not token values.
- Configure OIDC group-to-role mapping.
- Review policy overrides and audit logs.
- Generate a redacted support bundle.
- Generate a V2 validation package.

## Exercise: Synthetic Enterprise Setup

Synthetic data:

- Organization: `demo-org`
- Team: `demo-discovery`
- Admin user: `admin.demo@example.invalid`
- Scientist user: `scientist.demo@example.invalid`
- Project: `demo-project-alpha`

Steps:

1. Create the org and team.
2. Add both users.
3. Grant the scientist `editor` access to `demo-project-alpha`.
4. Create a service account with `project:read` only.
5. Open the RBAC matrix and policy page.
6. Generate a support bundle and validation package.

Expected outcomes:

- Scientist can view and edit permitted project data only.
- Service account cannot perform admin actions.
- Audit log records user, role, and service-account changes.
- Support bundle contains request IDs and diagnostics, not secrets.
- Validation package states software/process validation boundaries.

## Common Mistakes

- Granting platform admin when project-level permission is enough.
- Saving service token values in tickets or docs.
- Treating validation package output as clinical or regulatory approval.
- Approving project policy overrides without audit rationale.
