#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"
mkdir -p "$OUTPUT_DIR"

"${MOLECULE_RANKER[@]}" validate autonomy-boundaries \
  --json > "$OUTPUT_DIR/boundary_test_result.json"

cat > "$OUTPUT_DIR/demo_run_manifest.json" <<JSON
{
  "demo_id": "boundary_test_demo",
  "synthetic": true,
  "external_writes_expected": false,
  "output_meaning": "V3 autonomy boundary checks executed for the current software build.",
  "output_does_not_prove": "No clinical validity, biomedical outcome, protocol, synthesis, or dosing guidance."
}
JSON

echo "V3.0 boundary test demo outputs: $OUTPUT_DIR"
