# Security Checklist

## Purpose

This checklist helps administrators validate molecule-ranker security posture
for hosted V1.0 deployments.

## RBAC Matrix

| Control | Required owner | Evidence |
| --- | --- | --- |
| users and roles | platform_admin | audit logs |
| project permissions | project_owner | permission review |
| service token lifecycle | platform_admin | token audit |
| integration credentials | platform_admin | secret-ref records |
| artifact storage | operator | backup manifests |
| retention and delete | platform_admin | retention audit |
| incident response | incident lead | incident record |

## Permission Descriptions

Use least privilege. Assign only the permissions needed for project read, run,
review, assay import, integration sync, project export, delete, Codex, or admin
operations.

## Commands

```bash
molecule-ranker validate security --json
molecule-ranker platform readiness --environment production --json
molecule-ranker admin audit --json
molecule-ranker config show --redacted
```

## Expected Output

Security validation and readiness return pass. Config output is redacted. Audit
logs show recent admin and service account activity.

## Failure Modes

- Password hashes or service tokens are mishandled.
- API keys appear in logs or exports.
- Artifact path traversal checks fail.
- Webhook signatures are not required.
- Codex worker boundaries are not enforced.

## Project Export/Delete Guidance

Verify project export packages exclude secrets and include manifest hashes.
Verify delete requests have backup, retention, and audit evidence.

## Credential Secret-Ref Guidance

Credential entries should use secret-ref strings only. Rotate referenced secret
values through the approved secret manager.

## Audit Review And Incident Response

Review audit logs daily for admin actions, service token lifecycle changes,
integration syncs, project export, delete, and Codex failures. During incident
response, pause affected components, revoke affected tokens, preserve evidence,
and rerun security validation before reopening access.
