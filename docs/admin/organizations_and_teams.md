# Organizations and Teams

## Purpose

Organizations and teams group molecule-ranker users for internal research
project access. They simplify RBAC matrix decisions without replacing
project-specific permission review.

## RBAC Matrix

| Scope | Applies to | Typical use | Admin action |
| --- | --- | --- | --- |
| organization | all members | baseline membership | verify membership |
| team | subgroup | project collaboration | map to project role |
| project | workspace | artifact access | grant explicit permission |
| platform | admins | operations | restrict to operators |

## Permission Descriptions

Organization membership does not automatically grant all project permissions.
Project permissions still control read, run, review, assay import, integration,
project export, and delete actions.

## Commands

```bash
molecule-ranker admin orgs --json
molecule-ranker admin users --json
molecule-ranker admin audit --json
```

## Expected Output

Admin views show organizations, teams, users, and membership metadata with
redacted security-sensitive fields.

## Failure Modes

- Team membership exists but project access is missing.
- A user is assigned to the wrong team.
- Platform admin role is granted too broadly.

## Project Export/Delete Guidance

Confirm organization and team ownership before project export or delete.
Exported packages must stay inside approved internal storage.

## Audit Review And Incident Response

During incident response, list recent membership changes, project grants, and
service token lifecycle events. Preserve audit review evidence before changing
roles.

## Credential Secret-Ref Guidance

Teams should reference credentials by secret-ref only. Do not copy credential
values into organization notes, team descriptions, or project comments.
