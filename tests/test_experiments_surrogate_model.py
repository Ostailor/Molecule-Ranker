from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from molecule_ranker.experiments.schemas import ExperimentalLearningDataset
from molecule_ranker.experiments.surrogate_model import (
    SurrogateModelArtifact,
    predict_assay_surrogate_outcomes,
    train_assay_surrogate_model,
)
from molecule_ranker.schemas import EvidenceItem


def _dataset(rows: list[dict[str, Any]]) -> ExperimentalLearningDataset:
    return ExperimentalLearningDataset(
        dataset_id="dataset-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        endpoint_name="binding_affinity",
        rows=rows,
        feature_schema={"desc_molecular_weight": "float", "existing_ranking_score": "float"},
        label_schema={"binary_label": {"active": 1, "inactive": 0}},
        included_result_ids=[str(row["result_id"]) for row in rows],
    )


def _row(result_id: str, label: int, score: float) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "candidate_id": f"candidate-{result_id}",
        "candidate_name": f"Candidate {result_id}",
        "qc_status": "passed",
        "label": label,
        "label_type": "binary",
        "binary_label": label,
        "continuous_label": None,
        "desc_molecular_weight": 150.0 + score,
        "existing_ranking_score": score,
        "developability_score": 0.7,
        "morgan_fp_on_bits": [1, 7, 12] if label else [2, 8],
        "morgan_fp_n_bits": 32,
    }


def test_insufficient_data_skips_model():
    artifact = train_assay_surrogate_model(
        _dataset([_row("1", 1, 0.7), _row("2", 0, 0.2)]),
        config={"min_training_result_count": 4},
    )

    assert artifact.trained is False
    assert artifact.model is None
    assert artifact.metadata["training_result_count"] == 2
    assert "insufficient" in artifact.metadata["limitations"][0].lower()


def test_simple_model_trains_on_mocked_dataset(monkeypatch):
    monkeypatch.setattr(
        "molecule_ranker.experiments.surrogate_model._load_sklearn_estimators",
        lambda: _FakeEstimators(),
    )

    artifact = train_assay_surrogate_model(
        _dataset(
            [
                _row("1", 1, 0.8),
                _row("2", 0, 0.2),
                _row("3", 1, 0.75),
                _row("4", 0, 0.3),
                _row("5", 1, 0.9),
                _row("6", 0, 0.1),
            ]
        ),
        config={"min_training_result_count": 4, "small_dataset_threshold": 8},
    )

    assert artifact.trained is True
    assert artifact.metadata["model_type"] == "LogisticRegression"
    assert artifact.metadata["training_result_count"] == 6
    assert artifact.metadata["endpoint"] == "binding_affinity"
    assert artifact.metadata["target_symbol"] == "MAOB"
    assert artifact.metadata["calibration_status"] == "uncalibrated"
    assert artifact.metadata["features_used"]


def test_predictions_are_bounded_and_labeled_surrogate_estimates(monkeypatch):
    monkeypatch.setattr(
        "molecule_ranker.experiments.surrogate_model._load_sklearn_estimators",
        lambda: _FakeEstimators(),
    )
    artifact = train_assay_surrogate_model(
        _dataset(
            [
                _row("1", 1, 0.8),
                _row("2", 0, 0.2),
                _row("3", 1, 0.75),
                _row("4", 0, 0.3),
            ]
        ),
        config={"min_training_result_count": 4},
    )

    predictions = predict_assay_surrogate_outcomes(
        artifact,
        [_row("candidate", 1, 0.55)],
    )

    assert len(predictions) == 1
    prediction = predictions[0]
    assert 0.0 <= prediction["surrogate_model_estimate"] <= 1.0
    assert prediction["prediction_label"] == "surrogate model estimate"
    assert prediction["metadata"]["not_experimental_evidence"] is True


def test_predictions_do_not_become_evidence_items(monkeypatch):
    monkeypatch.setattr(
        "molecule_ranker.experiments.surrogate_model._load_sklearn_estimators",
        lambda: _FakeEstimators(),
    )
    artifact = train_assay_surrogate_model(
        _dataset([_row("1", 1, 0.8), _row("2", 0, 0.2), _row("3", 1, 0.7), _row("4", 0, 0.3)]),
        config={"min_training_result_count": 4},
    )

    predictions = predict_assay_surrogate_outcomes(artifact, [_row("candidate", 1, 0.55)])

    assert isinstance(artifact, SurrogateModelArtifact)
    assert all(not isinstance(prediction, EvidenceItem) for prediction in predictions)
    assert "not experimental evidence" in artifact.metadata["limitations"][-1].lower()


@dataclass
class _FakeEstimators:
    RandomForestClassifier: type = None  # type: ignore[assignment]
    RandomForestRegressor: type = None  # type: ignore[assignment]
    LogisticRegression: type = None  # type: ignore[assignment]
    KFold: type = None  # type: ignore[assignment]
    cross_val_score: Any = None

    def __post_init__(self) -> None:
        self.RandomForestClassifier = _FakeClassifier
        self.RandomForestRegressor = _FakeRegressor
        self.LogisticRegression = _FakeLogisticRegression
        self.KFold = _FakeKFold
        self.cross_val_score = _fake_cross_val_score


class _FakeClassifier:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def fit(self, x: list[list[float]], y: list[float]) -> _FakeClassifier:
        self.mean_label = sum(y) / len(y)
        return self

    def predict_proba(self, x: list[list[float]]) -> list[list[float]]:
        return [[0.25, 0.75] for _ in x]

    def predict(self, x: list[list[float]]) -> list[int]:
        return [1 for _ in x]


class _FakeLogisticRegression(_FakeClassifier):
    pass


class _FakeRegressor:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def fit(self, x: list[list[float]], y: list[float]) -> _FakeRegressor:
        self.mean_label = sum(y) / len(y)
        return self

    def predict(self, x: list[list[float]]) -> list[float]:
        return [0.42 for _ in x]


class _FakeKFold:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _fake_cross_val_score(
    estimator: object,
    x: list[list[float]],
    y: list[float],
    *,
    cv: object,
    scoring: str,
) -> list[float]:
    return [0.5, 0.75]
