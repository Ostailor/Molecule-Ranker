# Security Incidents Runbook

## Purpose

Respond to suspected security events affecting auth, RBAC, service tokens,
webhooks, artifact access, Codex worker boundaries, integrations, or exports.

## Prerequisites

- Incident lead assigned.
- Access to audit logs, deployment logs, and backup manifests.
- Ability to pause workers, connectors, or service tokens.
- Current security audit command available.

## Commands

```bash
molecule-ranker validate security --json
molecule-ranker admin audit --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker job list --database-url "$MOLECULE_RANKER_DATABASE_URL" --json
molecule-ranker platform backup --database-url "$MOLECULE_RANKER_DATABASE_URL" --output incident-snapshot.zip --json
```

## Expected Output

- Security audit reports pass or specific failed checks.
- Audit output contains actor, event type, object, timestamp, and redacted
  metadata.
- Job list identifies queued or running work that may need cancellation.
- Incident snapshot has a backup manifest and hashes.

## Failure Modes

- Audit logs are unavailable.
- Suspicious artifact download path appears in logs.
- Service token use does not match expected actors.
- Webhook signature verification fails.
- Export package includes unexpected files.

## Rollback Steps

1. Pause affected worker or integration components.
2. Revoke affected service tokens through the approved admin path.
3. Preserve current logs and backup snapshot.
4. Restore last known-good configuration or release package.
5. Validate security, readiness, and guardrails before reopening access.

## Safety/Security Notes

- Do not delete audit evidence during triage.
- Do not weaken auth, RBAC, or webhook signature checks during recovery.
- Share only redacted incident summaries outside the response group.
