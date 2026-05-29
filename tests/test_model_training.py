from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.models.schemas import ModelEndpoint, ModelFeatureSpec, ModelTrainingDataset
from molecule_ranker.models.training import train_baseline_surrogate_model


def _endpoint(label_type: str = "binary") -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id=f"endpoint-{label_type}",
        endpoint_name=f"{label_type}_endpoint",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit="nM",
        label_type=label_type,  # type: ignore[arg-type]
        positive_label="positive" if label_type == "binary" else None,
        directionality="binary" if label_type == "binary" else "lower_is_better",
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["molecular_weight", "tpsa"],
        normalization="none",
    )


def _dataset(
    *,
    label_type: str = "binary",
    row_count: int = 8,
    positive_count: int | None = 4,
    negative_count: int | None = 4,
) -> ModelTrainingDataset:
    return ModelTrainingDataset(
        dataset_id=f"dataset-{label_type}",
        endpoint=_endpoint(label_type),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_result_ids=[f"result-{index}" for index in range(row_count)],
        included_candidate_ids=[f"candidate-{index}" for index in range(row_count)],
        excluded_result_ids=[],
        exclusion_reasons={},
        feature_spec=_feature_spec(),
        feature_matrix_uri="features.json",
        labels_uri="labels.json",
        row_count=row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        metadata={"labels_are_imported_qc_passed_results": True},
    )


def _feature_rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "row_id": f"candidate-{index}",
            "candidate_id": f"candidate-{index}",
            "candidate_name": f"Candidate {index}",
            "candidate_origin": "existing",
            "result_id": f"result-{index}",
            "canonical_smiles": f"CC{'C' * index}O",
            "features": {
                "molecular_weight": 40.0 + index,
                "tpsa": 20.0 + index,
            },
        }
        for index in range(count)
    ]


def test_insufficient_data_skips(tmp_path: Path) -> None:
    result = train_baseline_surrogate_model(
        dataset=_dataset(row_count=2, positive_count=1, negative_count=1),
        feature_rows=_feature_rows(2),
        labels=[1, 0],
        output_dir=tmp_path,
        config={"min_training_rows_binary": 8},
    )

    assert result.training_run.status == "skipped_insufficient_data"
    assert result.model_card is None
    assert result.training_run.artifact_paths == {}


def test_binary_model_trains_on_mock_data(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "molecule_ranker.models.training._load_sklearn_estimators",
        lambda: _fake_estimators(),
    )

    result = train_baseline_surrogate_model(
        dataset=_dataset(row_count=8, positive_count=4, negative_count=4),
        feature_rows=_feature_rows(8),
        labels=[0, 1, 0, 1, 0, 1, 0, 1],
        output_dir=tmp_path,
        config={"model_type": "logistic_regression", "min_training_rows_binary": 4},
    )

    assert result.training_run.status == "succeeded"
    assert result.model_card is not None
    assert result.model_card.model_type == "LogisticRegression"
    assert Path(result.training_run.artifact_paths["model_card"]).exists()
    assert Path(result.training_run.artifact_paths["model_artifact"]).exists()
    assert Path(result.training_run.artifact_paths["feature_schema"]).exists()
    assert Path(result.training_run.artifact_paths["model_training_report"]).name == (
        "model_training_report.md"
    )
    assert Path(result.training_run.artifact_paths["model_predictions_json"]).name == (
        "model_predictions.json"
    )


def test_regression_model_trains_on_mock_data(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "molecule_ranker.models.training._load_sklearn_estimators",
        lambda: _fake_estimators(),
    )

    result = train_baseline_surrogate_model(
        dataset=_dataset(
            label_type="regression",
            row_count=8,
            positive_count=None,
            negative_count=None,
        ),
        feature_rows=_feature_rows(8),
        labels=[float(index) for index in range(8)],
        output_dir=tmp_path,
        config={"model_type": "random_forest", "min_training_rows_regression": 4},
    )

    assert result.training_run.status == "succeeded"
    assert result.model_card is not None
    assert result.model_card.model_type == "RandomForestRegressor"
    assert result.training_run.metrics["label_type"] == "regression"


def test_leakage_failure_blocks_training(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "molecule_ranker.models.training._load_sklearn_estimators",
        lambda: _fake_estimators(),
    )
    feature_rows = _feature_rows(8)
    feature_rows[0]["features"]["label"] = 1

    result = train_baseline_surrogate_model(
        dataset=_dataset(row_count=8, positive_count=4, negative_count=4),
        feature_rows=feature_rows,
        labels=[0, 1, 0, 1, 0, 1, 0, 1],
        output_dir=tmp_path,
        config={"min_training_rows_binary": 4},
    )

    assert result.training_run.status == "failed"
    assert "leakage" in (result.training_run.error_summary or "").lower()
    assert result.model_card is None


def test_model_card_created(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "molecule_ranker.models.training._load_sklearn_estimators",
        lambda: _fake_estimators(),
    )

    result = train_baseline_surrogate_model(
        dataset=_dataset(row_count=8, positive_count=4, negative_count=4),
        feature_rows=_feature_rows(8),
        labels=[0, 1, 0, 1, 0, 1, 0, 1],
        output_dir=tmp_path,
        config={"min_training_rows_binary": 4, "created_by": "tester"},
    )

    assert result.model_card is not None
    assert result.model_card.training_dataset_id == "dataset-binary"
    assert result.model_card.created_by == "tester"
    assert "Predictions are not experimental evidence." in result.model_card.limitations
    assert result.model_card_path is not None
    assert json.loads(result.model_card_path.read_text())["model_id"] == result.model_card.model_id


def test_prediction_artifact_generated(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "molecule_ranker.models.training._load_sklearn_estimators",
        lambda: _fake_estimators(),
    )

    result = train_baseline_surrogate_model(
        dataset=_dataset(row_count=8, positive_count=4, negative_count=4),
        feature_rows=_feature_rows(8),
        labels=[0, 1, 0, 1, 0, 1, 0, 1],
        output_dir=tmp_path,
        config={"min_training_rows_binary": 4},
    )

    assert result.prediction_artifact_path is not None
    prediction_artifact = json.loads(result.prediction_artifact_path.read_text())

    assert prediction_artifact["artifact_type"] == "ModelPredictionArtifact"
    assert prediction_artifact["predictions"]
    assert prediction_artifact["predictions"][0]["metadata"]["not_experimental_evidence"] is True
    assert prediction_artifact["predictions"][0]["metadata"]["not_assay_result"] is True
    standard_predictions = json.loads(
        Path(result.training_run.artifact_paths["model_predictions_json"]).read_text()
    )
    assert standard_predictions["artifact_type"] == "ModelPredictionArtifact"


@dataclass(frozen=True)
class _FakeEstimators:
    RandomForestClassifier: Any
    RandomForestRegressor: Any
    LogisticRegression: Any
    DummyClassifier: Any
    DummyRegressor: Any


class _FakeClassifier:
    def __init__(self, **_kwargs: Any) -> None:
        self.fit_called = False

    def fit(self, _x: list[list[float]], _y: list[float]) -> _FakeClassifier:
        self.fit_called = True
        return self

    def predict(self, x: list[list[float]]) -> list[int]:
        return [1 if sum(row) % 2 else 0 for row in x]

    def predict_proba(self, x: list[list[float]]) -> list[list[float]]:
        return [[0.35, 0.65] if sum(row) % 2 else [0.7, 0.3] for row in x]


class _FakeRegressor:
    def __init__(self, **_kwargs: Any) -> None:
        self.fit_called = False

    def fit(self, _x: list[list[float]], _y: list[float]) -> _FakeRegressor:
        self.fit_called = True
        return self

    def predict(self, x: list[list[float]]) -> list[float]:
        return [sum(row) / max(len(row), 1) for row in x]


def _fake_estimators() -> _FakeEstimators:
    return _FakeEstimators(
        RandomForestClassifier=_FakeClassifier,
        RandomForestRegressor=_FakeRegressor,
        LogisticRegression=_FakeClassifier,
        DummyClassifier=_FakeClassifier,
        DummyRegressor=_FakeRegressor,
    )
