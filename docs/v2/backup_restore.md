# Backup and Restore

V2.0 upgrades backup/restore into a disaster recovery drill with verification
evidence.

## Commands

```bash
molecule-ranker platform backup
molecule-ranker platform restore
molecule-ranker platform dr-drill
```

## DR Drill

1. Create backup.
2. Verify backup manifest.
3. Restore to a temporary environment.
4. Run database migration check.
5. Validate artifact hashes.
6. Validate key projects and artifacts load.
7. Validate user and role metadata.
8. Validate no secrets are included.
9. Run smoke workflow on the restored environment.
10. Produce DR report.

## Operator Notes

Backups must not include plaintext service tokens, OIDC secrets, integration
credentials, cache directories, or unauthorized artifacts. Restore drills
validate platform continuity; they do not validate clinical, scientific,
procedural, dosing, or synthesis outcomes.
