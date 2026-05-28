# Dashboard

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What The Dashboard Shows

The dashboard summarizes project runs, scores, generated molecules,
developability, literature evidence, assay results, review workflow state,
active learning suggestions, integrations status, Codex outputs, and artifact
links.

## Building A Static Dashboard

```bash
molecule-ranker project dashboard \
  --root .molecule-ranker/v1_0_demo \
  --output-dir .molecule-ranker/v1_0_demo/dashboard
```

## How To Read It

Use dashboard views for navigation and triage. Always inspect underlying
artifacts before acting on a score, warning, generated molecule, or Codex
summary.

## Safety Boundaries

Dashboard text should preserve limitations and separate evidence, generated
hypotheses, review decisions, and imported assay results. It must not imply
clinical claims or validated outcomes.
