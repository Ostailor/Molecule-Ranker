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
mkdir -p "${DEMO_ROOT}/reports"

uv run molecule-ranker validate graph --root "${DEMO_ROOT}" --fixture golden \
  > "${DEMO_ROOT}/reports/graph_validation.txt"
uv run molecule-ranker validate hypotheses --root "${DEMO_ROOT}" --fixture golden \
  > "${DEMO_ROOT}/reports/hypothesis_validation.txt"
uv run molecule-ranker validate campaign --root "${DEMO_ROOT}" --fixture golden \
  > "${DEMO_ROOT}/reports/campaign_validation.txt"
uv run molecule-ranker validate evaluation --root "${DEMO_ROOT}" \
  > "${DEMO_ROOT}/reports/evaluation_validation.txt"
DEMO_ROOT="${DEMO_ROOT}" \
DEMO_DB_PATH="${DEMO_DB_PATH}" \
MOLECULE_RANKER_AUTH_SECRET="${MOLECULE_RANKER_AUTH_SECRET}" \
uv run python - <<'PY'
import os
from pathlib import Path

from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit
from molecule_ranker.pilot.reports import write_pilot_readiness_report

root = Path(os.environ["DEMO_ROOT"])
config = PilotReadinessConfig.synthetic_dev(
    root_dir=root,
    environment="development",
    database_path=Path(os.environ["DEMO_DB_PATH"]),
    artifact_storage_path=root / ".molecule-ranker" / "artifacts",
    backup_path=root / ".molecule-ranker" / "backups",
    secret_key=os.environ["MOLECULE_RANKER_AUTH_SECRET"],
    release_validation_passed=True,
    security_validation_passed=True,
    guardrail_benchmark_passed=True,
)
report = run_pilot_readiness_audit(config)
output = write_pilot_readiness_report(report, root / "reports" / "pilot_readiness_report.md")
print(output)
if report.failed_count:
    raise SystemExit(1)
PY

cat <<EOF
Synthetic demo workflows complete.

Reports:
  ${DEMO_ROOT}/reports/graph_validation.txt
  ${DEMO_ROOT}/reports/hypothesis_validation.txt
  ${DEMO_ROOT}/reports/campaign_validation.txt
  ${DEMO_ROOT}/reports/evaluation_validation.txt
  ${DEMO_ROOT}/reports/pilot_readiness_report.md
EOF
