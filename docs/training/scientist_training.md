# Scientist Training

Audience: internal research scientists using V2.0 ranking, generated
hypotheses, developability, models, structure workflows, portfolios, graphs,
hypotheses, campaigns, and evaluations.

## Interpretation Boundaries

- Generated molecules are computational hypotheses only.
- Model predictions are not assay results or experimental evidence.
- Docking scores and poses are not proof of binding.
- Evaluation and benchmark reports are software/research artifacts, not
  clinical validation.
- The platform does not provide medical advice, lab protocols, synthesis
  instructions, dosing, or patient treatment guidance.

## Checklist

- Verify project permission and data provenance.
- Confirm generated molecule labels are visible.
- Separate imported evidence from predictions and summaries.
- Route generated molecules through review before export.
- Treat Codex summaries as summaries of permitted artifacts only.
- Escalate overclaims or missing provenance to reviewers.

## Exercise: Synthetic Ranking Review

Synthetic data:

- Project: `demo-project-alpha`
- Candidate: `Synthetic Candidate A`
- Generated candidate: `Generated Hypothesis G-001`
- Imported artifact: `synthetic_source_summary.json`
- Model artifact: `mock_prediction_batch.json`
- Structure artifact: `null_docking_report.json`

Steps:

1. Open the project ranking view.
2. Identify which candidate is generated.
3. Compare source-backed evidence and mocked model output.
4. Confirm the null docking report is separate from evidence.
5. Send `Generated Hypothesis G-001` to review before export.

Expected outcomes:

- Generated and existing candidates remain separated.
- Mocked model output is not cited as evidence.
- Null docking output is not treated as binding proof.
- Review item is created with rationale and boundaries.

## Common Mistakes

- Calling a generated molecule active because it ranks highly.
- Treating a model score as direct evidence.
- Treating a docking result as proof.
- Exporting generated molecules before review.
