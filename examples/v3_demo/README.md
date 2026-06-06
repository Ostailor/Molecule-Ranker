# molecule-ranker V3 Demo

This demo exercises the V3.0 autonomy validation surfaces for molecule-ranker
as an autonomous discovery operating system. It is a software and autonomy
readiness demo only.
It is not clinical validation, scientific validation, medical advice, or lab
guidance.

## Scope

The mocked demo covers:

- Small molecule disease-to-result-bundle validation.
- Generated small-molecule computational hypothesis validation.
- Biologics mocked workflow validation with antibody generation disabled.
- Integration dry-run validation with no external writes.
- Campaign co-pilot event validation with approval-gated replan behavior.
- V3 readiness report and release-candidate bundle generation.

The read-only live demo exercises read-only validation surfaces only. It does
not require or perform external writes.

## Safety Boundaries

All checked-in inputs are synthetic. The demo does not include real assay
results, real external records, real citations, wet-lab protocols, synthesis
instructions, dosing guidance, or generated antibody claims. Generated molecules
and generated antibodies, when represented by workflow metadata, remain
computational hypotheses.

## Files

- `synthetic_inputs/demo_user_goals.json`: user goals and expected governance
  posture for each demo workflow.
- `synthetic_inputs/synthetic_project_context.json`: synthetic project context,
  agent plan, approval gates, and tool-execution expectations.
- `synthetic_inputs/copilot_event.json`: synthetic co-pilot event fixture.
- `expected_outputs/expected_artifacts_manifest.json`: expected artifact classes.
- `expected_outputs/guardrail_expectations.json`: guardrail assertions.
- `run_mocked_demo.sh`: executes the mocked demo and writes JSON artifacts.
- `run_readonly_live_demo.sh`: executes read-only validation surfaces.
- `run_validation.sh`: validates scripts and runs the mocked demo in validation
  mode.

## Run

From the repository root:

```bash
examples/v3_demo/run_mocked_demo.sh
```

For CI or local validation with an isolated output directory:

```bash
VALIDATION_MODE=1 OUTPUT_DIR=/tmp/molecule-ranker-v3-demo examples/v3_demo/run_mocked_demo.sh
```

To validate the demo package:

```bash
examples/v3_demo/run_validation.sh
```

If the `molecule-ranker` console script is not on `PATH`, set:

```bash
MOLECULE_RANKER_CMD="uv run molecule-ranker" examples/v3_demo/run_validation.sh
```
