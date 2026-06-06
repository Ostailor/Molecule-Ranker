# Mocked Full Discovery Loop

Runs the V3 `full_discovery_loop` in mocked mode with clearly synthetic inputs.

## Run

```bash
examples/v3_0/mocked_full_discovery_loop/run.sh
```

For validation:

```bash
VALIDATION_MODE=1 OUTPUT_DIR=/tmp/molecule-ranker-v3-mocked examples/v3_0/mocked_full_discovery_loop/run.sh
```

## What The Output Means

The output demonstrates that the V3 one-command workflow can produce a result
bundle, trace, certification, human governance matrix, and related planning
artifacts without external writes.

## What The Output Does Not Prove

It does not prove binding, activity, safety, efficacy, manufacturability,
therapeutic value, clinical validity, or any wet-lab outcome. It provides no
protocol, synthesis instruction, or dosing guidance.
