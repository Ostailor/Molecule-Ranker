#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/expected_outputs/generated_readonly_live}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"

mkdir -p "$OUTPUT_DIR"

run_json() {
  local name="$1"
  shift
  echo "v3_demo: $name"
  "$@" > "$OUTPUT_DIR/$name.json"
}

run_json "small_molecule_readonly_e2e" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario small_molecule_readonly_e2e --json

run_json "integration_dry_run_e2e" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario integration_dry_run_e2e --json

run_json "autonomy_boundaries" \
  "${MOLECULE_RANKER[@]}" validate autonomy-boundaries --json

cat > "$OUTPUT_DIR/readonly_live_manifest.json" <<JSON
{
  "external_writes_allowed": false,
  "mode": "read_only_live_and_dry_run_only",
  "readiness_note": "This demo exercises read-only and dry-run validation surfaces only.",
  "synthetic_inputs": "$SCRIPT_DIR/synthetic_inputs",
  "tool_execution": [
    "validate autonomy --scenario small_molecule_readonly_e2e",
    "validate autonomy --scenario integration_dry_run_e2e",
    "validate autonomy-boundaries"
  ]
}
JSON

echo "V3 read-only demo outputs: $OUTPUT_DIR"

