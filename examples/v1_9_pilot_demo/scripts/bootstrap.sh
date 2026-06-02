#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEMO_DIR}/../.." && pwd)"

DEMO_ROOT="${DEMO_ROOT:-${DEMO_DIR}/.demo_state}"
DEMO_DB_PATH="${DEMO_DB_PATH:-${DEMO_ROOT}/platform.sqlite}"
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL:-pilot-admin@example.invalid}"
DEMO_PORT="${DEMO_PORT:-8765}"
DEMO_HOST="${DEMO_HOST:-127.0.0.1}"

mkdir -p "${DEMO_ROOT}/.molecule-ranker/artifacts"
mkdir -p "${DEMO_ROOT}/.molecule-ranker/backups"
mkdir -p "${DEMO_ROOT}/.molecule-ranker/support-bundles"
mkdir -p "${DEMO_ROOT}/docs/runbooks"

if [[ ! -f "${DEMO_ROOT}/auth_secret.txt" ]]; then
  python - <<'PY' > "${DEMO_ROOT}/auth_secret.txt"
import secrets
print(secrets.token_urlsafe(48))
PY
fi

if [[ ! -f "${DEMO_ROOT}/admin_password.txt" ]]; then
  python - <<'PY' > "${DEMO_ROOT}/admin_password.txt"
import secrets
print(f"Demo-local-{secrets.token_hex(6)}-1A")
PY
fi

for doc in deployment.md deployment_diagnostics.md production_config.md; do
  cat > "${DEMO_ROOT}/docs/runbooks/${doc}" <<'EOF'
# Synthetic Pilot Demo Runbook

This file is generated for the local V1.9 pilot demo readiness check.
It contains no secrets, no real biomedical claims, no lab protocols, no
synthesis instructions, and no dosing or patient-treatment guidance.
EOF
done

cd "${REPO_ROOT}"
uv run molecule-ranker db init \
  --root "${DEMO_ROOT}" \
  --db-path "${DEMO_DB_PATH}"

DEMO_ROOT="${DEMO_ROOT}" \
DEMO_DB_PATH="${DEMO_DB_PATH}" \
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL}" \
DEMO_ADMIN_PASSWORD="$(cat "${DEMO_ROOT}/admin_password.txt")" \
uv run python - <<'PY'
import os
from pathlib import Path

from molecule_ranker.platform.database import PlatformDatabase

root = Path(os.environ["DEMO_ROOT"])
db_path = Path(os.environ["DEMO_DB_PATH"])
email = os.environ["DEMO_ADMIN_EMAIL"]
password = os.environ["DEMO_ADMIN_PASSWORD"]

database = PlatformDatabase(root, db_path=db_path)
existing = next((user for user in database.list_users() if user.email == email), None)
if existing is None:
    user = database.create_user(
        email=email,
        password=password,
        display_name="Pilot Demo Admin",
        roles=["platform_admin", "user"],
    )
    print(f"Created demo admin user: {user.email}")
else:
    print(f"Demo admin user already exists: {existing.email}")
PY

cat > "${DEMO_ROOT}/demo.env" <<EOF
DEMO_ROOT=${DEMO_ROOT}
DEMO_DB_PATH=${DEMO_DB_PATH}
DEMO_ADMIN_EMAIL=${DEMO_ADMIN_EMAIL}
DEMO_ADMIN_PASSWORD_FILE=${DEMO_ROOT}/admin_password.txt
MOLECULE_RANKER_AUTH_SECRET=$(cat "${DEMO_ROOT}/auth_secret.txt")
DEMO_HOST=${DEMO_HOST}
DEMO_PORT=${DEMO_PORT}
EOF

cat <<EOF
V1.9 pilot demo bootstrap complete.

Demo root: ${DEMO_ROOT}
Database: ${DEMO_DB_PATH}
Admin email: ${DEMO_ADMIN_EMAIL}
Admin password file: ${DEMO_ROOT}/admin_password.txt

Start the hosted server with:
  set -a; source "${DEMO_ROOT}/demo.env"; set +a
  uv run molecule-ranker serve --root "\$DEMO_ROOT" --host "\$DEMO_HOST" --port "\$DEMO_PORT" --hosted --auth-secret "\$MOLECULE_RANKER_AUTH_SECRET" --platform-db-path "\$DEMO_DB_PATH"
EOF
