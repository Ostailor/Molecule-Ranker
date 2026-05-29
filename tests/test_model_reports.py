from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.models.reports import (
    render_model_evaluation_report,
    render_model_prediction_report,
    render_model_training_report,
    write_model_report_artifacts,
)
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelEvaluationReport,
    ModelFeatureSpec,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)
from molecule_ranker.models.splits import ModelSplitResult


def test_training_report_includes_provenance_and_leakage_checks() -> None:
    report = render_model_training_report(
        dataset=_dataset(),
        training_run=_training_run(),
        model_card=_model_card(),
        split_result=_split_result(),
    )

    assert "## Dataset Provenance" in report
    assert "result-1" in report
    assert "result-excluded" in report
    assert "failed_qc" in report
    assert "## Leakage Checks" in report
    assert "feature_label_leakage" in report
    assert "Predictions are not experimental evidence." in report


def test_evaluation_report_includes_leakage_checks() -> None:
    report = render_model_evaluation_report(
        evaluation_report=_evaluation_report(),
        model_card=_model_card(),
        dataset=_dataset(),
    )

    assert "Model Evaluation Report" in report
    assert "scaffold" in report
    assert "duplicate_assay_result_id" in report
    assert "Predictions are not clinical claims." in report


def test_prediction_report_shows_generated_prediction_warnings() -> None:
    report = render_model_prediction_report(
        model_card=_model_card(),
        predictions=[_prediction(origin="generated", applicability_domain="out_of_domain")],
        dataset=_dataset(),
        training_run=_training_run(),
        evaluation_report=_evaluation_report(),
        prediction_batch_artifact_id="batch-1",
    )

    assert "## Generated Molecule Prediction Warnings" in report
    assert "Generated molecule predictions are not experimental evidence." in report
    assert "Generated molecules remain computational hypotheses" in report
    assert "Candidate 1" in report
    assert "out_of_domain" in report


def test_reports_do_not_overclaim() -> None:
    report = render_model_prediction_report(
        model_card=_model_card(),
        predictions=[_prediction()],
        dataset=_dataset(),
        training_run=_training_run(),
        evaluation_report=_evaluation_report(),
        prediction_batch_artifact_id="batch-1",
    )
    lowered = report.lower()

    assert "prediction role: weak computational prioritization signal only" in lowered
    assert "is active" not in lowered
    assert "is safe" not in lowered
    assert "is efficacious" not in lowered
    assert "treats" not in lowered
    assert "cures" not in lowered


def test_write_model_report_artifacts_uses_standard_names(tmp_path: Path) -> None:
    paths = write_model_report_artifacts(
        output_dir=tmp_path,
        dataset=_dataset(),
        training_run=_training_run(),
        model_card=_model_card(),
        predictions=[_prediction(origin="generated")],
        split_result=_split_result(),
        evaluation_report=_evaluation_report(),
        prediction_batch_artifact_id="batch-1",
    )

    assert paths["model_training_report"].name == "model_training_report.md"
    assert paths["model_evaluation_report"].name == "model_evaluation_report.md"
    assert paths["model_card_json"].name == "model_card.json"
    assert paths["model_predictions_json"].name == "model_predictions.json"
    assert paths["model_prediction_report"].name == "model_prediction_report.md"
    assert json.loads(paths["model_card_json"].read_text())["model_id"] == "model-1"
    assert "result-1" in paths["model_training_report"].read_text()
    assert "Generated molecule" in paths["model_prediction_report"].read_text()


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id="endpoint-binary",
        endpoint_name="binary_endpoint",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit="nM",
        label_type="binary",
        positive_label="positive",
        directionality="binary",
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["molecular_weight"],
        normalization="none",
    )


def _dataset() -> ModelTrainingDataset:
    return ModelTrainingDataset(
        dataset_id="dataset-1",
        endpoint=_endpoint(),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_result_ids=["result-1", "result-2"],
        included_candidate_ids=["candidate-1", "candidate-2"],
        excluded_result_ids=["result-excluded"],
        exclusion_reasons={"result-excluded": "failed_qc"},
        feature_spec=_feature_spec(),
        feature_matrix_uri="features.json",
        labels_uri="labels.json",
        row_count=2,
        positive_count=1,
        negative_count=1,
        metadata={"labels_are_imported_qc_passed_results": True},
    )


def _model_card() -> ModelCard:
    return ModelCard(
        model_id="model-1",
        model_name="binary local surrogate",
        model_version="1.2.0",
        plugin_name="local_sklearn_baseline",
        endpoint=_endpoint(),
        feature_spec=_feature_spec(),
        training_dataset_id="dataset-1",
        training_data_summary={"source_result_ids": ["result-1", "result-2"]},
        model_type="LogisticRegression",
        intended_use="Assay-specific prioritization only.",
        limitations=["Predictions are not experimental evidence."],
        metrics={"accuracy": 0.75},
        calibration_metrics={"status": "uncalibrated"},
        applicability_domain_method="nearest_neighbor_tanimoto",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )


def _training_run() -> ModelTrainingRun:
    return ModelTrainingRun(
        training_run_id="training-run-1",
        model_id="model-1",
        dataset_id="dataset-1",
        status="succeeded",
        started_at=datetime(2026, 1, 4, tzinfo=UTC),
        completed_at=datetime(2026, 1, 4, 1, tzinfo=UTC),
        metrics={"accuracy": 0.75},
        calibration_metrics={"status": "uncalibrated"},
        artifact_paths={},
        warnings=["insufficient calibration data"],
        metadata={"leakage_check_report": _leakage_checks()},
    )


def _evaluation_report() -> ModelEvaluationReport:
    return ModelEvaluationReport(
        evaluation_id="evaluation-1",
        model_id="model-1",
        dataset_id="dataset-1",
        split_strategy="scaffold",
        metrics={"accuracy": 0.75},
        calibration_metrics={"status": "uncalibrated"},
        leakage_checks=_leakage_checks(),
        applicability_domain_summary={"out_of_domain": 1},
        warnings=["small validation set"],
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )


def _prediction(
    *,
    origin: str = "existing",
    applicability_domain: str = "near_domain",
) -> ModelPrediction:
    return ModelPrediction(
        prediction_id="prediction-1",
        model_id="model-1",
        model_version="1.2.0",
        endpoint_id="endpoint-binary",
        candidate_id="candidate-1",
        candidate_name="Candidate 1",
        candidate_origin=origin,  # type: ignore[arg-type]
        canonical_smiles="CCO",
        predicted_value=True,
        predicted_probability=0.6,
        prediction_label="surrogate_positive",
        uncertainty=0.4,
        confidence=0.6,
        applicability_domain=applicability_domain,  # type: ignore[arg-type]
        calibration_status="uncalibrated",
        explanation="Computational surrogate prediction artifact only.",
        warnings=["not evidence"],
        created_at=datetime(2026, 1, 6, tzinfo=UTC),
        metadata={"not_experimental_evidence": True},
    )


def _split_result() -> ModelSplitResult:
    return ModelSplitResult(
        strategy="scaffold",
        assignments=[],
        leakage_check_report=_leakage_checks(),
    )


def _leakage_checks() -> dict[str, object]:
    return {
        "passed": True,
        "failed_checks": [],
        "checks": {
            "feature_label_leakage": {"passed": True},
            "duplicate_assay_result_id": {"passed": True},
        },
    }
