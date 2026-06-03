# Terraform Deployment Notes

This directory is a Helm-like infrastructure scaffold for V2.0 enterprise
operators. It is intentionally small: production teams should bind these
resources to their approved Kubernetes provider, managed Postgres service,
object/artifact storage class, backup system, and secret manager.

molecule-ranker is internal research software, not a regulated clinical product.
It does not provide medical advice, dosing guidance, synthesis instructions, lab
protocols, or patient treatment guidance.

## Expected Inputs

- A Kubernetes namespace dedicated to molecule-ranker.
- A managed PostgreSQL endpoint stored in a secret manager.
- Secret references for `auth-secret` and `database-url`.
- Persistent volumes for artifacts, project workspaces, platform storage, and
  optional Codex worker scratch storage.
- Network rules that keep external integration writes disabled by default.

## Offline/Local Deployment

For an offline/local deployment, mirror the container image and chart into your
internal registry, pre-create the namespace, create PVCs from local storage
classes, then apply the rendered manifests. Keep backup and restore tooling local
to that environment and run the DR drill before release.

## Backup And Restore

Back up PostgreSQL and all persistent volumes in one coordinated operation.
Restore to a temporary namespace first, then run migrations, artifact hash
validation, support-bundle redaction checks, and `molecule-ranker platform
dr-drill`.

## Resource Limits

The default chart values document resource limits for server, worker, and Codex
worker pods. Tune these limits from observed SLO reports rather than removing
them.
