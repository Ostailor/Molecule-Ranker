# Troubleshooting

Use this guide for enterprise support triage. Do not paste secrets, tokens,
patient treatment guidance, dosing information, lab protocols, or synthesis
instructions into tickets, Codex prompts, or support bundles.

## Access Issues

- Confirm the user is active.
- Confirm OIDC group-to-role mapping.
- Confirm project permission or team/org membership.
- Confirm service account scopes.
- Check audit logs for denied permissions.

## Artifact Issues

- Validate artifact contract and hash.
- Confirm artifact namespace and project scope.
- Confirm the path is not cache, secret, or outside the project.
- Confirm download permission.

## Job Issues

- Check queue state, job status, worker health, and project scope.
- Review redacted job config and metadata.
- Retry only when the job is safe to retry or has an idempotency key.

## Escalation

Generate a redacted support bundle, attach request IDs, include release version,
contract versions, SLO report, and relevant audit event IDs. Do not attach raw
secrets, full copyrighted text, raw assay files, Codex transcripts by default,
or unapproved sensitive data.
