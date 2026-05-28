# Backup and Restore Runbook

## Purpose

Create, verify, and restore V1.0 internal MVP backups containing platform
metadata, artifact registry records, safe artifact files, and manifest hashes.

## Prerequisites

- Writable backup destination outside cache and worker scratch directories.
- Platform database access.
- Enough disk space for database copies and artifact files.
- An isolated restore target for validation.

## Commands

```bash
molecule-ranker platform backup \
  --root /srv/molecule-ranker \
  --database-url "$MOLECULE_RANKER_DATABASE_URL" \
  --output /backups/molecule-ranker-v1-demo.zip \
  --json

molecule-ranker platform backup-verify /backups/molecule-ranker-v1-demo.zip --json

molecule-ranker platform restore \
  --input /backups/molecule-ranker-v1-demo.zip \
  --target-dir /restore/molecule-ranker-v1-demo \
  --dry-run \
  --json
```

## Expected Output

- Backup returns `"status": "pass"` and an entry count.
- Verification returns `"status": "pass"` with checked hashes.
- Restore dry-run returns `"dry_run": true` and writes no files.
- A real restore writes `database/`, `artifacts/`, and manifest-backed files.

## Failure Modes

- Backup archive lacks `backup_manifest.json`.
- Hash or size mismatch during verification.
- Artifact file is missing or excluded by safety policy.
- Restore target is not writable.
- Archive contains unsafe paths.

## Rollback Steps

1. Stop any restore process that reports verification failures.
2. Delete the incomplete restore target.
3. Select the previous verified backup.
4. Run `backup-verify` and restore dry-run before extracting.
5. Record the failed archive path and verification errors.

## Safety/Security Notes

- Backups exclude cache files, environment files, raw credentials, and temporary
  worker directories by default.
- Keep backup archives in approved protected storage.
- Do not include unredacted Codex transcripts unless policy explicitly requires
  them and access controls are in place.
