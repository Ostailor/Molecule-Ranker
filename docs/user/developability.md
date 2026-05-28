# Developability

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Developability Does

Developability triage flags computational and rule-based concerns that may make
a candidate harder to prioritize. It can consider descriptors, alerts,
structure availability, and configured filters.

## How To Interpret Developability

Developability output is not proof of safety, not proof of efficacy, and not a
substitute for expert assessment. A low-risk label means fewer configured
triage concerns were found. A high-risk label means review the warnings before
using the score.

## Workflow

```bash
molecule-ranker assess-developability \
  --input results/example-disease-a/candidates.json \
  --output results/example-disease-a/developability.json \
  --json
```

## Review Guidance

Compare developability with literature evidence, assay results, generated
molecules, review notes, active learning priorities, integrations provenance,
Codex summaries, and dashboard warnings. Treat disagreements as review items.
