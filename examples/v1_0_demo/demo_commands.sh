#!/usr/bin/env bash
set -euo pipefail

# Synthetic V1.0 demo only. These commands use fake Example* entities and
# deterministic mocked/offline validation surfaces by default.

DEMO_DIR="${DEMO_DIR:-examples/v1_0_demo}"
WORK_DIR="${WORK_DIR:-.molecule-ranker/v1_0_demo}"
RUN_DIR="${RUN_DIR:-$WORK_DIR/validation/golden/existing_molecule_ranking}"
DB_PATH="${DB_PATH:-$WORK_DIR/platform.sqlite}"
REVIEW_DB_PATH="${REVIEW_DB_PATH:-$WORK_DIR/review.sqlite}"
EXPERIMENT_DB_PATH="${EXPERIMENT_DB_PATH:-$WORK_DIR/experiments.sqlite}"

mkdir -p "$WORK_DIR"

molecule-ranker project create --root "$WORK_DIR" --workspace-id "v1-demo-synthetic" --name "Synthetic V1.0 Demo" --json
molecule-ranker validate golden --workflow existing_molecule_ranking --root "$WORK_DIR" --json
molecule-ranker validate golden --workflow generation_workflow --root "$WORK_DIR" --json
molecule-ranker assess-developability --input "$RUN_DIR/candidates.json" --output "$RUN_DIR/developability.json" --json
molecule-ranker project run "$RUN_DIR" --root "$WORK_DIR" --run-id "synthetic-existing-ranking"
molecule-ranker review create --from-run "$RUN_DIR" --db-path "$REVIEW_DB_PATH" --reviewer-id "example-reviewer" --reviewer-name "Example Reviewer" --reviewer-role "scientist" --json
molecule-ranker experiment import "$DEMO_DIR/synthetic_assay_results.csv" --db-path "$EXPERIMENT_DB_PATH" --dry-run --default-disease "ExampleDiseaseA" --default-target "ExampleTargetA" --json
molecule-ranker experiment link --from-run "$RUN_DIR" --db-path "$EXPERIMENT_DB_PATH" --json
molecule-ranker experiment active-learning --from-run "$RUN_DIR" --db-path "$EXPERIMENT_DB_PATH" --strategy evidence_gap --batch-size 2 --include-generated --json
molecule-ranker integration system create --name "ExampleExternalSystemA" --system-type "generic_rest" --vendor "synthetic" --base-url "https://example.invalid/synthetic" --mode dry_run --root "$WORK_DIR" --db-path "$DB_PATH" --json
molecule-ranker integration sync run --external-system-id "ext-exampleexternalsystema" --direction import --object-type assay_results --project-id "v1-demo-synthetic" --dry-run --root "$WORK_DIR" --db-path "$DB_PATH" --json
molecule-ranker project summarize --root "$WORK_DIR" --use-codex --mode dry_run --json
molecule-ranker platform backup --root "$WORK_DIR" --db-path "$DB_PATH" --output "$WORK_DIR/synthetic-demo-export.zip" --json
molecule-ranker project dashboard --root "$WORK_DIR" --output-dir "$WORK_DIR/dashboard"
