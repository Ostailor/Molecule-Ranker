"""Optional local surrogate models for imported assay outcome datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from molecule_ranker.experiments.schemas import ExperimentalLearningDataset

NON_FEATURE_COLUMNS = {
    "result_id",
    "candidate_id",
    "candidate_name",
    "candidate_origin",
    "canonical_smiles",
    "inchi_key",
    "disease_name",
    "target_symbol",
    "assay_name",
    "assay_type",
    "endpoint_name",
    "endpoint_category",
    "endpoint_directionality",
    "measured_value_numeric",
    "normalized_value",
    "normalized_unit",
    "outcome_label",
    "activity_direction",
    "qc_status",
    "label",
    "label_type",
    "binary_label",
    "continuous_label",
    "safety_label",
}


@dataclass
class SurrogateModelArtifact:
    trained: bool
    model: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    label_kind: str | None = None


@dataclass(frozen=True)
class _Estimators:
    RandomForestClassifier: Any
    RandomForestRegressor: Any
    LogisticRegression: Any
    KFold: Any
    cross_val_score: Any


def train_assay_surrogate_model(
    dataset: ExperimentalLearningDataset,
    *,
    config: dict[str, Any] | None = None,
) -> SurrogateModelArtifact:
    """Train an optional local surrogate model from labeled QC-passed assay rows."""

    config = dict(config or {})
    min_count = int(config.get("min_training_result_count", 8))
    training_rows, label_kind = _training_rows(dataset)
    metadata = _base_metadata(dataset, training_rows)
    if len(training_rows) < min_count:
        metadata["limitations"].insert(
            0,
            f"Insufficient labeled QC-passed results for surrogate modeling: "
            f"{len(training_rows)} < {min_count}.",
        )
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=[],
            label_kind=label_kind,
        )

    estimators = _load_sklearn_estimators()
    if estimators is None:
        metadata["limitations"].insert(
            0,
            "scikit-learn is not installed; heuristic active learning remains in use.",
        )
        metadata["model_type"] = "unavailable"
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=[],
            label_kind=label_kind,
        )

    feature_names = _feature_names(training_rows)
    x = [_feature_vector(row, feature_names) for row in training_rows]
    y = [_label_value(row, label_kind) for row in training_rows]
    if label_kind == "binary" and len(set(y)) < 2:
        metadata["limitations"].insert(
            0,
            "Binary surrogate model requires at least two observed label classes.",
        )
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=feature_names,
            label_kind=label_kind,
        )

    model_type, model = _make_model(
        estimators,
        label_kind=label_kind,
        training_count=len(training_rows),
        config=config,
    )
    model.fit(x, y)
    metadata.update(
        {
            "model_type": model_type,
            "features_used": feature_names,
            "label_kind": label_kind,
            "calibration_status": "uncalibrated",
            "cross_validation": _cross_validation_metadata(
                estimators,
                model,
                x,
                y,
                label_kind=label_kind,
            ),
        }
    )
    return SurrogateModelArtifact(
        trained=True,
        model=model,
        metadata=metadata,
        feature_names=feature_names,
        label_kind=label_kind,
    )


def predict_assay_surrogate_outcomes(
    artifact: SurrogateModelArtifact,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return bounded predictions labeled as surrogate model estimates."""

    if not artifact.trained or artifact.model is None:
        return []
    predictions: list[dict[str, Any]] = []
    x = [_feature_vector(row, artifact.feature_names) for row in rows]
    estimates = _predict_estimates(artifact.model, x, artifact.label_kind)
    for row, estimate in zip(rows, estimates, strict=True):
        predictions.append(
            {
                "candidate_id": row.get("candidate_id"),
                "candidate_name": row.get("candidate_name"),
                "endpoint_name": row.get("endpoint_name") or artifact.metadata.get("endpoint"),
                "surrogate_model_estimate": round(_clamp(estimate), 6),
                "prediction_label": "surrogate model estimate",
                "metadata": {
                    "model_type": artifact.metadata.get("model_type"),
                    "calibration_status": artifact.metadata.get("calibration_status"),
                    "not_experimental_evidence": True,
                    "not_biological_activity_proof": True,
                    "features_used": list(artifact.feature_names),
                },
            }
        )
    return predictions


def _training_rows(
    dataset: ExperimentalLearningDataset,
) -> tuple[list[dict[str, Any]], str | None]:
    binary_rows = [
        row
        for row in dataset.rows
        if row.get("qc_status") == "passed" and row.get("binary_label") in {0, 1}
    ]
    if binary_rows:
        return binary_rows, "binary"
    continuous_rows = [
        row
        for row in dataset.rows
        if row.get("qc_status") == "passed" and _is_number(row.get("continuous_label"))
    ]
    if continuous_rows:
        return continuous_rows, "continuous"
    return [], None


def _make_model(
    estimators: _Estimators,
    *,
    label_kind: str | None,
    training_count: int,
    config: dict[str, Any],
) -> tuple[str, Any]:
    small_threshold = int(config.get("small_dataset_threshold", 12))
    random_state = int(config.get("random_state", 13))
    if label_kind == "binary" and training_count <= small_threshold:
        return (
            "LogisticRegression",
            estimators.LogisticRegression(max_iter=1000, random_state=random_state),
        )
    if label_kind == "binary":
        return (
            "RandomForestClassifier",
            estimators.RandomForestClassifier(n_estimators=100, random_state=random_state),
        )
    return (
        "RandomForestRegressor",
        estimators.RandomForestRegressor(n_estimators=100, random_state=random_state),
    )


def _cross_validation_metadata(
    estimators: _Estimators,
    model: Any,
    x: list[list[float]],
    y: list[float],
    *,
    label_kind: str | None,
) -> dict[str, Any]:
    if len(x) < 4:
        return {"performed": False, "reason": "too_few_rows"}
    if label_kind == "binary" and len(set(y)) < 2:
        return {"performed": False, "reason": "single_class"}
    try:
        folds = min(3, len(x))
        cv = estimators.KFold(n_splits=folds, shuffle=True, random_state=13)
        scoring = "roc_auc" if label_kind == "binary" else "neg_mean_squared_error"
        scores = estimators.cross_val_score(model, x, y, cv=cv, scoring=scoring)
    except Exception as exc:
        return {"performed": False, "reason": f"cross_validation_failed:{type(exc).__name__}"}
    score_values = [float(score) for score in scores]
    return {
        "performed": True,
        "method": "KFold",
        "folds": len(score_values),
        "scoring": scoring,
        "scores": [round(score, 6) for score in score_values],
        "mean_score": round(sum(score_values) / len(score_values), 6),
    }


def _base_metadata(
    dataset: ExperimentalLearningDataset,
    training_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model_type": None,
        "training_result_count": len(training_rows),
        "endpoint": dataset.endpoint_name,
        "disease_name": dataset.disease_name,
        "target_symbol": dataset.target_symbol,
        "features_used": [],
        "cross_validation": {"performed": False, "reason": "model_not_trained"},
        "calibration_status": "uncalibrated",
        "limitations": [
            "Surrogate model estimates are computational prioritization signals only.",
            "Surrogate model estimates are not proof of biological activity.",
            "Surrogate model predictions are not experimental evidence.",
        ],
    }


def _feature_names(rows: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    max_fingerprint_bit = 0
    for row in rows:
        for key, value in row.items():
            if key in NON_FEATURE_COLUMNS or key.startswith("review_"):
                continue
            if _is_number(value):
                names.add(key)
        bits = row.get("morgan_fp_on_bits")
        if isinstance(bits, list):
            numeric_bits = [int(bit) for bit in bits if isinstance(bit, int)]
            max_fingerprint_bit = max([max_fingerprint_bit, *numeric_bits], default=0)
    fingerprint_bits = [f"morgan_bit_{index}" for index in range(max_fingerprint_bit + 1)]
    return sorted(names) + fingerprint_bits


def _feature_vector(row: dict[str, Any], feature_names: list[str]) -> list[float]:
    on_bits = set()
    raw_bits = row.get("morgan_fp_on_bits")
    if isinstance(raw_bits, list):
        on_bits = {int(bit) for bit in raw_bits if isinstance(bit, int)}
    values: list[float] = []
    for name in feature_names:
        if name.startswith("morgan_bit_"):
            bit = int(name.rsplit("_", 1)[-1])
            values.append(1.0 if bit in on_bits else 0.0)
            continue
        values.append(_to_float(row.get(name)))
    return values


def _label_value(row: dict[str, Any], label_kind: str | None) -> float:
    key = "binary_label" if label_kind == "binary" else "continuous_label"
    return _to_float(row.get(key))


def _predict_estimates(model: Any, x: list[list[float]], label_kind: str | None) -> list[float]:
    if label_kind == "binary" and hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(x)
        return [float(probability[-1]) for probability in probabilities]
    raw_predictions = model.predict(x)
    return [float(value) for value in raw_predictions]


def _load_sklearn_estimators() -> _Estimators | None:
    try:
        ensemble = import_module("sklearn.ensemble")
        linear_model = import_module("sklearn.linear_model")
        model_selection = import_module("sklearn.model_selection")
    except Exception:
        return None
    return _Estimators(
        RandomForestClassifier=ensemble.RandomForestClassifier,
        RandomForestRegressor=ensemble.RandomForestRegressor,
        LogisticRegression=linear_model.LogisticRegression,
        KFold=model_selection.KFold,
        cross_val_score=model_selection.cross_val_score,
    )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _to_float(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "SurrogateModelArtifact",
    "predict_assay_surrogate_outcomes",
    "train_assay_surrogate_model",
]
