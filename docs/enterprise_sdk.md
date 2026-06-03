# Enterprise SDK

The molecule-ranker SDK targets the stable V2.0 enterprise platform release
while preserving the `/api/v1` route family and `api.v1` contract identifier.

## Client Responsibilities

- Read `/version` or `/api/v1/version` during startup and log the reported
  package version plus contract identifiers.
- Fail closed when the server reports an unsupported API contract.
- Use scoped bearer tokens or service-account tokens.
- Keep tenant and project IDs explicit in project, artifact, job, review,
  experiment, and Codex calls.
- Treat server validation and support-bundle artifacts as software/process
  evidence only.

## Stability

V2.0 clients can rely on stable required fields, route paths, auth
requirements, permission requirements, and error envelope semantics for the
registered `api.v1` routes. Additive fields may appear in responses.

## Safety Boundary

The SDK must not convert generated molecules, model predictions, docking
outputs, graph summaries, benchmark reports, prospective validation analytics,
or Codex outputs into biomedical evidence or claims.
