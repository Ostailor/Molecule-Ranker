# Deployment Runbook

## Purpose

Deploy molecule-ranker V1.5 as an internal research platform MVP with hosted
auth, RBAC, jobs, audit logs, and artifact storage enabled.

## Prerequisites

- A reviewed V1.5 release package.
- A platform database URL or SQLite path for the target environment.
- Artifact storage and backup directories owned by the service account.
- Secret values supplied by the approved secret manager, not pasted into files.

## Commands

```bash
uv sync --all-groups --frozen
molecule-ranker db migrate --database-url "$MOLECULE_RANKER_DATABASE_URL"
molecule-ranker platform readiness --environment production --json
molecule-ranker serve --hosted --host 127.0.0.1 --port 8765
```

## Expected Output

- `db migrate` reports the current schema migration.
- `platform readiness` returns `"status": "pass"`.
- `/health`, `/ready`, `/version`, and `/metrics` return HTTP 200.
- `/version` reports `1.7.0` and V1 contract identifiers.

## Failure Modes

- Database connection fails or migrations are not current.
- Production secret key, allowed hosts, or auth mode is missing.
- Artifact storage is not writable.
- Readiness reports worker, backup, retention, or metrics failures.

## Rollback Steps

1. Stop the newly deployed web and worker processes.
2. Preserve logs, audit events, and the failed readiness report.
3. Restore the previous approved release package.
4. Restore the last verified backup only if a migration or write changed data.
5. Re-run `molecule-ranker platform readiness --environment production`.

## Safety/Security Notes

- Do not put secret values in command history, source files, or tickets.
- Do not skip auth, RBAC, webhook signature, audit, or readiness checks.
- This platform is for internal research triage only and does not produce
  clinical guidance or validated biomedical conclusions.
