# Production Configuration Runbook

## Purpose

Verify production configuration before a hosted molecule-ranker deployment is
made available to internal users.

## Prerequisites

- Approved secret-manager entries for platform auth and integration credentials.
- Explicit allowed host names.
- Debug mode off.
- Retention, backup, and audit settings selected by the operator team.

## Commands

```bash
molecule-ranker config show --redacted
molecule-ranker config validate
molecule-ranker platform readiness --environment production --json
molecule-ranker api export-openapi --output openapi-v1.json
```

## Expected Output

- `config show --redacted` never prints secret material.
- `config validate` reports `"ok": true`.
- Readiness reports pass for secret key, allowed hosts, debug, auth, storage,
  audit, retention, backup, and health checks.
- OpenAPI export completes and includes `/api/v1/...` routes.

## Failure Modes

- Missing secret key or wildcard allowed hosts in production.
- Debug is enabled.
- Auth mode is absent or unsupported.
- Integration credentials are enabled but not validated.
- Backup path or artifact storage path cannot be written.

## Rollback Steps

1. Remove the candidate configuration from the deployment target.
2. Reapply the previous known-good configuration through the deployment system.
3. Re-run `config show --redacted` and `platform readiness`.
4. Keep failed readiness output for the release record.

## Safety/Security Notes

- Never print raw secret values for troubleshooting.
- Do not weaken host, auth, webhook, or RBAC checks to make readiness pass.
- Treat Codex outputs as assistant artifacts, not evidence.
