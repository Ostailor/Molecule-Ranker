# Reliability Operations

## Daily Reliability Loop

1. Run readiness or inspect the latest readiness report.
2. Review metrics and alerts.
3. Review queue backlog and worker status.
4. Review failed, timed-out, cancelled, retrying, and dead-letter jobs.
5. Verify backup freshness.
6. Verify retention policy is configured.
7. Confirm no migration is pending.

## Commands

```bash
uv run molecule-ranker pilot readiness --root "$PILOT_ROOT" --db-path "$PILOT_DB" --json
uv run molecule-ranker ops metrics --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker ops alerts --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker migrate check --root "$PILOT_ROOT"
```

## Job Operations

- Retry only transient and idempotent jobs.
- Do not auto-retry non-idempotent external write jobs.
- Cancel jobs cooperatively where possible.
- Treat timed-out jobs as failed until artifacts are inspected.
- Dead-letter jobs require cause review before requeue.

## Backup And Retention

- Verify backup path exists and is writable.
- Keep backup age below the internal stale-backup threshold.
- Confirm retention policy for artifacts, audit logs, cache, and Codex transcripts.
- Do not expose cache payloads through support or dashboard views.

## Codex Worker

Codex worker may be disabled for pilot. If enabled:

- Confirm prompt and artifact context are unchanged before retry.
- Confirm transcripts are redacted before support use.
- Confirm output remains assistant output and not evidence or decisions.

