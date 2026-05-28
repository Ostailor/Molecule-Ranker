# Ranking Workflow

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## Purpose

The ranking workflow gathers source-backed disease, target, candidate,
literature, developability, assay results, review, active learning,
integrations, Codex, and dashboard artifacts into a ranked research triage
report.

## Running A Workflow

Use the standard CLI or hosted UI for project runs. In offline validation, use:

```bash
molecule-ranker validate golden --workflow existing_molecule_ranking --json
```

For a project workspace:

```bash
molecule-ranker project create --workspace-id example-project --json
molecule-ranker project run results/example-disease-a --run-id example-run
```

## Interpreting Scores

Scores combine configured evidence, data-quality, literature, and
developability signals. They are relative triage scores. A score is not a
clinical claim, not a safety claim, not proof of activity, and not a substitute
for expert review.

## Reviewing Outputs

Check `candidates.json`, `trace.json`, `report.md`, provenance fields, source
record IDs, limitations, and warnings. Reports should clearly separate evidence
from generated hypotheses and Codex assistant text.
