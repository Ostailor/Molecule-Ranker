# Deployment

V2.0 supports single-node Docker Compose, split server/worker Docker Compose,
Kubernetes manifests, Helm-like templates, and offline/local deployment
guidance.

## Deployment Principles

- Run containers as non-root users.
- Separate server and worker processes.
- Use Postgres for production database deployments.
- Use durable object/artifact storage.
- Pass secrets through environment references or secret managers.
- Log to stdout/stderr.
- Configure health and readiness checks.
- Document resource limits and capacity assumptions.
- Disable external integration writes by default.
- Keep Codex workers optional and isolated.

## Production Checklist

- Confirm `/health`, `/ready`, `/api/v2/version`, and admin health endpoints.
- Configure OIDC and allowed email domains.
- Configure RBAC before project onboarding.
- Configure backup schedule and restore verification.
- Configure SLO reporting and alerts.
- Run deployment smoke tests.
- Run `molecule-ranker v2 release-gate`.

## Offline/Local Use

Offline/local deployments are for internal research evaluation, training,
operator drills, or isolated synthetic demos. They must not contain real
secrets in images or examples and must not be used to produce clinical,
procedural, dosing, synthesis, or treatment guidance.
