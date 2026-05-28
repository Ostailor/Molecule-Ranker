# Codex Worker Runbook

## Purpose

Operate the guarded Codex worker as an assistant backbone for summaries,
candidate explanations, dossiers, dashboards, and engineering support.

## Prerequisites

- Codex worker explicitly enabled for the environment.
- `codex:run` permission assigned only to approved users or service accounts.
- Artifact storage and scoped workspace directories are writable.
- Guardrail tests and security audit pass.

## Commands

```bash
molecule-ranker codex status --json
molecule-ranker admin codex-status --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker worker run --database-url "$MOLECULE_RANKER_DATABASE_URL"
molecule-ranker validate guardrails results/example-disease-a/
```

## Expected Output

- Codex status reports whether the CLI command is available.
- Admin status shows queued Codex jobs and status counts.
- Guardrail validation passes for generated Codex artifacts.
- Codex outputs are stored separately from evidence records.

## Failure Modes

- Codex CLI command is unavailable.
- Worker cannot access scoped artifacts.
- Output contains blocked claims or unsafe content.
- A user lacks `codex:run` permission.
- Transcript storage policy is misconfigured.

## Rollback Steps

1. Stop Codex-capable workers.
2. Mark affected jobs failed or guardrail_failed.
3. Preserve redacted transcripts and audit records.
4. Disable Codex worker configuration.
5. Re-run security and guardrail validation before re-enabling.

## Safety/Security Notes

- Codex output is assistant output, not evidence.
- Do not let Codex read arbitrary files or secret paths.
- Do not promote Codex-generated biomedical statements into source-backed
  evidence records.
