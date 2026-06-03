# V2.0 Deployment Hardening

molecule-ranker is internal research software, not a regulated clinical product.
It does not provide medical advice, dosing guidance, synthesis instructions, lab
protocols, or patient treatment guidance.

## Container Hardening

- Run containers as non-root UID/GID `1000`.
- Drop Linux capabilities and set `no-new-privileges`.
- Keep logs on stdout/stderr.
- Mount only `/data/artifacts`, `/data/storage`, and `/data/projects` as durable
  data paths.
- Do not bake secrets, `.env` files, Codex credentials, OIDC client secrets, or
  service tokens into images.

## Secrets

- Use secret-manager references or Docker/Kubernetes secrets.
- Prefer `_FILE` style runtime secrets for Compose.
- Use Kubernetes `secretKeyRef` or an ExternalSecret controller for cluster
  deployments.
- Never include tokens in support bundles, audit metadata, metrics, artifacts, or
  Codex prompts.

## Network And Identity

- Enforce HTTPS in production.
- Put the server behind approved internal ingress, WAF, or reverse proxy controls.
- Require enterprise identity for hosted deployments.
- Keep external integration writes disabled by default until approved and audited.

## Codex Worker Isolation

- Keep `MOLECULE_RANKER_ENABLE_CODEX_WORKER=false` unless explicitly enabled.
- Run Codex worker separately from server and normal worker.
- Mount project and artifact paths read-only for Codex where feasible.
- Use separate scratch storage for Codex transcripts and task workspaces.
- Redact transcripts from support bundles by default.

## Resource Limits

Document and enforce resource limits:

- Server: CPU/memory sized from API and dashboard SLOs.
- Worker: CPU/memory sized from job throughput and queue latency.
- Codex worker: separate lower concurrency and memory budget.
- Postgres: production config with managed backups and tested restore.

Do not remove resource limits as a substitute for queue, model, or artifact
storage tuning.

## Backup And Restore

- Back up Postgres plus artifact/project/platform volumes together.
- Verify backup manifests and artifact hashes.
- Restore to a temporary environment first.
- Run `molecule-ranker platform dr-drill`.
- Confirm no secrets are present in backup artifacts or support bundles.

## Operational Gates

Before promotion:

- `/health`, `/ready`, `/version`, and `/metrics` return expected results.
- `molecule-ranker ops slo-report` generates a redacted report.
- `molecule-ranker validate isolation` passes.
- `molecule-ranker validate v2-package` generates a complete package.
- `molecule-ranker v2 release-gate` passes when the release gate is available in
  the target branch.
