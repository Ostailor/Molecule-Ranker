# Active Learning

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Active Learning Does

Active learning suggests which candidates or generated molecules may be useful
to review next based on scores, uncertainty, evidence gaps, developability,
assay results, and configured strategy.

## Running Active Learning

```bash
molecule-ranker experiment active-learning \
  --from-run results/example-disease-a \
  --db-path experiments.sqlite \
  --strategy evidence_gap \
  --batch-size 5 \
  --include-generated \
  --json
```

## How To Interpret Suggestions

Suggestions are triage prompts. They are not lab protocols, not synthesis
instructions, and not evidence that a molecule is useful or safe. Review
warnings, rationale, provenance, and whether any generated molecule lacks exact
imported assay results.

## Using Results

Use active learning output to prioritize review workflow discussion,
integration dry-runs, Codex explanations, and dashboard planning. Keep final
decisions with expert reviewers.
