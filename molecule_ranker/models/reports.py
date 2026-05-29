"""Model card and report helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEvaluationReport,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)

MODEL_REPORT_DISCLAIMERS = [
    "Predictions are not experimental evidence.",
    "Predictions are not clinical claims.",
    "Generated molecules remain computational hypotheses.",
    "This model is endpoint-specific and assay-context-specific.",
    "Insufficient data limits confidence when training, calibration, or evaluation data are small.",
]


def _training_model_type(
    training_run: ModelTrainingRun,
    model_card: ModelCard | None,
) -> str:
    if model_card is not None:
        return model_card.model_type
    return str(training_run.metrics.get("model_type", ""))


def render_model_training_report(
    *,
    dataset: ModelTrainingDataset,
    training_run: ModelTrainingRun,
    model_card: ModelCard | None = None,
    split_result: Any | None = None,
) -> str:
    """Render a markdown training report grounded in model artifacts."""

    leakage_checks = _leakage_checks(training_run=training_run, split_result=split_result)
    split_strategy = _split_strategy(training_run=training_run, split_result=split_result)
    return "\n".join(
        [
            "# Model Training Report",
            "",
            *_disclaimer_block(),
            "## Endpoint and Assay Context",
            _endpoint_context(dataset, model_card),
            "",
            "## Dataset Provenance",
            _dataset_provenance(dataset),
            "",
            "## Included Results",
            _bullet_list(dataset.source_result_ids),
            "",
            "## Excluded Results",
            _excluded_results(dataset),
            "",
            "## Split Strategy",
            str(split_strategy),
            "",
            "## Leakage Checks",
            _json_block(leakage_checks),
            "",
            "## Model Type",
            _training_model_type(training_run, model_card),
            "",
            "## Metrics",
            _json_block(training_run.metrics),
            "",
            "## Calibration",
            _json_block(training_run.calibration_metrics),
            "",
            "## Applicability Domain",
            _applicability_domain_method(model_card),
            "",
            "## Limitations",
            _limitations(model_card, training_run.warnings),
            "",
        ]
    )


def render_model_evaluation_report(
    *,
    evaluation_report: ModelEvaluationReport,
    model_card: ModelCard | None = None,
    dataset: ModelTrainingDataset | None = None,
) -> str:
    """Render a markdown evaluation report grounded in evaluation artifacts."""

    return "\n".join(
        [
            "# Model Evaluation Report",
            "",
            *_disclaimer_block(),
            "## Endpoint and Assay Context",
            _endpoint_context(dataset, model_card),
            "",
            "## Dataset Provenance",
            _evaluation_dataset_provenance(dataset, evaluation_report),
            "",
            "## Split Strategy",
            evaluation_report.split_strategy,
            "",
            "## Leakage Checks",
            _json_block(evaluation_report.leakage_checks),
            "",
            "## Model Type",
            str(model_card.model_type if model_card is not None else evaluation_report.model_id),
            "",
            "## Metrics",
            _json_block(evaluation_report.metrics),
            "",
            "## Calibration",
            _json_block(evaluation_report.calibration_metrics),
            "",
            "## Applicability Domain",
            _json_block(evaluation_report.applicability_domain_summary),
            "",
            "## Limitations",
            _limitations(model_card, evaluation_report.warnings),
            "",
        ]
    )


def render_model_prediction_report(
    *,
    model_card: ModelCard,
    predictions: Sequence[ModelPrediction],
    dataset: ModelTrainingDataset | None = None,
    training_run: ModelTrainingRun | None = None,
    evaluation_report: ModelEvaluationReport | None = None,
    prediction_batch_artifact_id: str | None = None,
) -> str:
    """Render a markdown prediction report without turning predictions into evidence."""

    out_of_domain = _out_of_domain_predictions(predictions)
    generated = [
        prediction for prediction in predictions if prediction.candidate_origin == "generated"
    ]
    return "\n".join(
        [
            "# Model Prediction Report",
            "",
            *_disclaimer_block(),
            "## Endpoint and Assay Context",
            _endpoint_context(dataset, model_card),
            "",
            "## Dataset Provenance",
            _prediction_provenance(
                model_card,
                dataset,
                training_run,
                evaluation_report,
                prediction_batch_artifact_id,
            ),
            "",
            "## Model Type",
            model_card.model_type,
            "",
            "## Metrics",
            _json_block(model_card.metrics),
            "",
            "## Calibration",
            _json_block(model_card.calibration_metrics),
            "",
            "## Applicability Domain",
            model_card.applicability_domain_method,
            "",
            "## Prediction Summary",
            _prediction_summary(predictions),
            "",
            "## Out-of-Domain Predictions",
            _prediction_table(out_of_domain),
            "",
            "## Generated Molecule Prediction Warnings",
            _generated_prediction_warnings(generated),
            "",
            "## Limitations",
            _limitations(model_card, []),
            "",
        ]
    )


def write_model_report_artifacts(
    *,
    output_dir: str | Path,
    dataset: ModelTrainingDataset,
    training_run: ModelTrainingRun,
    model_card: ModelCard,
    predictions: Sequence[ModelPrediction],
    split_result: Any | None = None,
    evaluation_report: ModelEvaluationReport | None = None,
    prediction_batch_artifact_id: str | None = None,
) -> dict[str, Path]:
    """Write the standard V1.2 model report artifact set."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    model_card_path = output_path / "model_card.json"
    predictions_path = output_path / "model_predictions.json"
    training_report_path = output_path / "model_training_report.md"
    evaluation_report_path = output_path / "model_evaluation_report.md"
    prediction_report_path = output_path / "model_prediction_report.md"

    model_card_path.write_text(model_card.model_dump_json(indent=2) + "\n")
    predictions_path.write_text(
        json.dumps(
            {
                "artifact_type": "ModelPredictionArtifact",
                "model_id": model_card.model_id,
                "endpoint_id": model_card.endpoint.endpoint_id,
                "prediction_batch_artifact_id": prediction_batch_artifact_id,
                "predictions": [prediction.model_dump(mode="json") for prediction in predictions],
                "warnings": MODEL_REPORT_DISCLAIMERS,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    training_report_path.write_text(
        render_model_training_report(
            dataset=dataset,
            training_run=training_run,
            model_card=model_card,
            split_result=split_result,
        )
    )
    if evaluation_report is None:
        evaluation_text = render_model_evaluation_report(
            evaluation_report=ModelEvaluationReport(
                evaluation_id="not_recorded",
                model_id=model_card.model_id,
                dataset_id=dataset.dataset_id,
                split_strategy=_split_strategy(
                    training_run=training_run,
                    split_result=split_result,
                ),
                metrics=training_run.metrics,
                calibration_metrics=training_run.calibration_metrics,
                leakage_checks=dict(
                    _leakage_checks(
                        training_run=training_run,
                        split_result=split_result,
                    )
                ),
                applicability_domain_summary={"method": model_card.applicability_domain_method},
                warnings=training_run.warnings,
            ),
            model_card=model_card,
            dataset=dataset,
        )
    else:
        evaluation_text = render_model_evaluation_report(
            evaluation_report=evaluation_report,
            model_card=model_card,
            dataset=dataset,
        )
    evaluation_report_path.write_text(evaluation_text)
    prediction_report_path.write_text(
        render_model_prediction_report(
            model_card=model_card,
            predictions=predictions,
            dataset=dataset,
            training_run=training_run,
            evaluation_report=evaluation_report,
            prediction_batch_artifact_id=prediction_batch_artifact_id,
        )
    )
    return {
        "model_card_json": model_card_path,
        "model_predictions_json": predictions_path,
        "model_training_report": training_report_path,
        "model_evaluation_report": evaluation_report_path,
        "model_prediction_report": prediction_report_path,
    }


def _disclaimer_block() -> list[str]:
    return [
        "## Disclaimers",
        *_bullet_lines(MODEL_REPORT_DISCLAIMERS),
        "",
    ]


def _endpoint_context(
    dataset: ModelTrainingDataset | None,
    model_card: ModelCard | None,
) -> str:
    if dataset is not None:
        endpoint = dataset.endpoint
    elif model_card is not None:
        endpoint = model_card.endpoint
    else:
        endpoint = None
    if endpoint is None:
        return "Endpoint context was not recorded."
    return "\n".join(
        [
            f"- Endpoint ID: {endpoint.endpoint_id}",
            f"- Endpoint name: {endpoint.endpoint_name}",
            f"- Endpoint category: {endpoint.endpoint_category}",
            f"- Target symbol: {endpoint.target_symbol or 'not recorded'}",
            f"- Disease name: {endpoint.disease_name or 'not recorded'}",
            f"- Assay type: {endpoint.assay_type or 'not recorded'}",
            f"- Unit: {endpoint.unit or 'not recorded'}",
            f"- Label type: {endpoint.label_type}",
            f"- Directionality: {endpoint.directionality}",
            "- Endpoint specificity: model is endpoint-specific and context-specific.",
        ]
    )


def _dataset_provenance(dataset: ModelTrainingDataset | None) -> str:
    if dataset is None:
        return "Dataset provenance was not included in this artifact bundle."
    positive_count = (
        str(dataset.positive_count) if dataset.positive_count is not None else "not applicable"
    )
    negative_count = (
        str(dataset.negative_count) if dataset.negative_count is not None else "not applicable"
    )
    return "\n".join(
        [
            f"- Dataset ID: {dataset.dataset_id}",
            f"- Created at: {dataset.created_at.isoformat()}",
            f"- Row count: {dataset.row_count}",
            f"- Positive count: {positive_count}",
            f"- Negative count: {negative_count}",
            f"- Source result IDs: {', '.join(dataset.source_result_ids) or 'none'}",
            f"- Included candidate IDs: {', '.join(dataset.included_candidate_ids) or 'none'}",
            f"- Feature matrix URI: {dataset.feature_matrix_uri or 'not recorded'}",
            f"- Labels URI: {dataset.labels_uri or 'not recorded'}",
        ]
    )


def _prediction_provenance(
    model_card: ModelCard,
    dataset: ModelTrainingDataset | None,
    training_run: ModelTrainingRun | None,
    evaluation_report: ModelEvaluationReport | None,
    prediction_batch_artifact_id: str | None,
) -> str:
    dataset_id = dataset.dataset_id if dataset is not None else model_card.training_dataset_id
    training_run_id = (
        training_run.training_run_id if training_run is not None else "not recorded"
    )
    evaluation_id = (
        evaluation_report.evaluation_id if evaluation_report is not None else "not recorded"
    )
    rows = [
        f"- Model ID: {model_card.model_id}",
        f"- Dataset ID: {dataset_id}",
        f"- Training run ID: {training_run_id}",
        f"- Evaluation ID: {evaluation_id}",
        f"- Prediction batch artifact ID: {prediction_batch_artifact_id or 'not recorded'}",
    ]
    if dataset is not None:
        rows.append(f"- Source result IDs: {', '.join(dataset.source_result_ids) or 'none'}")
    return "\n".join(rows)


def _excluded_results(dataset: ModelTrainingDataset) -> str:
    if not dataset.excluded_result_ids:
        return "- No excluded result IDs recorded."
    return "\n".join(
        f"- {result_id}: {dataset.exclusion_reasons.get(result_id, 'reason not recorded')}"
        for result_id in dataset.excluded_result_ids
    )


def _leakage_checks(
    *,
    training_run: ModelTrainingRun,
    split_result: Any | None,
) -> Mapping[str, Any]:
    if split_result is not None and hasattr(split_result, "leakage_check_report"):
        return dict(split_result.leakage_check_report)
    value = training_run.metadata.get("leakage_check_report")
    return value if isinstance(value, Mapping) else {"passed": "not recorded"}


def _split_strategy(
    *,
    training_run: ModelTrainingRun,
    split_result: Any | None,
) -> str:
    if split_result is not None and hasattr(split_result, "strategy"):
        return str(split_result.strategy)
    return str(training_run.metadata.get("split_strategy") or "not recorded")


def _applicability_domain_method(model_card: ModelCard | None) -> str:
    if model_card is None:
        return "not recorded"
    return model_card.applicability_domain_method


def _evaluation_dataset_provenance(
    dataset: ModelTrainingDataset | None,
    evaluation_report: ModelEvaluationReport,
) -> str:
    if dataset is not None:
        return _dataset_provenance(dataset)
    return f"Dataset ID: {evaluation_report.dataset_id}"


def _out_of_domain_predictions(
    predictions: Sequence[ModelPrediction],
) -> list[ModelPrediction]:
    return [
        prediction
        for prediction in predictions
        if prediction.applicability_domain == "out_of_domain"
    ]


def _prediction_summary(predictions: Sequence[ModelPrediction]) -> str:
    generated_count = sum(
        1 for prediction in predictions if prediction.candidate_origin == "generated"
    )
    out_of_domain_count = sum(
        1 for prediction in predictions if prediction.applicability_domain == "out_of_domain"
    )
    uncalibrated_count = sum(
        1 for prediction in predictions if prediction.calibration_status != "calibrated"
    )
    return "\n".join(
        [
            f"- Prediction count: {len(predictions)}",
            f"- Generated molecule prediction count: {generated_count}",
            f"- Out-of-domain prediction count: {out_of_domain_count}",
            f"- Uncalibrated or insufficient-calibration count: {uncalibrated_count}",
            "- Prediction role: weak computational prioritization signal only.",
        ]
    )


def _prediction_table(predictions: Sequence[ModelPrediction]) -> str:
    if not predictions:
        return "- No out-of-domain predictions recorded."
    rows = [
        "| Candidate | Endpoint | Confidence | Uncertainty | Warnings |",
        "| --- | --- | --- | --- | --- |",
    ]
    for prediction in predictions:
        rows.append(
            "| "
            + " | ".join(
                [
                    prediction.candidate_name,
                    prediction.endpoint_id,
                    f"{prediction.confidence:.3g}",
                    f"{prediction.uncertainty:.3g}",
                    "; ".join(prediction.warnings),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _generated_prediction_warnings(predictions: Sequence[ModelPrediction]) -> str:
    if not predictions:
        return "- No generated molecule predictions recorded."
    rows = [
        "- Generated molecule predictions are not experimental evidence.",
        (
            "- Generated molecules remain computational hypotheses until exact imported "
            "results are linked."
        ),
    ]
    for prediction in predictions:
        rows.append(
            f"- {prediction.candidate_name}: {prediction.applicability_domain}; "
            f"confidence {prediction.confidence:.3g}; {', '.join(prediction.warnings)}"
        )
    return "\n".join(rows)


def _limitations(model_card: ModelCard | None, extra_warnings: Sequence[str]) -> str:
    values = [*(model_card.limitations if model_card is not None else []), *extra_warnings]
    values.extend(MODEL_REPORT_DISCLAIMERS)
    return "\n".join(_bullet_lines(_dedupe(values)))


def _bullet_list(values: Sequence[str]) -> str:
    if not values:
        return "- None recorded."
    return "\n".join(_bullet_lines(values))


def _bullet_lines(values: Sequence[str]) -> list[str]:
    return [f"- {value}" for value in values]


def _json_block(value: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(dict(value), indent=2, sort_keys=True) + "\n```"


def _dedupe(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


__all__ = [
    "MODEL_REPORT_DISCLAIMERS",
    "ModelCard",
    "ModelEvaluationReport",
    "render_model_evaluation_report",
    "render_model_prediction_report",
    "render_model_training_report",
    "write_model_report_artifacts",
]
