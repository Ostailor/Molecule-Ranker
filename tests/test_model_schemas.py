from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelEvaluationReport,
    ModelFeatureSpec,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id="endpoint-binding-affinity",
        endpoint_name="binding_affinity",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit="nM",
        label_type="binary",
        positive_label="active",
        directionality="lower_is_better",
        thresholds={"active_nm": 100.0},
        metadata={"context_specific": True},
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors", "morgan_fingerprint", "oracle_scores"],
        fingerprint_radius=2,
        fingerprint_bits=2048,
        descriptor_names=["mol_wt", "tpsa"],
        normalization="standard",
        metadata={"deterministic": True},
    )


def test_model_endpoint_and_feature_spec_validate_allowed_values() -> None:
    endpoint = _endpoint()
    feature_spec = _feature_spec()

    assert endpoint.endpoint_category == "potency"
    assert endpoint.label_type == "binary"
    assert feature_spec.normalization == "standard"
    assert feature_spec.feature_families == [
        "rdkit_descriptors",
        "morgan_fingerprint",
        "oracle_scores",
    ]

    with pytest.raises(ValidationError):
        ModelEndpoint(**{**endpoint.model_dump(), "endpoint_category": "clinical"})
    with pytest.raises(ValidationError):
        ModelEndpoint(**{**endpoint.model_dump(), "label_type": "claim"})
    with pytest.raises(ValidationError):
        ModelFeatureSpec(**{**feature_spec.model_dump(), "normalization": "magic"})


def test_training_dataset_model_card_and_evaluation_require_aware_timestamps() -> None:
    endpoint = _endpoint()
    feature_spec = _feature_spec()
    dataset = ModelTrainingDataset(
        dataset_id="dataset-1",
        endpoint=endpoint,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_result_ids=["result-1", "result-2"],
        included_candidate_ids=["candidate-1", "candidate-2"],
        excluded_result_ids=["result-3"],
        exclusion_reasons={"result-3": "failed_qc"},
        feature_spec=feature_spec,
        feature_matrix_uri="artifacts/features.parquet",
        labels_uri="artifacts/labels.parquet",
        row_count=2,
        positive_count=1,
        negative_count=1,
        train_count=1,
        validation_count=0,
        test_count=1,
        metadata={"labels_are_imported_qc_passed_results": True},
    )
    card = ModelCard(
        model_id="model-1",
        model_name="binding surrogate",
        model_version="1.2.0",
        plugin_name="local_assay_surrogate",
        endpoint=endpoint,
        feature_spec=feature_spec,
        training_dataset_id=dataset.dataset_id,
        training_data_summary={"row_count": dataset.row_count},
        model_type="LogisticRegression",
        intended_use="assay-specific prioritization only",
        limitations=["Predictions are not experimental evidence."],
        metrics={"roc_auc": 0.75},
        calibration_metrics={"status": "calibrated"},
        applicability_domain_method="fingerprint_similarity",
        license=None,
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        created_by="analyst-1",
        metadata={"not_evidence": True},
    )
    evaluation = ModelEvaluationReport(
        evaluation_id="eval-1",
        model_id=card.model_id,
        dataset_id=dataset.dataset_id,
        split_strategy="deterministic_holdout",
        metrics={"roc_auc": 0.75},
        calibration_metrics={"ece": 0.1},
        leakage_checks={"test_labels_excluded": True},
        applicability_domain_summary={"in_domain": 2},
        warnings=[],
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
        metadata={},
    )

    assert dataset.created_at.tzinfo is not None
    assert card.endpoint.endpoint_id == endpoint.endpoint_id
    assert evaluation.leakage_checks["test_labels_excluded"] is True

    with pytest.raises(ValidationError):
        ModelTrainingDataset(**{**dataset.model_dump(), "created_at": datetime(2026, 1, 2)})
    with pytest.raises(ValidationError):
        ModelCard(**{**card.model_dump(), "created_at": datetime(2026, 1, 3)})
    with pytest.raises(ValidationError):
        ModelEvaluationReport(**{**evaluation.model_dump(), "created_at": datetime(2026, 1, 4)})


def test_prediction_and_training_run_validate_bounds_statuses_and_timestamps() -> None:
    prediction = ModelPrediction(
        prediction_id="prediction-1",
        model_id="model-1",
        model_version="1.2.0",
        endpoint_id="endpoint-binding-affinity",
        candidate_id="candidate-1",
        candidate_name="Candidate 1",
        candidate_origin="generated",
        canonical_smiles="CCO",
        inchi_key=None,
        predicted_value=True,
        predicted_probability=0.73,
        prediction_label="surrogate model estimate",
        uncertainty=0.22,
        confidence=0.78,
        applicability_domain="in_domain",
        calibration_status="calibrated",
        explanation="Computational surrogate estimate only.",
        warnings=["not experimental evidence"],
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
        metadata={"not_assay_result": True},
    )
    training_run = ModelTrainingRun(
        training_run_id="training-run-1",
        model_id="model-1",
        dataset_id="dataset-1",
        status="succeeded",
        started_at=datetime(2026, 1, 5, tzinfo=UTC),
        completed_at=datetime(2026, 1, 5, 1, tzinfo=UTC),
        metrics={"roc_auc": 0.75},
        calibration_metrics={"status": "calibrated"},
        artifact_paths={"model_card": "artifacts/model_card.json"},
        warnings=[],
        error_summary=None,
        metadata={},
    )

    assert prediction.predicted_probability == 0.73
    assert training_run.status == "succeeded"

    for field_name in ("predicted_probability", "uncertainty", "confidence"):
        with pytest.raises(ValidationError):
            ModelPrediction(**{**prediction.model_dump(), field_name: 1.01})
    with pytest.raises(ValidationError):
        ModelPrediction(**{**prediction.model_dump(), "candidate_origin": "invented"})
    with pytest.raises(ValidationError):
        ModelPrediction(**{**prediction.model_dump(), "applicability_domain": "active"})
    with pytest.raises(ValidationError):
        ModelPrediction(**{**prediction.model_dump(), "calibration_status": "validated"})
    with pytest.raises(ValidationError):
        ModelPrediction(**{**prediction.model_dump(), "created_at": datetime(2026, 1, 5)})
    with pytest.raises(ValidationError):
        ModelTrainingRun(**{**training_run.model_dump(), "status": "waiting"})
    with pytest.raises(ValidationError):
        ModelTrainingRun(**{**training_run.model_dump(), "started_at": datetime(2026, 1, 5)})
