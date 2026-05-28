# Artifact Storage

## Purpose

Artifact storage holds molecule-ranker run outputs, reports, trace files,
review exports, backups, dashboard files, and integration export packages.

## RBAC Matrix

| Action | viewer | reviewer | editor | project_owner | platform_admin |
| --- | --- | --- | --- | --- | --- |
| view artifact metadata | yes | yes | yes | yes | yes |
| download artifact | limited | limited | yes | yes | yes |
| register artifact | no | no | yes | yes | yes |
| project export | no | no | limited | yes | yes |
| delete artifact metadata | no | no | no | limited | yes |

## Permission Descriptions

Artifact permissions protect files and metadata. Access to an artifact does not
grant authority to make clinical claims.

## Commands

```bash
molecule-ranker project artifacts --root /srv/molecule-ranker --json
molecule-ranker platform backup --root /srv/molecule-ranker --output backup-placeholder.zip --json
molecule-ranker platform backup-verify backup-placeholder.zip --json
```

## Expected Output

Artifact listings include artifact IDs, paths, sizes, hashes, and provenance.
Backup verification reports manifest hash checks.

## Failure Modes

- Artifact path is outside allowed storage.
- File hash does not match registry metadata.
- Export attempts to include cache, environment, or secret-like files.

## Project Export/Delete Guidance

Before project export, check artifact manifests and excluded files. Before
delete, verify backup and retention policy. Use soft delete when recovery may be
needed.

## Credential Secret-Ref Guidance

Do not store credential material in artifact paths. Connector outputs should
reference secret-ref identifiers only.

## Incident Response

If artifact access is suspicious, pause downloads, preserve audit logs, verify
hashes against manifests, and review recent project export events.
