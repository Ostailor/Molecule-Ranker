#!/bin/sh
set -eu

ROOT_DIR="${MOLECULE_RANKER_ROOT:-/data/projects}"
HOST="${MOLECULE_RANKER_HOST:-127.0.0.1}"
PORT="${MOLECULE_RANKER_PORT:-8765}"
DB_PATH="${MOLECULE_RANKER_PLATFORM_DB_PATH:-/data/storage/platform.sqlite}"
ALLOW_PUBLIC_BIND="${MOLECULE_RANKER_ALLOW_PUBLIC_BIND:-false}"

mkdir -p "$ROOT_DIR" "$(dirname "$DB_PATH")" "${MOLECULE_RANKER_ARTIFACT_ROOT:-/data/artifacts}" "${MOLECULE_RANKER_STORAGE_ROOT:-/data/storage}"

if [ -n "${MOLECULE_RANKER_DATABASE_URL:-}" ]; then
  molecule-ranker-wait-for-db
  molecule-ranker db migrate --root "$ROOT_DIR" --database-url "$MOLECULE_RANKER_DATABASE_URL"
else
  molecule-ranker db migrate --root "$ROOT_DIR" --db-path "$DB_PATH"
fi

case "${1:-web}" in
  web)
    shift || true
    if [ -z "${MOLECULE_RANKER_AUTH_SECRET:-}" ]; then
      echo "MOLECULE_RANKER_AUTH_SECRET must be set for hosted web mode." >&2
      exit 1
    fi
    if [ -n "${MOLECULE_RANKER_DATABASE_URL:-}" ]; then
      set -- molecule-ranker serve \
        --root "$ROOT_DIR" \
        --host "$HOST" \
        --port "$PORT" \
        --hosted \
        --auth-secret "${MOLECULE_RANKER_AUTH_SECRET:-}" \
        --platform-database-url "$MOLECULE_RANKER_DATABASE_URL" \
        "$@"
    else
      set -- molecule-ranker serve \
        --root "$ROOT_DIR" \
        --host "$HOST" \
        --port "$PORT" \
        --hosted \
        --auth-secret "${MOLECULE_RANKER_AUTH_SECRET:-}" \
        --platform-db-path "$DB_PATH" \
        "$@"
    fi
    if [ "$ALLOW_PUBLIC_BIND" = "true" ] || [ "$ALLOW_PUBLIC_BIND" = "1" ]; then
      set -- "$@" --allow-public-bind
    fi
    ;;
  worker)
    shift || true
    if [ -n "${MOLECULE_RANKER_DATABASE_URL:-}" ]; then
      set -- molecule-ranker worker run --root "$ROOT_DIR" --database-url "$MOLECULE_RANKER_DATABASE_URL" "$@"
    else
      set -- molecule-ranker worker run --root "$ROOT_DIR" --db-path "$DB_PATH" "$@"
    fi
    ;;
  cli)
    shift || true
    set -- molecule-ranker "$@"
    ;;
esac

exec "$@"
