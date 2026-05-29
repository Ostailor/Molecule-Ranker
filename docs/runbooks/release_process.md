# Release Process Runbook

## Purpose

Package and verify molecule-ranker V1.2 as a validated internal research
platform MVP.

## Prerequisites

- Release branch reviewed.
- Dependency lock file updated intentionally.
- Synthetic demo artifacts present.
- Backup/restore dry-run recorded.
- OpenAPI and artifact contracts exported.

## Commands

```bash
uv sync --all-groups --frozen
uv run ruff check .
uv run pyright
uv run pytest
molecule-ranker validate release --json
molecule-ranker validate security --json
molecule-ranker release check --json
molecule-ranker api export-openapi --output openapi-v1.json
```

## Expected Output

- Lint, typecheck, and tests pass.
- Release validation reports mocked external services and NullCodexProvider.
- Security audit reports pass.
- Release check reports all gates ready.
- OpenAPI schema includes `/api/v1/...` routes.

## Failure Modes

- Version is not `1.2.0`.
- Golden workflow or contract validation fails.
- Security or guardrail audit fails.
- Demo artifacts are missing or not clearly synthetic.
- Backup/restore evidence is missing.

## Rollback Steps

1. Do not tag the release.
2. Preserve failing validation output.
3. Revert the release package candidate.
4. Fix the failed gate with tests.
5. Restart the release command sequence from dependency sync.

## Safety/Security Notes

- Do not ship with skipped security, guardrail, readiness, or backup gates.
- Keep generated hypotheses labeled as hypotheses.
- Do not add fabricated citations or experimental outcomes to release demos.
