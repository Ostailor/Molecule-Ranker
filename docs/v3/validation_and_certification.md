# Validation And Certification

V3 validation and certification are software, workflow, autonomy, governance,
and reproducibility checks. They are not clinical validation.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Composite Validation

```bash
molecule-ranker validate v3 \
  --mode mocked \
  --output-dir .molecule-ranker/validation/v3
```

This writes:

- `v3_validation_report.json`
- `v3_validation_report.md`

## Result Certification

Each V3 result bundle includes result certification:

- `v3_result_certification.json`
- `v3_result_certification.md`

Certification checks product contract inclusion, workflow contract validity,
required artifacts, lineage, guardrails, approvals, external writes, output
separation, generated-output labeling, evidence import rules, failed-QC
handling, reproducibility, and safety-case linkage.

## Release Gate

```bash
molecule-ranker v3 release-gate \
  --output-dir .molecule-ranker/v3_release_gate
```

This writes:

- `v3_release_gate.json`
- `v3_release_gate.md`

The release gate checks version, lock files, static validation markers, product
contracts, bundle contracts, mocked discovery, V3 validation, readiness, safety
case, residual risk register, support redaction, docs, demos, result
certification, and autonomy boundaries.

## What Certification Means

Certification means the platform controls passed. It does not mean a molecule,
biologic, hypothesis, model prediction, graph inference, docking score, or
Codex statement is scientifically true or clinically valid.

