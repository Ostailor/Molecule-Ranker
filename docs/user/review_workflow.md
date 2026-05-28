# Review Workflow

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Review Does

The review workflow creates expert workspaces from run artifacts. Reviewers can
inspect candidates, generated molecules, developability, literature evidence,
assay results, active learning suggestions, integrations provenance, Codex
assistant output, and dashboard summaries.

## Creating A Review Workspace

```bash
molecule-ranker review create \
  --from-run results/example-disease-a \
  --db-path review.sqlite \
  --reviewer-id reviewer-example \
  --reviewer-name "Example Reviewer" \
  --reviewer-role scientist \
  --json
```

## Reviewing Candidates

Use decisions and comments to capture expert judgment. A review decision is not
biomedical evidence. Keep rationale grounded in artifacts and limitations.

## Outputs

Review can produce queue records, comments, decisions, dossiers, handoffs, and
dashboards. These support internal triage and should not be presented as
clinical claims.
