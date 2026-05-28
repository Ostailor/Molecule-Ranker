# Integration Sync Runbook

## Purpose

Operate existing V1.0 external integration syncs in dry-run or approved write
mode while preserving provenance and mapping review.

## Prerequisites

- External system registered in the platform database.
- Credential reference stored through the credential manager.
- Data contract and object type reviewed.
- Dry-run sync completed before any write-enabled sync.

## Commands

```bash
molecule-ranker integration system list --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker integration sync run \
  --external-system-id ext-example \
  --direction import \
  --object-type assay_results \
  --project-id project-example \
  --dry-run \
  --database-url "$MOLECULE_RANKER_DATABASE_URL" \
  --json
molecule-ranker integration mapping list --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
```

## Expected Output

- Sync job is recorded with mode `dry_run`.
- Records seen, warnings, and provenance metadata are captured.
- Mapping review queue contains pending, approved, or rejected states.
- No external write occurs in dry-run mode.

## Failure Modes

- External system ID is missing.
- Credential reference is absent or invalid.
- Data contract validation fails.
- Mapping conflicts require review.
- Write-enabled sync is requested for a dry-run-only connector.

## Rollback Steps

1. Pause the connector in platform configuration.
2. Preserve sync job, mapping, and audit records.
3. Reject incorrect mappings.
4. Restore previous connector configuration.
5. Repeat dry-run sync before approving any write-enabled operation.

## Safety/Security Notes

- Keep sync dry-run by default.
- Do not paste raw external credentials into CLI commands.
- External records are provenance and mapping metadata, not independent
  biomedical truth.
