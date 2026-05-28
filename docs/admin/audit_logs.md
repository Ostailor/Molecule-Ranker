# Audit Logs

## Purpose

Audit logs provide the administrative record for molecule-ranker users, roles,
project permissions, service token lifecycle, integrations, project export,
delete, Codex jobs, and retention actions.

## RBAC Matrix

| Role | View own project audit | View platform audit | Export audit |
| --- | --- | --- | --- |
| viewer | no | no | no |
| project_owner | limited | no | limited |
| platform_admin | yes | yes | yes |

## Permission Descriptions

Audit access is administrative. It should be limited to operators and project
owners with a clear need.

## Audit Review Procedures

Review audit logs after role changes, service token lifecycle events, project
export, delete, integration credential changes, Codex worker failures, and
security incidents.

## Commands

```bash
molecule-ranker admin audit --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker validate security --json
```

## Expected Output

Audit entries include event type, actor, object type, object ID, timestamp,
summary, and redacted metadata.

## Failure Modes

- Audit query fails because the database is unavailable.
- Expected event is missing.
- Metadata contains unexpected unredacted sensitive values.

## Project Export/Delete Guidance

Every project export, soft delete, and purge must have an audit trail. Preserve
the audit event ID in the operator record.

## Credential Secret-Ref Guidance

Credential events should record secret-ref labels and redacted status only.

## Incident Response

During incident response, preserve audit output before changing state. Compare
service account actions, exports, and integration syncs against the incident
timeline.
