# Admin Guide

Admins manage organizations, teams, users, roles, service accounts, project
permissions, integrations, jobs, workers, Codex workers, policies, audit logs,
support bundles, backup/restore, observability, and validation packages.

## Admin Process

1. Create or verify the organization tenant.
2. Create teams and assign users.
3. Assign least-privilege roles.
4. Grant project permissions only to required users, teams, or org roles.
5. Create service accounts with scoped tokens.
6. Configure policies and review project overrides.
7. Review audit logs after administrative changes.
8. Run backup/restore and release validation checks before promotion.

## Admin Console Expectations

- Admin permission is required.
- Actions are audited.
- Secrets are never shown.
- Service tokens are shown only once.
- RBAC matrix and policy explanations are visible.
- Support bundles exclude secrets, cache, and Codex transcripts by default.

## Boundaries

Admins manage software, access, and process controls. Admin controls do not
authorize medical advice, clinical use, dosing guidance, synthesis
instructions, or lab protocols.
