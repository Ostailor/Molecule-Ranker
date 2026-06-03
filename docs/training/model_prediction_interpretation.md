# Model Prediction Interpretation

Audience: scientists, reviewers, and admins interpreting model prediction
artifacts and model cards.

## Interpretation Boundaries

Model predictions are computational prioritization signals. They are not assay
results, experimental evidence, clinical evidence, safety findings, efficacy
findings, binding proof, dosing guidance, or patient treatment guidance.

## Checklist

- Confirm model card schema and contract version.
- Confirm assay endpoint, calibration status, and applicability-domain notes.
- Confirm prediction artifact is separate from evidence.
- Reject use of uncalibrated predictions for portfolio selection.
- Review uncertainty and limitations before downstream use.

## Exercise: Synthetic Prediction Batch

Synthetic data:

- Model card: `mock_model_card.json`
- Prediction artifact: `mock_prediction_batch.json`
- Candidate: `Synthetic Candidate A`
- Calibration status: `mock_calibrated`

Steps:

1. Open the model card and prediction artifact.
2. Confirm endpoint and calibration metadata.
3. Confirm prediction is not stored as an evidence item.
4. Write a bounded interpretation note.

Expected outcomes:

- Prediction is described as a prioritization signal only.
- Applicability and uncertainty are noted.
- No claim of activity, safety, efficacy, binding, or treatment suitability is
  made.

## Common Mistakes

- Treating predicted activity as measured activity.
- Comparing predictions across incompatible endpoints.
- Ignoring calibration and applicability domain.
- Using uncalibrated predictions to drive portfolio decisions.
