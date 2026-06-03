# molecule-ranker V2.0 Enterprise Deployment

molecule-ranker V2.0 is a validated enterprise discovery operating system for
internal research teams. It is internal research software, not a regulated
clinical product. It does not provide medical advice, dosing guidance, synthesis
instructions, lab protocols, or patient treatment guidance.

V2.0 deployment packaging covers five targets:

1. Single-node Docker Compose.
2. Split server/worker Docker Compose.
3. Kubernetes manifests.
4. Helm-like templates.
5. Offline/local deployment guide.

External integration writes are disabled by default in every enterprise example.
Enable writes only after admin approval, policy review, and audit readiness.

## Container Image

Build the V2.0 image from the repository root:

```bash
docker build -f deployment/Dockerfile -t molecule-ranker:2.1.0 .
```

The image:

- Runs as the non-root `molecule-ranker` user.
- Exposes `/health`, `/ready`, `/version`, and `/metrics`.
- Logs to stdout/stderr.
- Stores data only in mounted `/data/artifacts`, `/data/storage`, and
  `/data/projects` paths.
- Does not bake auth, OIDC, Codex, database, or service-token secrets into the
  image.

## Single-Node Docker Compose

Use the enterprise compose file for an all-in-one internal node with Postgres,
server, worker, and shared volumes:

```bash
mkdir -p deployment/secrets
printf '%s' "$POSTGRES_PASSWORD_FROM_SECRET_MANAGER" > deployment/secrets/postgres_password
printf '%s' "$MOLECULE_RANKER_AUTH_SECRET_FROM_SECRET_MANAGER" > deployment/secrets/auth_secret
docker compose -f deployment/docker-compose.enterprise.yml up --build
```

The server binds to `127.0.0.1:8765` unless `MOLECULE_RANKER_BIND` is set.
Run behind an approved TLS reverse proxy or private network ingress.

## Split Server/Worker Docker Compose

The same file separates runtime concerns:

```bash
docker compose -f deployment/docker-compose.enterprise.yml up molecule-ranker-server
docker compose -f deployment/docker-compose.enterprise.yml up molecule-ranker-worker
```

The optional Codex worker is isolated behind a profile and mounts artifacts and
projects read-only:

```bash
docker compose -f deployment/docker-compose.enterprise.yml --profile codex up molecule-ranker-codex-worker
```

Codex remains an orchestration/summarization backbone. It cannot create
evidence, assay results, molecules, scores, or review decisions.

## Kubernetes Manifests

Static manifests are under `deployment/k8s/`:

- `deployment.yaml`: server and non-Codex worker.
- `codex-worker.yaml`: optional Codex worker, scaled to zero by default.
- `service.yaml`: internal ClusterIP service for the server.
- `ingress.yaml`: TLS ingress example.
- `secret.example.yaml`: placeholder key names only.

Apply real secrets through your secret manager or ExternalSecret controller,
then apply storage, workloads, service, and ingress:

```bash
kubectl apply -f deployment/k8s/secret.example.yaml
kubectl apply -f deployment/k8s/deployment.yaml
kubectl apply -f deployment/k8s/service.yaml
kubectl apply -f deployment/k8s/ingress.yaml
```

Do not apply `secret.example.yaml` with real values committed to git.

## Helm-Like Template

The chart-like package lives under `deployment/helm/`. Render with Helm or use
the templates as a controlled starting point:

```bash
helm template molecule-ranker deployment/helm \
  --set secretRefs.existingSecret=molecule-ranker-secrets \
  --set image.repository=registry.example.internal/molecule-ranker \
  --set image.tag=2.1.0
```

Default values include resource limits and set
`MOLECULE_RANKER_EXTERNAL_WRITES_ENABLED=false`.

## Terraform Scaffold

`deployment/terraform/` provides a minimal Kubernetes namespace/PVC and
secret-reference contract. Production teams should wire it to their approved
provider, managed Postgres, storage class, and secret manager.

## Offline/Local Deployment

For offline/local deployment:

1. Mirror `molecule-ranker:2.1.0` into the internal registry or load it with
   `docker load`.
2. Pre-create secret files or Kubernetes secret-manager entries from offline
   approved material.
3. Use `deployment/docker-compose.enterprise.yml` with local volumes, or render
   `deployment/helm/` into an offline cluster.
4. Run `molecule-ranker platform readiness`, `molecule-ranker ops slo-report`,
   and `molecule-ranker platform dr-drill` before handing the environment to
   users.

## Health, Readiness, And SLOs

- Liveness: `GET /health`.
- Readiness: `GET /ready`.
- Version and contracts: `GET /version`.
- Metrics: `GET /metrics`.
- SLO report: `molecule-ranker ops slo-report`.

V2.0 readiness should confirm version `2.1.0`, `/api/v2`, V2 artifact
contracts, policy enforcement, isolation audit, backup verification, and release
gate status.

## Backup And Restore

Back up Postgres and the artifact/project/platform volumes as one coordinated
operation. Restore into a temporary environment first, then run:

```bash
molecule-ranker platform restore --help
molecule-ranker platform dr-drill --json
molecule-ranker validate v2-package --output validation_package/
```

Backups and support bundles must exclude cache directories, `.env` files, Codex
runtime credentials, service tokens, OIDC secrets, and secret-manager mounts.

## Resource Limits

Default examples document these starting limits:

- Server: 2 CPU, 2 GiB memory.
- Worker: 2 CPU, 3 GiB memory.
- Codex worker: 1 CPU, 2 GiB memory.
- Postgres compose service: 2 CPU, 2 GiB memory.

Tune from observed SLO reports and job profiles. Do not remove resource limits
to mask queue or memory pressure.

## Production Notes

- Use HTTPS in production.
- Keep logs on stdout/stderr and aggregate them centrally.
- Keep external integration writes disabled by default.
- Keep Codex worker optional, scoped, and isolated.
- Use secret references through environment/secret manager mechanisms.
- Run the release gate before promoting V2.0 deployment artifacts.
