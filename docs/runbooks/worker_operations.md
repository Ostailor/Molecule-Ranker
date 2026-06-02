# Worker Operations Runbook

## Purpose

Operate V1.0 background jobs for ranking, generation, developability,
experiments, integrations, dashboards, and guarded Codex tasks.

## Prerequisites

- Platform database initialized and reachable.
- A service account with the required permissions.
- Worker host has access to the project root and artifact storage.
- Readiness checks have passed or warnings are documented.

## Commands

```bash
molecule-ranker platform readiness --json
molecule-ranker worker run --root /srv/molecule-ranker --database-url "$MOLECULE_RANKER_DATABASE_URL"
molecule-ranker job list --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker admin jobs --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
```

## Expected Output

- Worker starts without database errors.
- Queued jobs move to running and then succeeded, failed, cancelled, or
  guardrail_failed.
- Job list output includes redacted config snapshots.
- Audit logs record enqueue, start, completion, cancellation, and failures.

## Failure Modes

- Worker cannot claim jobs due to database connectivity.
- Job remains queued because no worker is running.
- Job fails due to missing artifacts or invalid configuration.
- Codex jobs fail guardrails.
- Integration jobs remain dry-run when write mode was expected.

## V1.9 Job Control

- Queued jobs cancel immediately.
- Running jobs mark `cancel_requested` and stop at the next worker checkpoint
  when the handler supports cooperative cancellation.
- Failed and guardrail-failed jobs can be retried from redacted config
  snapshots after an operator confirms the failure cause.
- Resume summaries expose checkpoint state and cancel state, but redact resume
  tokens and sensitive metadata.
- Retry, cancel, success, failure, and guardrail-failure events remain auditable.

## Rollback Steps

1. Stop the worker process.
2. Inspect the failed job and audit event.
3. Cancel queued jobs that depend on bad configuration.
4. Restore previous worker package or configuration.
5. Restart one worker and verify a synthetic test job before scaling.

## Safety/Security Notes

- Do not grant broad permissions to worker identities.
- Do not rerun failed jobs until the artifact paths and permissions are checked.
- Never treat worker-generated summaries as biomedical evidence.
