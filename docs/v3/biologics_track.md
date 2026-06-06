# Biologics Track

The V3 biologics track is governed and optional. It supports biologics planning
outputs already present in V2.9 and keeps antibody generation disabled by
default.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Run With Biologics

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run \
  --enable-biologics \
  --output-dir results/parkinson-biologics
```

## Optional Antibody Generation

Generated antibodies require an explicit flag:

```bash
--enable-antibody-generation
```

Use this only when the review plan and governance approvals are ready.

## Artifacts

- `biologic_candidates.json` when biologics is enabled.
- `generated_antibodies.json` when antibody generation is enabled.

## How To Interpret

Biologics outputs are internal research-planning artifacts. Generated antibody
outputs are computational hypotheses, not validated binders, not developable
products, and not therapeutic candidates.

## Required Review Gate

Generated antibody advancement requires human approval. The system blocks Codex
self-approval and treats advancement without review as a governance failure.

## No Wet-Lab Protocols

V3 biologics documentation and outputs must not provide expression,
purification, immunization, wet-lab procedures, dosing, or clinical guidance.

