# Pilot Setup

## Goal

Prepare a V1.9 hosted pilot workspace for the first internal team with auth,
RBAC, artifact storage, readiness checks, and support workflows enabled.

## Prerequisites

- V1.9.0 package installed.
- Platform database path or database URL selected.
- Writable artifact, backup, report, and support-bundle directories.
- Admin email and password supplied from the approved local setup channel.
- No live external connectors enabled until reviewed.

## Configure Local Auth

Use hosted mode for team pilots. For local setup, create the platform database
and one admin user before starting the server.

```bash
export PILOT_ROOT="$PWD/.pilot"
export PILOT_DB="$PILOT_ROOT/platform.sqlite"
export PILOT_AUTH_SECRET="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"

mkdir -p "$PILOT_ROOT"
uv run molecule-ranker db init --root "$PILOT_ROOT" --db-path "$PILOT_DB"
```

Create the first admin with a local setup script or an approved internal admin
bootstrap path. Do not paste credentials into source files, tickets, or shared
logs.

Example local admin bootstrap:

```bash
export PILOT_ADMIN_EMAIL="pilot-admin@example.invalid"
read -rsp "Pilot admin password: " PILOT_ADMIN_PASSWORD
echo

uv run python - <<'PY'
import os
from pathlib import Path

from molecule_ranker.platform.database import PlatformDatabase

database = PlatformDatabase(Path(os.environ["PILOT_ROOT"]), db_path=Path(os.environ["PILOT_DB"]))
existing = next(
    (user for user in database.list_users() if user.email == os.environ["PILOT_ADMIN_EMAIL"]),
    None,
)
if existing is None:
    database.create_user(
        email=os.environ["PILOT_ADMIN_EMAIL"],
        password=os.environ["PILOT_ADMIN_PASSWORD"],
        display_name="Pilot Admin",
        roles=["platform_admin", "user"],
    )
PY

unset PILOT_ADMIN_PASSWORD
```

## Start Hosted Server

```bash
uv run molecule-ranker serve \
  --root "$PILOT_ROOT" \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --auth-secret "$PILOT_AUTH_SECRET" \
  --platform-db-path "$PILOT_DB"
```

Open `http://127.0.0.1:8765/dashboard`.

## Docker Compose

Use the V1.9 demo compose file for a synthetic local hosted stack:

```bash
docker compose -f examples/v1_9_pilot_demo/docker-compose.yml up
```

The compose demo stores generated runtime files under
`examples/v1_9_pilot_demo/.demo_state/`.

## Verify Setup

```bash
uv run molecule-ranker pilot readiness \
  --root "$PILOT_ROOT" \
  --db-path "$PILOT_DB" \
  --environment development \
  --output "$PILOT_ROOT/pilot_readiness_report.md"
```

Resolve failed checks before inviting pilot users.
