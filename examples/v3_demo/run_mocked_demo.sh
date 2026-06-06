#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/expected_outputs/generated_mocked}"
VALIDATION_MODE="${VALIDATION_MODE:-0}"
MOLECULE_RANKER_CMD="${MOLECULE_RANKER_CMD:-molecule-ranker}"

read -r -a MOLECULE_RANKER <<< "$MOLECULE_RANKER_CMD"

mkdir -p "$OUTPUT_DIR"

run_json() {
  local name="$1"
  shift
  echo "v3_demo: $name"
  "$@" > "$OUTPUT_DIR/$name.json"
}

run_json "small_molecule_disease_to_result_bundle" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario v3_full_demo_mocked --json

run_json "generated_small_molecule_hypothesis" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario small_molecule_generation_mocked_e2e --json

run_json "biologics_mocked_workflow" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario biologics_mocked_e2e --json

run_json "integration_dry_run_workflow" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario integration_dry_run_e2e --json

run_json "campaign_copilot_event_workflow" \
  "${MOLECULE_RANKER[@]}" validate autonomy --scenario campaign_copilot_monitoring --json

run_json "guardrail_validation" \
  "${MOLECULE_RANKER[@]}" validate autonomy-boundaries --json

RC_OUTPUT_DIR="$OUTPUT_DIR/v3_rc"
run_json "v3_readiness_and_rc" \
  "${MOLECULE_RANKER[@]}" v3 rc --output-dir "$RC_OUTPUT_DIR" --json

cat > "$OUTPUT_DIR/demo_run_manifest.json" <<JSON
{
  "agent_plan": "See synthetic_inputs/synthetic_project_context.json.",
  "approval_gate": "Generated assets, biologics review, integration mapping, and co-pilot replan are approval-gated.",
  "guardrail_validation": "$OUTPUT_DIR/guardrail_validation.json",
  "mode": "mocked",
  "result_bundle": "$RC_OUTPUT_DIR/v3_rc_result_bundle.zip",
  "synthetic": true,
  "tool_execution": [
    "validate autonomy --scenario v3_full_demo_mocked",
    "validate autonomy --scenario small_molecule_generation_mocked_e2e",
    "validate autonomy --scenario biologics_mocked_e2e",
    "validate autonomy --scenario integration_dry_run_e2e",
    "validate autonomy --scenario campaign_copilot_monitoring",
    "validate autonomy-boundaries",
    "v3 rc"
  ],
  "user_goal": "Run the V3 mocked autonomy demo from synthetic project objective to readiness bundle.",
  "v3_readiness_report": "$RC_OUTPUT_DIR/v3_readiness_report.md",
  "validation_mode": "$VALIDATION_MODE"
}
JSON

echo "V3 mocked demo outputs: $OUTPUT_DIR"

