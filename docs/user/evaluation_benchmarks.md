# Evaluation Benchmarks

V1.8 adds scientific evaluation benchmark suites and prospective validation
analytics for internal research operations.

These outputs answer operational questions:

- Are rankings improving over time?
- Are generated molecules becoming more experiment-worthy?
- Are surrogate models calibrated and useful prospectively?
- Are portfolio selections better than baselines?
- Are campaigns producing useful learning under budget?
- Are hypotheses being supported, contradicted, or retired efficiently?
- Are Codex-assisted summaries safe, grounded, and useful?
- Are guardrails preventing overclaims and unsafe outputs?
- Are integrations preserving provenance and data quality?
- Are decisions reproducible across versions?

## Artifact Boundary

Benchmark results are evaluation artifacts, not biomedical evidence.
Prospective validation analytics are not clinical validation. Reports do not
prove that molecules are active, safe, effective, synthesizable, or suitable for
any patient or treatment use.

Experimental labels may only come from imported assay results or explicitly
supplied benchmark fixtures. Prospective predictions must be frozen before
outcome labels are imported. Codex-assisted summaries may describe computed
evaluation artifacts, but they must not invent benchmark results, assay labels,
metrics, or conclusions.

## Core Objects

- `BenchmarkSuite`: versioned group of evaluation tasks.
- `BenchmarkTask`: one objective with artifact IDs for inputs, labels, and metrics.
- `BenchmarkDataset`: dataset-level counts, source artifact IDs, and data contract version.
- `BenchmarkSplit`: train, validation, and test IDs with leakage-check metadata.
- `FrozenPredictionSet`: prediction artifact metadata captured before outcome labels arrive.
- `ProspectiveValidationRun`: frozen predictions paired with later imported
  outcome imports.
- `EvaluationMetric`: deterministic metric value plus whether higher values are better.
- `EvaluationReport`: metrics, baseline metrics, comparisons, warnings, and limitations.
- `DecisionQualityReport`: decision artifacts, outcome artifacts, lessons, and metrics.
- `ReproducibilityManifest`: code version, artifact contract version, config hash,
  artifact hashes, random seeds, and dependency summary.

## CLI

Run the deterministic synthetic V1.8 fixture:

```bash
uv run molecule-ranker validate evaluation --json
```

The fixture writes:

- `.molecule-ranker/validation/evaluation/evaluation_report.json`
- `.molecule-ranker/validation/evaluation/evaluation_report.md`

Synthetic fixture results are for release validation only.
