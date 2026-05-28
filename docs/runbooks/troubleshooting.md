# Troubleshooting Runbook

## Purpose

Provide first-line diagnostics for failed deployments, jobs, validation,
exports, dashboards, integrations, and guarded Codex tasks.

## Prerequisites

- Access to the relevant project root.
- Redacted configuration view.
- Platform database connection.
- Recent readiness, security, or validation output.

## Commands

```bash
molecule-ranker config show --redacted
molecule-ranker platform doctor --json
molecule-ranker validate release --json
molecule-ranker job list --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker admin audit --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
```

## Expected Output

- Config output is redacted.
- Doctor reports pass, warn, or fail for each readiness category.
- Release validation uses mocked external services by default.
- Job and audit output identify recent failed operations.

## Failure Modes

- CLI command cannot import the package.
- Database schema is missing tables.
- Artifact contract validation fails.
- Guardrail audit reports unsafe claims.
- Dashboard cannot find registered workspace artifacts.

## Rollback Steps

1. Stop new jobs for the affected project.
2. Save failing command output.
3. Revert to the previous approved configuration or release package.
4. Restore artifacts from verified backup if files were corrupted.
5. Re-run the exact failing diagnostic command.

## Safety/Security Notes

- Keep diagnostic output redacted before sharing.
- Do not edit database rows manually to force checks to pass.
- Use synthetic data for reproduction unless production data access is approved.
