# Project Permissions

## Purpose

Project permissions define who can inspect, run, review, export, or delete a
molecule-ranker project.

## RBAC Matrix

| Permission | viewer | reviewer | runner | editor | project_owner | platform_admin |
| --- | --- | --- | --- | --- | --- | --- |
| `project:read` | yes | yes | yes | yes | yes | yes |
| `run:create` | no | no | yes | yes | yes | yes |
| `review:write` | no | yes | no | yes | yes | yes |
| `experiment:import` | no | no | no | yes | yes | yes |
| `integration:sync` | no | no | no | limited | limited | yes |
| `artifact:export` | no | no | no | limited | yes | yes |
| delete project | no | no | no | no | yes | yes |

## Permission Descriptions

Permissions control action types, not scientific authority. A user with
`review:write` can record review workflow decisions, but those decisions are not
biomedical evidence.

## Commands

```bash
molecule-ranker project comment --project-id project-example --actor-user-id user-example --body "Access reviewed." --json
molecule-ranker admin audit --json
```

## Expected Output

Permission-sensitive commands either complete with an audit event or fail with a
permission error. Audit review should show actor, object, timestamp, and summary.

## Failure Modes

- User can read a project but cannot export artifacts.
- Job creation fails because `run:create` is missing.
- Integration sync fails because only platform admins can manage connector-wide
  write settings.

## Project Export/Delete Guidance

Before project export, confirm the recipient and storage path. Before delete,
verify backups and retention policy. Use purge only after explicit project ID
confirmation.

## Incident Response

If project access is too broad, remove grants, revoke service tokens associated
with the project, review export history, and preserve audit logs.

## Credential Secret-Ref Guidance

Project permissions should never expose credential values. Integrations must
refer to credentials through secret-ref metadata.
