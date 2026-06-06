#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"
mkdir -p "$OUTPUT_DIR"

"${MOLECULE_RANKER[@]}" validate autonomy \
  --scenario campaign_copilot_monitoring \
  --json > "$OUTPUT_DIR/campaign_copilot_event_workflow.json"

cat > "$OUTPUT_DIR/demo_run_manifest.json" <<JSON
{
  "demo_id": "campaign_copilot_event_loop",
  "synthetic": true,
  "external_writes_expected": false,
  "output_meaning": "Synthetic co-pilot event validation under V3 controls.",
  "output_does_not_prove": "No campaign activation, clinical validity, protocol, synthesis, or dosing guidance."
}
JSON

echo "V3.0 campaign co-pilot event loop outputs: $OUTPUT_DIR"
