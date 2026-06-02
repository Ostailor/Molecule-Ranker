# Troubleshooting Decision Tree

## Start

Capture project ID, job ID, artifact ID, request ID, and page or command. Then
choose the first matching branch.

## Login Fails

- Check auth mode and local user status.
- Confirm the account is active.
- Check `/dashboard/admin/audit` for failed auth events.
- Do not reset credentials through shared channels.

## User Cannot See Project

- Confirm project ID.
- Confirm user, team, or organization permission.
- Confirm RBAC is enabled.
- Confirm the user is not relying on a service token for browser access.

## Page Is Empty

- Check whether a project exists.
- Check whether the run is registered.
- Check whether artifacts are registered and inside the project root.
- Check empty-state guidance on the dashboard.

## Artifact Missing

- Confirm artifact ID.
- Confirm the artifact path exists.
- Confirm it is not a cache or secret path.
- Confirm project access.
- Regenerate or re-register the artifact if needed.

## Job Failed

- Open `/dashboard/admin/jobs` or `/dashboard/admin/support`.
- Read redacted error summary.
- Check worker status and queue backlog.
- Retry only if the job type and idempotency policy allow it.
- Move dead-letter jobs back to queue only after cause is fixed.

## Readiness Fails

- Open the readiness report.
- Fix blockers before launch.
- Re-run readiness after every change.

```bash
uv run molecule-ranker pilot readiness --root "$PILOT_ROOT" --db-path "$PILOT_DB" --json
```

## Secret Exposure Concern

- Stop sharing the output.
- Preserve the request ID and page path.
- Generate a redacted support bundle.
- Rotate affected credentials outside this runbook if exposure is confirmed.

