# Admin Checklist

## Daily Checks

- Open `/dashboard/admin/support`.
- Review pilot readiness summary.
- Review worker status and queue backlog.
- Review failed jobs and dead-letter jobs.
- Review guardrail failures.
- Review backup status and retention status.
- Review auth failures and webhook signature failures if integrations are enabled.
- Confirm no secrets or cache payloads appear in redacted logs.

## Before Adding Users

- Confirm project owner is assigned.
- Confirm role mapping is correct.
- Confirm admin count is minimal.
- Confirm service tokens are scoped to required actions only.
- Confirm audit logging is enabled.

## Admin Actions

Every admin action must be auditable:

- Retry failed job.
- Cancel job.
- Move dead-letter job back to queue.
- Generate support bundle.
- Run readiness check.
- Run migration dry-run.
- Run backup verification.
- View redacted logs.

## Commands

```bash
uv run molecule-ranker ops metrics --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker ops alerts --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker ops health-history --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker support bundle --root "$PILOT_ROOT" --output "$PILOT_ROOT/support_bundle.zip"
```

## Escalation

Escalate immediately if any surface displays secrets, cache payloads, unredacted
tokens, unauthorized project data, or generated hypotheses without clear labels.

