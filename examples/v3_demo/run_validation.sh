#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/expected_outputs/generated_validation}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

bash -n "$SCRIPT_DIR/run_mocked_demo.sh"
bash -n "$SCRIPT_DIR/run_readonly_live_demo.sh"
bash -n "$SCRIPT_DIR/run_validation.sh"

VALIDATION_MODE=1 OUTPUT_DIR="$OUTPUT_DIR/mocked" MOLECULE_RANKER_CMD="$MOLECULE_RANKER_CMD" \
  "$SCRIPT_DIR/run_mocked_demo.sh"

test -f "$OUTPUT_DIR/mocked/demo_run_manifest.json"
test -f "$OUTPUT_DIR/mocked/v3_rc/v3_readiness_report.md"
test -f "$OUTPUT_DIR/mocked/v3_rc/v3_rc_result_bundle.zip"

echo "V3 demo validation completed: $OUTPUT_DIR"

