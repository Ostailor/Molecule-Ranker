from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.models.plugin import (
    ExternalModelPluginPlaceholder,
    ModelPlugin,
    RuleBasedSurrogatePlugin,
    SklearnSurrogatePlugin,
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
from molecule_ranker.schemas import EvidenceItem


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
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors", "oracle_scores"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["mol_wt"],
        normalization="none",
    )


def _dataset(row_count: int = 2) -> ModelTrainingDataset:
    return ModelTrainingDataset(
        dataset_id="dataset-1",
        endpoint=_endpoint(),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_result_ids=["result-1", "result-2"][:row_count],
        included_candidate_ids=["candidate-1", "candidate-2"][:row_count],
        excluded_result_ids=[],
        exclusion_reasons={},
        feature_spec=_feature_spec(),
        feature_matrix_uri=None,
        labels_uri=None,
        row_count=row_count,
        positive_count=1 if row_count else 0,
        negative_count=1 if row_count > 1 else 0,
        train_count=None,
        validation_count=None,
        test_count=None,
        metadata={},
    )


def _model_card() -> ModelCard:
    endpoint = _endpoint()
    feature_spec = _feature_spec()
    return ModelCard(
        model_id="model-1",
        model_name="binding rule surrogate",
        model_version="1.2.0",
        plugin_name="rule_based_surrogate",
        endpoint=endpoint,
        feature_spec=feature_spec,
        training_dataset_id="dataset-1",
        training_data_summary={"row_count": 2},
        model_type="rule_based",
        intended_use="assay-specific prioritization only",
        limitations=["Predictions are not experimental evidence."],
        metrics={},
        calibration_metrics={"status": "not_applicable"},
        applicability_domain_method="feature_presence",
        license=None,
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        created_by=None,
        metadata={},
    )


def test_rule_based_plugin_conforms_to_contract_and_returns_training_run() -> None:
    plugin: ModelPlugin = RuleBasedSurrogatePlugin()

    training_run = plugin.train(
        _dataset(),
        features=[{"oracle_score": 0.7}, {"oracle_score": 0.3}],
        labels=[1, 0],
        config={"model_id": "model-1"},
    )

    assert isinstance(plugin, ModelPlugin)
    assert isinstance(training_run, ModelTrainingRun)
    assert training_run.status == "succeeded"
    assert training_run.model_id == "model-1"
    assert training_run.dataset_id == "dataset-1"
    assert plugin.supported_label_types == ["binary", "regression"]


def test_rule_based_predictions_are_guarded_model_predictions_only() -> None:
    plugin = RuleBasedSurrogatePlugin()

    predictions = plugin.predict(
        _model_card(),
        candidates=[
            {
                "candidate_id": "candidate-1",
                "candidate_name": "Candidate 1",
                "candidate_origin": "generated",
                "canonical_smiles": "CCO",
            }
        ],
        features=[{"predicted_probability": 0.64, "uncertainty": 0.2}],
        config={},
    )

    assert len(predictions) == 1
    prediction = predictions[0]
    assert isinstance(prediction, ModelPrediction)
    assert not isinstance(prediction, EvidenceItem)
    assert not isinstance(prediction, AssayResult)
    assert prediction.model_id == "model-1"
    assert prediction.endpoint_id == "endpoint-binding-affinity"
    assert prediction.uncertainty == 0.2
    assert prediction.applicability_domain == "in_domain"
    assert prediction.warnings
    assert prediction.metadata["not_experimental_evidence"] is True
    assert prediction.metadata["not_assay_result"] is True


def test_rule_based_evaluation_returns_report_with_leakage_boundary() -> None:
    plugin = RuleBasedSurrogatePlugin()

    report = plugin.evaluate(
        _model_card(),
        _dataset(),
        features=[{"predicted_probability": 0.6}],
        labels=[1],
        splits={"strategy": "deterministic_holdout", "test_result_ids": ["result-2"]},
    )

    assert isinstance(report, ModelEvaluationReport)
    assert report.model_id == "model-1"
    assert report.dataset_id == "dataset-1"
    assert report.split_strategy == "deterministic_holdout"
    assert report.leakage_checks["test_labels_excluded_from_training"] is True
    assert report.warnings


def test_sklearn_plugin_missing_dependency_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = SklearnSurrogatePlugin()
    monkeypatch.setattr("molecule_ranker.models.plugin._load_sklearn", lambda: None)

    with pytest.raises(RuntimeError, match="scikit-learn"):
        plugin.train(_dataset(), features=[], labels=[], config={"model_id": "model-1"})


def test_external_plugin_placeholder_is_disabled_by_default() -> None:
    plugin = ExternalModelPluginPlaceholder()

    assert plugin.plugin_name == "external_model_placeholder"
    with pytest.raises(RuntimeError, match="disabled by default"):
        plugin.predict(_model_card(), candidates=[], features=[], config={})


def test_plugins_reject_unsupported_labels_and_features() -> None:
    plugin = RuleBasedSurrogatePlugin()
    dataset = _dataset().model_copy(
        update={
            "endpoint": _endpoint().model_copy(update={"label_type": "multiclass"}),
            "feature_spec": _feature_spec().model_copy(
                update={"feature_families": ["patient_clinical_data"]}
            ),
        }
    )

    with pytest.raises(ValueError, match="Unsupported label type"):
        plugin.train(dataset, features=[], labels=[], config={"model_id": "model-1"})


def _assert_contract_shape(_plugin: ModelPlugin, _payload: Any) -> None:
    pass
