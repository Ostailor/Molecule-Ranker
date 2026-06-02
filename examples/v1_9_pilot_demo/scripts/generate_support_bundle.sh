#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEMO_DIR}/../.." && pwd)"
DEMO_ROOT="${DEMO_ROOT:-${DEMO_DIR}/.demo_state}"

"${SCRIPT_DIR}/seed_demo_project.sh"

set -a
source "${DEMO_ROOT}/demo.env"
set +a

cd "${REPO_ROOT}"
mkdir -p "${DEMO_ROOT}/support"

uv run molecule-ranker support bundle \
  --root "${DEMO_ROOT}" \
  --output "${DEMO_ROOT}/support/v1_9_pilot_demo_support_bundle.zip"

echo "Support bundle: ${DEMO_ROOT}/support/v1_9_pilot_demo_support_bundle.zip"
