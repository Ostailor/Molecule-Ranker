# Local Development Runbook

## Purpose

Run molecule-ranker V1.0 locally for deterministic development, validation, and
operator training without live external services.

## Prerequisites

- Python environment managed by `uv`.
- Local writable workspace.
- No public API keys are required for default validation.
- Synthetic or fixture data only.

## Commands

```bash
uv sync --all-groups
uv run pytest tests_validation
tmpdir="$(mktemp -d)"
molecule-ranker db init --root "$tmpdir" --db-path "$tmpdir/platform.sqlite" --json
molecule-ranker platform readiness --root "$tmpdir" --db-path "$tmpdir/platform.sqlite" --json
molecule-ranker project create --root "$tmpdir" --workspace-id local-dev --json
```

## Expected Output

- Validation tests pass using mocked external services.
- SQLite database initialization returns `"ok": true`.
- Readiness returns pass or a development-only warning.
- Project creation writes `.molecule-ranker/workspace.json`.

## Failure Modes

- Dependency install fails due to an unlocked or partial environment.
- SQLite path is not writable.
- Validation fails because generated artifacts from another run polluted the
  local workspace.
- A local environment variable unexpectedly enables live integration behavior.

## Rollback Steps

1. Stop local servers and workers.
2. Delete the temporary workspace created for the failed run.
3. Re-run `uv sync --all-groups`.
4. Recreate the workspace with a fresh temporary directory.

## Safety/Security Notes

- Use temporary directories for local smoke checks.
- Keep external integrations in dry-run mode unless a separate live validation
  plan has been approved.
- Do not store secrets in `.env` files under demo or artifact directories.
