# Data Retention Runbook

## Purpose

Apply V1.0 retention policies for artifacts, Codex transcripts, audit logs,
caches, and imported assay-result records.

## Prerequisites

- Retention windows approved for the environment.
- Current backup verified before destructive retention actions.
- Actor identity recorded for audit events.
- Scope and expected deletion mode reviewed.

## Commands

```bash
molecule-ranker platform retention run \
  --artifact-retention-days 365 \
  --codex-transcript-retention-days 90 \
  --audit-log-retention-days 730 \
  --cache-retention-days 30 \
  --assay-result-retention-days 365 \
  --database-url "$MOLECULE_RANKER_DATABASE_URL" \
  --json
```

## Expected Output

- Retention command returns counts for each retained or deleted category.
- Audit event records the retention run.
- Cache files older than the configured window are removed.
- Soft-deleted artifacts remain represented in metadata when configured.

## Failure Modes

- Retention policy is missing.
- Backup verification was not completed.
- File deletion fails due to permissions.
- Database write fails during metadata updates.
- Retention window is shorter than approved policy.

## Rollback Steps

1. Stop the retention run if errors appear.
2. Preserve logs and audit records.
3. Restore deleted files from the last verified backup if needed.
4. Reapply the corrected retention configuration.
5. Re-run retention on a small synthetic scope before broad execution.

## Safety/Security Notes

- Do not run retention without a verified backup.
- Do not remove audit logs outside approved retention windows.
- Do not use retention to hide incidents or failed validation results.
