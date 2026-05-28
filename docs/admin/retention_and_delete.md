# Retention and Delete

## Purpose

Retention and delete controls manage old artifacts, Codex transcripts, audit
logs, cache files, assay results, project exports, and user data according to
approved policy.

## RBAC Matrix

| Action | project_owner | platform_admin |
| --- | --- | --- |
| request project export | yes | yes |
| soft delete project | limited | yes |
| purge project | no | yes |
| run retention | no | yes |
| restore from backup | no | yes |

## Permission Descriptions

Delete permissions are operational controls. They do not remove the need for
backup verification, audit review, or incident response records.

## Commands

```bash
molecule-ranker platform export-project project-example --json
molecule-ranker platform delete-project project-example --soft --json
molecule-ranker platform retention run --artifact-retention-days 365 --json
```

## Expected Output

Project export returns package path, artifact count, skipped artifact count,
and hash. Delete and retention commands write audit events.

## Failure Modes

- Backup was not verified before delete.
- Project ID confirmation is missing for purge.
- Retention window is absent or conflicts with policy.
- Export excludes files that are missing or unsafe to package.

## Project Export/Delete Guidance

Use project export for handoff and recovery packages. Use soft delete before
purge whenever possible. Purge requires explicit project ID confirmation and a
verified backup.

## Credential Secret-Ref Guidance

Exports must include credential references only when necessary for provenance,
never raw credential values.

## Incident Response

If data was deleted incorrectly, stop retention jobs, preserve audit logs,
restore from verified backup, and document scope and recovery time.
