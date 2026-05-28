# Integration Credentials

## Purpose

Integration credentials let molecule-ranker connect to existing systems while
keeping secret values outside project artifacts and documentation.

## RBAC Matrix

| Action | editor | project_owner | platform_admin |
| --- | --- | --- | --- |
| view connector metadata | limited | limited | yes |
| create credential reference | no | no | yes |
| test credential reference | no | no | yes |
| run dry-run sync | limited | limited | yes |
| approve write-enabled connector | no | no | yes |

## Permission Descriptions

Integration permissions control connector metadata, sync jobs, mapping review,
credential references, and warehouse exports.

## Credential Secret-Ref Guidance

Use secret-ref values such as `env:BENCHLING_TOKEN_PLACEHOLDER` or
`secret-manager:integration/example`. Store only the reference in
molecule-ranker. Do not store raw credential values in project artifacts,
comments, dashboards, exports, or audit metadata.

## Commands

```bash
molecule-ranker integration credential create \
  --external-system-id ext-example \
  --credential-type api_key \
  --secret-env-var BENCHLING_TOKEN_PLACEHOLDER \
  --json
molecule-ranker integration credential list --json
```

## Expected Output

Credential output shows credential ID, type, external system, and secret-ref. It
must not show credential material.

## Failure Modes

- Secret-ref environment variable is missing.
- Credential is scoped to the wrong external system.
- Dry-run sync fails contract validation.

## Project Export/Delete Guidance

Project export packages must exclude raw credentials. Delete operations must not
remove central credential audit history unless retention policy allows it.

## Incident Response

Pause affected connectors, rotate the secret-ref value in the secret manager,
review sync jobs, and re-run security validation.
