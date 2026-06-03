# Operator Guide

Operators run the platform, validate releases, monitor SLOs, perform
backup/restore drills, manage incidents, and support enterprise deployments.

## Daily Operations

- Review health and readiness endpoints.
- Review job queues and failed jobs.
- Review SLO reports and error budgets.
- Verify backup freshness.
- Monitor auth failures, integration sync success, artifact write success, and
  support bundle generation success.
- Check audit logs for admin and external-write actions.

## Runbooks

Use the deployment, monitoring, backup/restore, worker, Codex worker, security
incident, integration sync, and support bundle runbooks. Runbooks are
operational process documents; they must not contain lab protocols, synthesis
instructions, dosing guidance, or patient treatment guidance.

## Incident Handling

For security incidents, revoke affected sessions and service tokens, disable
integration writes if needed, preserve audit evidence, generate redacted support
bundles, and document remediation before restoring normal operations.
