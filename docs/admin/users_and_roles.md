# Users and Roles

## Purpose

This admin guide explains user role management for molecule-ranker hosted mode.
The platform is for internal research use and is not a clinical decision system.

## RBAC Matrix

| Role | Project read | Run jobs | Review write | Import assay results | Manage integrations | Admin audit |
| --- | --- | --- | --- | --- | --- | --- |
| viewer | yes | no | no | no | no | no |
| reviewer | yes | no | yes | no | no | no |
| runner | yes | yes | no | no | no | no |
| editor | yes | yes | yes | yes | limited | no |
| project_owner | yes | yes | yes | yes | limited | project scope |
| platform_admin | yes | yes | yes | yes | yes | yes |

## Permission Descriptions

- `project:read`: view project metadata, artifacts, and dashboard summaries.
- `run:create`: start ranking, generation, developability, or dashboard jobs.
- `review:write`: add review decisions, comments, and follow-up requests.
- `experiment:import`: import assay results from files.
- `integration:sync`: run dry-run syncs and approved integration actions.
- `artifact:export`: export project packages.
- `codex:run`: request guarded Codex assistant tasks.

## Commands

```bash
molecule-ranker user create --email user@example.invalid --password "<generated-placeholder>" --json
molecule-ranker user list --json
molecule-ranker admin users --json
```

## Expected Output

User listings show user IDs, email, active status, and role metadata. Passwords,
service token hashes, and credential secret-ref values are never printed.

## Failure Modes

- User cannot access a project because project permission was not granted.
- User can see a dashboard but cannot create jobs due to missing `run:create`.
- Review workflow updates fail because the user lacks `review:write`.

## Project Export/Delete Guidance

Grant `artifact:export` only to users who need package export. Require project
owner or platform admin review before delete or purge actions.

## Audit Review And Incident Response

Review audit logs after user creation, role changes, project export, delete, and
service token lifecycle changes. If access looks wrong, revoke affected tokens,
pause workers if needed, and preserve audit output for incident response.
