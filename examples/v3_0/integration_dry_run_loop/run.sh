#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/output}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"
mkdir -p "$OUTPUT_DIR"

"${MOLECULE_RANKER[@]}" discover \
  --disease "Synthetic V3 integration planning condition" \
  --mode dry_run \
  --enable-integrations \
  --output-dir "$OUTPUT_DIR" \
  --json > "$OUTPUT_DIR/command_result.json"

echo "V3.0 integration dry-run loop outputs: $OUTPUT_DIR"
