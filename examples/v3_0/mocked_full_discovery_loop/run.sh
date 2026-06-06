#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output}"
VALIDATION_MODE="${VALIDATION_MODE:-0}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"
mkdir -p "$OUTPUT_DIR"

"${MOLECULE_RANKER[@]}" discover \
  --disease "Synthetic V3 planning condition" \
  --mode mocked \
  --output-dir "$OUTPUT_DIR" \
  --json > "$OUTPUT_DIR/command_result.json"

cat > "$OUTPUT_DIR/demo_run_manifest.json" <<JSON
{
  "demo_id": "mocked_full_discovery_loop",
  "mode": "mocked",
  "synthetic": true,
  "validation_mode": "$VALIDATION_MODE",
  "external_writes_expected": false,
  "output_meaning": "Governed V3 research-planning bundle generation in mocked mode.",
  "output_does_not_prove": "No clinical validity, wet-lab outcome, protocol, synthesis, or dosing guidance."
}
JSON

echo "V3.0 mocked full discovery loop outputs: $OUTPUT_DIR"
