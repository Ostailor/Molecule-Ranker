# Generated Hypotheses

Generated small-molecule hypotheses are optional V3 outputs. They are disabled
by default and are created only when `--enable-generation` is used.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Run With Generation

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run \
  --enable-generation \
  --output-dir results/parkinson-generated-hypotheses
```

## Artifact

When enabled, V3 writes:

- `generated_candidates.json`

The artifact must label generated molecules as computational hypotheses.

## How To Interpret

Generated hypotheses can support brainstorming, prioritization discussions, and
review queue preparation. They do not claim binding, activity, safety, efficacy,
manufacturability, therapeutic value, or synthetic feasibility.

## Required Review Gate

Generated molecule advancement requires human approval. Advancement without
review is a release-blocking governance failure.

## What Not To Do

Do not treat generated structures or text as evidence. Do not infer assay
results, synthesis routes, dosing, treatment value, or clinical suitability from
generated output.

