# V3 Quickstart

Use this guide to run a governed V3 workflow with safe defaults.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## 1. Confirm Version

```bash
molecule-ranker --version
```

Expected version:

```text
3.0.0
```

## 2. Run Mocked Discovery

Start with mocked mode before using live read-only data.

```bash
molecule-ranker discover \
  --disease "Synthetic neurodegeneration fixture" \
  --mode mocked \
  --output-dir results/v3-mocked-demo
```

Mocked mode uses deterministic synthetic fixtures. It is useful for validating
workflow mechanics, artifacts, governance checkpoints, and certification.

## 3. Run Dry-Run Discovery

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run \
  --output-dir results/parkinson-dry-run
```

Dry-run mode keeps external writes disabled and does not activate campaigns.

## 4. Optional Features

Enable optional outputs only when the review plan is ready:

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run \
  --enable-generation \
  --enable-biologics \
  --output-dir results/parkinson-v3-options
```

Generated molecules and generated antibodies are computational hypotheses.
They require human review and independent validation before any advancement.

## 5. Validate V3

```bash
molecule-ranker validate v3 \
  --mode mocked \
  --output-dir .molecule-ranker/validation/v3
```

## 6. Run Release Gate

```bash
molecule-ranker v3 release-gate \
  --output-dir .molecule-ranker/v3_release_gate
```

The release gate certifies software and autonomy readiness. It does not certify
biomedical truth or clinical validity.

