# Service Accounts

## Purpose

Service accounts support automation for molecule-ranker jobs, integrations,
dashboard builds, backups, and guarded Codex tasks.

## RBAC Matrix

| Service account type | Minimum permissions | Notes |
| --- | --- | --- |
| worker | `run:create`, `project:read` | add narrow job permissions only |
| integration dry-run | `integration:read`, `integration:sync` | keep dry-run by default |
| export automation | `artifact:export` | restrict project scope |
| Codex worker | `codex:run`, `project:read` | require guardrail monitoring |
| admin automation | platform admin | use only for operations |

## Service Token Lifecycle

1. Create a token for one automation purpose.
2. Store the token only in the approved secret manager.
3. Record owner, scope, created date, and rotation schedule.
4. Rotate on schedule or after role changes.
5. Revoke immediately when automation is retired or suspected compromised.

## Commands

```bash
molecule-ranker auth token create --user-id service-account-example --json
molecule-ranker auth token revoke token-example --actor-user-id admin-example --json
molecule-ranker admin audit --json
```

## Expected Output

Token creation shows the token once. Later listings show token IDs and metadata,
not token material. Audit review records create and revoke events.

## Failure Modes

- Token is scoped too broadly.
- Token owner is unknown.
- Token is not rotated.
- Automation fails after a role change because permissions were narrowed.

## Project Export/Delete Guidance

Service accounts should not delete projects unless explicitly dedicated to
retention automation. Export automation must write to approved internal storage.

## Credential Secret-Ref Guidance

Store service tokens as secret-ref entries such as `env:MOLECULE_RANKER_TOKEN`.
Do not paste token material into runbooks, tickets, or project comments.

## Incident Response

Revoke the affected token, pause related jobs, check audit logs, rotate related
secret-ref values, and re-run security validation.
