# molecule-ranker V1.0 Deployment

V1.0 is a validated internal research-platform MVP. It is not a regulated clinical product and
does not provide medical advice, dosing, synthesis instructions, lab protocols, or
patient treatment guidance.

## Modes

### 1. Local CLI mode

```bash
molecule-ranker --help
molecule-ranker db migrate --db-path .molecule-ranker/platform.sqlite
```

Use this for single-user/local workflows. Local SQLite metadata remains supported.

### 2. Local web mode

```bash
molecule-ranker serve \
  --root . \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --platform-db-path .molecule-ranker/platform.sqlite \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET"
```

The server binds to `127.0.0.1` by default. Binding to `0.0.0.0` requires
`--allow-public-bind` and should only be used behind approved internal network controls.

### 3. Docker Compose internal deployment

```bash
cp .env.example .env
# edit .env with values from your secret manager
docker compose up --build
```

The production compose file uses PostgreSQL through `MOLECULE_RANKER_DATABASE_URL`.
The dev compose file uses SQLite fallback:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Enable the optional Codex worker only when the host has a controlled Codex CLI
authentication setup:

```bash
docker compose --profile codex up codex-worker
```

### 4. Kubernetes manifests

Templates are under `deployment/k8s/`. Apply your own namespace, image registry,
TLS secret, network policy, persistent volume claims, and secret manager integration
before use.

```bash
kubectl apply -f deployment/k8s/secret.example.yaml
kubectl apply -f deployment/k8s/deployment.yaml
kubectl apply -f deployment/k8s/service.yaml
kubectl apply -f deployment/k8s/ingress.yaml
```

`secret.example.yaml` is a placeholder. Do not commit generated secrets.

## Storage

The container declares and compose mounts three durable paths:

- `/data/artifacts`: exported artifacts and transcript artifacts.
- `/data/storage`: platform metadata, SQLite fallback, worker scratch roots.
- `/data/projects`: project/review/experiment workspace data.

Do not expose cache directories, `.env` files, or credential mounts through artifact
download routes or dashboards.

## Database

Hosted deployments should use PostgreSQL:

```bash
export MOLECULE_RANKER_DATABASE_URL='postgresql+psycopg://...'
molecule-ranker db migrate --database-url "$MOLECULE_RANKER_DATABASE_URL"
```

Dev/local deployments can omit `MOLECULE_RANKER_DATABASE_URL` and use:

```bash
molecule-ranker db migrate --db-path /data/storage/platform.sqlite
```

## Health and readiness

Use:

- `GET /health` for process liveness.
- `GET /ready` for hosted database readiness.
- `GET /version` for deployed version and contract checks. A V1.0 deployment
  must report `version=1.0.0`, `api_contract_version=api.v1`,
  `artifact_contract_version=artifacts.v1`,
  `data_contract_version=data-contracts.v1`, and
  `warehouse_contract_version=mr_warehouse_v1.0.0`.

The Dockerfile health check uses `/health`; Kubernetes readiness uses `/ready`.

## Backup, restore, and disaster recovery

Before every V1.0 release cut and after every hosted deployment:

- Back up the platform database with the organization-approved PostgreSQL dump
  mechanism, or copy the SQLite database only after stopping writers.
- Back up `/data/artifacts`, `/data/projects`, and `/data/storage` together so
  artifact manifests, hashes, run metadata, and review workspaces remain
  consistent.
- Exclude cache directories, `.env` files, local Codex credentials, API keys,
  service tokens, and secret-manager mounts from backup artifacts.
- Restore into an isolated environment first, then run database migrations,
  `molecule-ranker db check`, `GET /ready`, `GET /version`, and one synthetic
  project export before allowing users back in.
- Record restore time, data-loss window, package hashes, and operator identity
  in the incident or release log.

## Codex CLI worker

Codex execution in hosted mode must run through the queued Codex worker. API routes
must not invoke Codex subprocesses directly.

Credential handling:

- Do not bake Codex, OpenAI, ChatGPT, or other LLM credentials into the image.
- Do not commit Codex credentials to this repo.
- Do not put credentials in `.env.example`, Kubernetes example secrets, artifacts,
  dashboard text, audit metadata, or Codex prompts.
- If your organization allows hosted Codex execution, provide credentials through an
  approved runtime mechanism outside project artifact paths, and restrict worker
  filesystem access to the mounted storage paths.

## Core commands

```bash
molecule-ranker serve
molecule-ranker worker run
molecule-ranker db migrate
```

The container entrypoint maps:

- `web` to `molecule-ranker serve`
- `worker` to `molecule-ranker worker run`
- `cli` to arbitrary `molecule-ranker` commands

## Reverse proxy and systemd

Examples:

- `deployment/nginx.example.conf`
- `deployment/systemd/molecule-ranker.service`
- `deployment/systemd/molecule-ranker-worker.service`

Keep TLS termination, identity provider integration, and network allowlisting in your
organization-managed infrastructure.
