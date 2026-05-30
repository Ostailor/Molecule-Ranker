# Developability

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Developability Does

Developability triage flags computational and rule-based concerns that may make
a candidate harder to prioritize. It can consider descriptors, alerts,
structure availability, optional structure workflow artifacts, and configured
filters.

## Structure Workflows

V1.3 structure workflows are optional. Target structure selection is auditable
and prefers suitable experimental structures over predicted structures. Receptor
preparation, ligand 3D preparation, binding-site selection, docking,
pose-quality checks, consensus rescoring, and protein-ligand interaction
profiles are recorded as computational artifacts only.

Docking scores are not proof of binding. A pose is not experimental evidence.
Structure-based scores are not activity evidence. Predicted structures are
lower-confidence than suitable experimental structures. Generated molecules
remain computational hypotheses.

## How To Interpret Developability

Developability output is not proof of safety, not proof of efficacy, and not a
substitute for expert assessment. Structure outputs do not establish binding,
inhibition, activation, activity, treatment value, safety, or efficacy. A
low-risk label means fewer configured triage concerns were found. A high-risk
label means review the warnings before using the score.

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
