"""Baseline local surrogate model training helpers."""

from __future__ import annotations

import json
import pickle
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.models.reports import write_model_report_artifacts
from molecule_ranker.models.schemas import (
    CandidateOrigin,
    ModelCard,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)
from molecule_ranker.models.splits import ModelSplitResult, build_model_splits

PREDICTION_WARNING = "Predictions are not experimental evidence."
ASSAY_RESULT_WARNING = "Predictions are not assay results."


@dataclass(frozen=True)
class BaselineTrainingResult:
    training_run: ModelTrainingRun
    model_card: ModelCard | None
    split_result: ModelSplitResult | None
    model_card_path: Path | None = None
    model_artifact_path: Path | None = None
    prediction_artifact_path: Path | None = None


@dataclass(frozen=True)
class _Estimators:
    RandomForestClassifier: Any
    RandomForestRegressor: Any
    LogisticRegression: Any
    DummyClassifier: Any
    DummyRegressor: Any


def train_baseline_surrogate_model(
    *,
    dataset: ModelTrainingDataset,
    feature_rows: Sequence[Mapping[str, Any]],
    labels: Sequence[Any],
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
) -> BaselineTrainingResult:
    config = dict(config or {})
    now = datetime.now(UTC)
    model_id = str(config.get("model_id") or _stable_id("model", dataset.dataset_id))
    training_run_id = _stable_id("training-run", model_id, dataset.dataset_id, now.isoformat())
    output_path = Path(output_dir)

    readiness_failure = _readiness_failure(dataset, labels, config)
    if readiness_failure is not None:
        return BaselineTrainingResult(
            training_run=_training_run(
                training_run_id=training_run_id,
                model_id=model_id,
                dataset=dataset,
                status="skipped_insufficient_data",
                started_at=now,
                warnings=[readiness_failure],
            ),
            model_card=None,
            split_result=None,
        )

    pooling_failure = _pooling_failure(dataset, config)
    if pooling_failure is not None:
        return BaselineTrainingResult(
            training_run=_training_run(
                training_run_id=training_run_id,
                model_id=model_id,
                dataset=dataset,
                status="failed",
                started_at=now,
                warnings=[pooling_failure],
                error_summary=pooling_failure,
            ),
            model_card=None,
            split_result=None,
        )

    split_result = build_model_splits(
        feature_rows,
        feature_rows=feature_rows,
        strategy=str(config.get("split_strategy") or "auto"),  # type: ignore[arg-type]
        output_dir=output_path,
        config={
            "seed": int(config.get("random_seed", 0) or 0),
            "test_fraction": float(config.get("test_fraction", 0.2) or 0.2),
            "result_dates_reliable": bool(config.get("result_dates_reliable", False)),
        },
    )
    if not split_result.leakage_check_report["passed"]:
        error = "Leakage checks failed; surrogate training was blocked."
        return BaselineTrainingResult(
            training_run=_training_run(
                training_run_id=training_run_id,
                model_id=model_id,
                dataset=dataset,
                status="failed",
                started_at=now,
                warnings=[error],
                error_summary=error,
                metadata={"leakage_check_report": split_result.leakage_check_report},
            ),
            model_card=None,
            split_result=split_result,
        )

    if str(config.get("model_type") or "") == "dummy":
        return _train_pure_dummy_model(
            dataset=dataset,
            feature_rows=feature_rows,
            labels=labels,
            output_path=output_path,
            model_id=model_id,
            training_run_id=training_run_id,
            started_at=now,
            split_result=split_result,
            config=config,
        )

    estimators = _load_sklearn_estimators()
    if estimators is None:
        error = "scikit-learn is not installed; install the surrogate dependency group."
        return BaselineTrainingResult(
            training_run=_training_run(
                training_run_id=training_run_id,
                model_id=model_id,
                dataset=dataset,
                status="failed",
                started_at=now,
                warnings=[error],
                error_summary=error,
            ),
            model_card=None,
            split_result=split_result,
        )

    feature_names = _feature_names(feature_rows)
    x = [_feature_vector(row, feature_names) for row in feature_rows]
    y = [_label_value(label) for label in labels]
    model_type, model = _make_model(estimators, dataset, len(y), config)
    dummy_model_type, dummy_model = _make_dummy_model(estimators, dataset, config)
    model.fit(x, y)
    dummy_model.fit(x, y)

    estimates = _predict_estimates(model, x, dataset.endpoint.label_type)
    dummy_estimates = _predict_estimates(dummy_model, x, dataset.endpoint.label_type)
    metrics = _metrics(dataset.endpoint.label_type, y, estimates, dummy_estimates)
    metrics.update(
        {
            "label_type": dataset.endpoint.label_type,
            "model_type": model_type,
            "dummy_model_type": dummy_model_type,
            "row_count": len(y),
            "feature_count": len(feature_names),
        }
    )
    calibration_metrics = {"status": "uncalibrated"}

    model_card = _model_card(
        model_id=model_id,
        dataset=dataset,
        model_type=model_type,
        metrics=metrics,
        calibration_metrics=calibration_metrics,
        feature_names=feature_names,
        config=config,
    )
    output_path.mkdir(parents=True, exist_ok=True)
    model_card_path = output_path / f"{model_id}_model_card.json"
    model_artifact_path = output_path / f"{model_id}_model_artifact.pkl"
    feature_schema_path = output_path / f"{model_id}_feature_schema.json"
    prediction_artifact_path = output_path / f"{model_id}_prediction_artifact.json"

    model_card_path.write_text(model_card.model_dump_json(indent=2) + "\n")
    feature_schema_path.write_text(
        json.dumps(
            {
                "feature_spec": dataset.feature_spec.model_dump(mode="json"),
                "feature_names": feature_names,
                "normalization": dataset.feature_spec.normalization,
                "deterministic": True,
                "label_columns_excluded": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with model_artifact_path.open("wb") as artifact_file:
        pickle.dump(
            {
                "model": model,
                "model_card": model_card.model_dump(mode="json"),
                "feature_names": feature_names,
                "split_metadata": split_result.assignments,
            },
            artifact_file,
        )
    predictions = _prediction_artifact_predictions(
        model_card=model_card,
        feature_rows=feature_rows,
        estimates=estimates,
    )
    prediction_artifact_path.write_text(
        json.dumps(
            {
                "artifact_type": "ModelPredictionArtifact",
                "model_id": model_card.model_id,
                "endpoint_id": dataset.endpoint.endpoint_id,
                "predictions": [prediction.model_dump(mode="json") for prediction in predictions],
                "warnings": [PREDICTION_WARNING, ASSAY_RESULT_WARNING],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    training_run = _training_run(
        training_run_id=training_run_id,
        model_id=model_id,
        dataset=dataset,
        status="succeeded",
        started_at=now,
        metrics=metrics,
        calibration_metrics=calibration_metrics,
        artifact_paths={
            "model_card": str(model_card_path),
            "model_artifact": str(model_artifact_path),
            "feature_schema": str(feature_schema_path),
            "split_assignment": str(split_result.assignment_path or ""),
            "prediction_artifact": str(prediction_artifact_path),
        },
        warnings=[PREDICTION_WARNING, ASSAY_RESULT_WARNING],
        metadata={
            "not_experimental_evidence": True,
            "not_assay_result": True,
            "leakage_check_report": split_result.leakage_check_report,
        },
    )
    training_run = _with_standard_report_artifacts(
        output_path=output_path,
        dataset=dataset,
        training_run=training_run,
        model_card=model_card,
        predictions=predictions,
        split_result=split_result,
        prediction_batch_artifact_id=prediction_artifact_path.name,
    )
    return BaselineTrainingResult(
        training_run=training_run,
        model_card=model_card,
        split_result=split_result,
        model_card_path=model_card_path,
        model_artifact_path=model_artifact_path,
        prediction_artifact_path=prediction_artifact_path,
    )


def _train_pure_dummy_model(
    *,
    dataset: ModelTrainingDataset,
    feature_rows: Sequence[Mapping[str, Any]],
    labels: Sequence[Any],
    output_path: Path,
    model_id: str,
    training_run_id: str,
    started_at: datetime,
    split_result: ModelSplitResult,
    config: Mapping[str, Any],
) -> BaselineTrainingResult:
    feature_names = _feature_names(feature_rows)
    y = [_label_value(label) for label in labels]
    estimates = _dummy_estimates(dataset.endpoint.label_type, y)
    metrics = _metrics(dataset.endpoint.label_type, y, estimates, estimates)
    model_type = (
        "DummyRegressor"
        if dataset.endpoint.label_type == "regression"
        else "DummyClassifier"
    )
    metrics.update(
        {
            "label_type": dataset.endpoint.label_type,
            "model_type": model_type,
            "dummy_model_type": model_type,
            "row_count": len(y),
            "feature_count": len(feature_names),
            "pure_python_dummy": True,
        }
    )
    calibration_metrics = {"status": "not_applicable"}
    model_card = _model_card(
        model_id=model_id,
        dataset=dataset,
        model_type=model_type,
        metrics=metrics,
        calibration_metrics=calibration_metrics,
        feature_names=feature_names,
        config=config,
    )
    output_path.mkdir(parents=True, exist_ok=True)
    model_card_path = output_path / f"{model_id}_model_card.json"
    model_artifact_path = output_path / f"{model_id}_model_artifact.pkl"
    feature_schema_path = output_path / f"{model_id}_feature_schema.json"
    prediction_artifact_path = output_path / f"{model_id}_prediction_artifact.json"
    model_card_path.write_text(model_card.model_dump_json(indent=2) + "\n")
    feature_schema_path.write_text(
        json.dumps(
            {
                "feature_spec": dataset.feature_spec.model_dump(mode="json"),
                "feature_names": feature_names,
                "normalization": dataset.feature_spec.normalization,
                "deterministic": True,
                "label_columns_excluded": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with model_artifact_path.open("wb") as artifact_file:
        pickle.dump(
            {
                "model_type": model_type,
                "constant_estimate": estimates[0] if estimates else 0.0,
                "model_card": model_card.model_dump(mode="json"),
                "feature_names": feature_names,
                "split_metadata": split_result.assignments,
            },
            artifact_file,
        )
    predictions = _prediction_artifact_predictions(
        model_card=model_card,
        feature_rows=feature_rows,
        estimates=estimates,
    )
    prediction_artifact_path.write_text(
        json.dumps(
            {
                "artifact_type": "ModelPredictionArtifact",
                "model_id": model_card.model_id,
                "endpoint_id": dataset.endpoint.endpoint_id,
                "predictions": [prediction.model_dump(mode="json") for prediction in predictions],
                "warnings": [PREDICTION_WARNING, ASSAY_RESULT_WARNING],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    training_run = _training_run(
        training_run_id=training_run_id,
        model_id=model_id,
        dataset=dataset,
        status="succeeded",
        started_at=started_at,
        metrics=metrics,
        calibration_metrics=calibration_metrics,
        artifact_paths={
            "model_card": str(model_card_path),
            "model_artifact": str(model_artifact_path),
            "feature_schema": str(feature_schema_path),
            "split_assignment": str(split_result.assignment_path or ""),
            "prediction_artifact": str(prediction_artifact_path),
        },
        warnings=[PREDICTION_WARNING, ASSAY_RESULT_WARNING],
        metadata={
            "not_experimental_evidence": True,
            "not_assay_result": True,
            "leakage_check_report": split_result.leakage_check_report,
        },
    )
    training_run = _with_standard_report_artifacts(
        output_path=output_path,
        dataset=dataset,
        training_run=training_run,
        model_card=model_card,
        predictions=predictions,
        split_result=split_result,
        prediction_batch_artifact_id=prediction_artifact_path.name,
    )
    return BaselineTrainingResult(
        training_run=training_run,
        model_card=model_card,
        split_result=split_result,
        model_card_path=model_card_path,
        model_artifact_path=model_artifact_path,
        prediction_artifact_path=prediction_artifact_path,
    )


def _readiness_failure(
    dataset: ModelTrainingDataset,
    labels: Sequence[Any],
    config: Mapping[str, Any],
) -> str | None:
    if dataset.endpoint.label_type == "binary":
        min_rows = int(config.get("min_training_rows_binary", 8) or 8)
        min_positive = int(config.get("min_positive_count", 1) or 1)
        min_negative = int(config.get("min_negative_count", 1) or 1)
        positive_count = dataset.positive_count if dataset.positive_count is not None else sum(
            1 for label in labels if _label_value(label) == 1.0
        )
        negative_count = dataset.negative_count if dataset.negative_count is not None else sum(
            1 for label in labels if _label_value(label) == 0.0
        )
        if len(labels) < min_rows or dataset.row_count < min_rows:
            return f"Insufficient binary training rows: {len(labels)} < {min_rows}."
        if positive_count < min_positive:
            return f"Insufficient positive labels: {positive_count} < {min_positive}."
        if negative_count < min_negative:
            return f"Insufficient negative labels: {negative_count} < {min_negative}."
    elif dataset.endpoint.label_type == "regression":
        min_rows = int(config.get("min_training_rows_regression", 8) or 8)
        if len(labels) < min_rows or dataset.row_count < min_rows:
            return f"Insufficient regression training rows: {len(labels)} < {min_rows}."
    else:
        return f"Unsupported local baseline label type: {dataset.endpoint.label_type}."
    return None


def _pooling_failure(dataset: ModelTrainingDataset, config: Mapping[str, Any]) -> str | None:
    pooled_ids = dataset.metadata.get("pooled_endpoint_ids")
    if not pooled_ids:
        return None
    if bool(config.get("allow_endpoint_pooling", False)) and dataset.metadata.get(
        "pooled_endpoint_label"
    ):
        return None
    return "Refusing to train pooled unrelated endpoints without an explicit pooled label."


def _make_model(
    estimators: _Estimators,
    dataset: ModelTrainingDataset,
    row_count: int,
    config: Mapping[str, Any],
) -> tuple[str, Any]:
    requested = str(config.get("model_type") or "auto")
    seed = int(config.get("random_seed", 0) or 0)
    if dataset.endpoint.label_type == "regression":
        if requested == "dummy":
            return "DummyRegressor", estimators.DummyRegressor(strategy="mean")
        return "RandomForestRegressor", estimators.RandomForestRegressor(random_state=seed)
    if requested == "dummy":
        return (
            "DummyClassifier",
            estimators.DummyClassifier(strategy="most_frequent", random_state=seed),
        )
    if requested == "random_forest":
        return "RandomForestClassifier", estimators.RandomForestClassifier(random_state=seed)
    if requested == "logistic_regression" or row_count < int(config.get("small_binary_rows", 16)):
        return "LogisticRegression", estimators.LogisticRegression(max_iter=1000, random_state=seed)
    return "RandomForestClassifier", estimators.RandomForestClassifier(random_state=seed)


def _make_dummy_model(
    estimators: _Estimators,
    dataset: ModelTrainingDataset,
    config: Mapping[str, Any],
) -> tuple[str, Any]:
    seed = int(config.get("random_seed", 0) or 0)
    if dataset.endpoint.label_type == "regression":
        return "DummyRegressor", estimators.DummyRegressor(strategy="mean")
    return (
        "DummyClassifier",
        estimators.DummyClassifier(strategy="most_frequent", random_state=seed),
    )


def _model_card(
    *,
    model_id: str,
    dataset: ModelTrainingDataset,
    model_type: str,
    metrics: Mapping[str, Any],
    calibration_metrics: Mapping[str, Any],
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> ModelCard:
    return ModelCard(
        model_id=model_id,
        model_name=f"{dataset.endpoint.endpoint_name} local surrogate",
        model_version="1.2.0",
        plugin_name="local_sklearn_baseline",
        endpoint=dataset.endpoint,
        feature_spec=dataset.feature_spec,
        training_dataset_id=dataset.dataset_id,
        training_data_summary={
            "row_count": dataset.row_count,
            "positive_count": dataset.positive_count,
            "negative_count": dataset.negative_count,
            "source_result_count": len(dataset.source_result_ids),
        },
        model_type=model_type,
        intended_use="Assay-specific prioritization and active-design ranking only.",
        limitations=[
            PREDICTION_WARNING,
            ASSAY_RESULT_WARNING,
            "Generated molecules still require exact imported experimental results for evidence.",
        ],
        metrics=dict(metrics),
        calibration_metrics=dict(calibration_metrics),
        applicability_domain_method=str(
            config.get("applicability_domain_method") or "feature_space"
        ),
        license=None,
        created_at=datetime.now(UTC),
        created_by=_optional_string(config.get("created_by")),
        metadata={
            "feature_names": list(feature_names),
            "not_experimental_evidence": True,
            "not_assay_result": True,
            "endpoint_specific": True,
        },
    )


def _training_run(
    *,
    training_run_id: str,
    model_id: str,
    dataset: ModelTrainingDataset,
    status: str,
    started_at: datetime,
    metrics: Mapping[str, Any] | None = None,
    calibration_metrics: Mapping[str, Any] | None = None,
    artifact_paths: Mapping[str, str] | None = None,
    warnings: Sequence[str] | None = None,
    error_summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelTrainingRun:
    return ModelTrainingRun(
        training_run_id=training_run_id,
        model_id=model_id,
        dataset_id=dataset.dataset_id,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        completed_at=datetime.now(UTC),
        metrics=dict(metrics or {}),
        calibration_metrics=dict(calibration_metrics or {}),
        artifact_paths=dict(artifact_paths or {}),
        warnings=list(warnings or []),
        error_summary=error_summary,
        metadata={
            "not_experimental_evidence": True,
            "not_assay_result": True,
            **dict(metadata or {}),
        },
    )


def _with_standard_report_artifacts(
    *,
    output_path: Path,
    dataset: ModelTrainingDataset,
    training_run: ModelTrainingRun,
    model_card: ModelCard,
    predictions: Sequence[ModelPrediction],
    split_result: ModelSplitResult,
    prediction_batch_artifact_id: str,
) -> ModelTrainingRun:
    report_paths = write_model_report_artifacts(
        output_dir=output_path,
        dataset=dataset,
        training_run=training_run,
        model_card=model_card,
        predictions=predictions,
        split_result=split_result,
        prediction_batch_artifact_id=prediction_batch_artifact_id,
    )
    return training_run.model_copy(
        update={
            "artifact_paths": {
                **training_run.artifact_paths,
                **{key: str(path) for key, path in report_paths.items()},
            }
        }
    )


def _feature_names(feature_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in feature_rows:
        features = row.get("features")
        if isinstance(features, Mapping):
            names.extend(str(name) for name in features)
    return list(dict.fromkeys(names))


def _feature_vector(row: Mapping[str, Any], feature_names: Sequence[str]) -> list[float]:
    features = row.get("features")
    feature_mapping = features if isinstance(features, Mapping) else {}
    return [_label_value(feature_mapping.get(name)) for name in feature_names]


def _metrics(
    label_type: str,
    labels: Sequence[float],
    estimates: Sequence[float],
    dummy_estimates: Sequence[float],
) -> dict[str, Any]:
    if label_type == "binary":
        predictions = [1.0 if estimate >= 0.5 else 0.0 for estimate in estimates]
        dummy_predictions = [1.0 if estimate >= 0.5 else 0.0 for estimate in dummy_estimates]
        return {
            "accuracy": _accuracy(labels, predictions),
            "dummy_accuracy": _accuracy(labels, dummy_predictions),
        }
    return {
        "mean_squared_error": _mean_squared_error(labels, estimates),
        "dummy_mean_squared_error": _mean_squared_error(labels, dummy_estimates),
    }


def _dummy_estimates(label_type: str, labels: Sequence[float]) -> list[float]:
    if not labels:
        return []
    if label_type == "binary":
        positive = sum(1 for label in labels if label >= 0.5)
        estimate = 1.0 if positive >= len(labels) - positive else 0.0
        return [estimate for _label in labels]
    estimate = sum(labels) / len(labels)
    return [estimate for _label in labels]


def _prediction_artifact_predictions(
    *,
    model_card: ModelCard,
    feature_rows: Sequence[Mapping[str, Any]],
    estimates: Sequence[float],
) -> list[ModelPrediction]:
    predictions: list[ModelPrediction] = []
    for index, row in enumerate(feature_rows):
        estimate = float(estimates[index]) if index < len(estimates) else 0.0
        if model_card.endpoint.label_type == "binary":
            probability = _clamp(estimate)
            predicted_value: float | str | bool | None = probability >= 0.5
            prediction_label = "surrogate_positive" if probability >= 0.5 else "surrogate_negative"
        else:
            probability = None
            predicted_value = estimate
            prediction_label = "surrogate_regression_estimate"
        predictions.append(
            ModelPrediction(
                prediction_id=_stable_id(
                    "prediction",
                    model_card.model_id,
                    str(row.get("candidate_id") or row.get("row_id") or index),
                ),
                model_id=model_card.model_id,
                model_version=model_card.model_version,
                endpoint_id=model_card.endpoint.endpoint_id,
                candidate_id=_optional_string(row.get("candidate_id")),
                candidate_name=str(
                    row.get("candidate_name") or row.get("candidate_id") or "unknown"
                ),
                candidate_origin=_candidate_origin(row.get("candidate_origin")),
                canonical_smiles=_optional_string(row.get("canonical_smiles")),
                inchi_key=_optional_string(row.get("inchi_key")),
                predicted_value=predicted_value,
                predicted_probability=probability,
                prediction_label=prediction_label,
                uncertainty=0.5,
                confidence=0.5,
                applicability_domain="unknown",
                calibration_status="uncalibrated",
                explanation="Computational surrogate prediction artifact only.",
                warnings=[PREDICTION_WARNING, ASSAY_RESULT_WARNING],
                created_at=datetime.now(UTC),
                metadata={
                    "not_experimental_evidence": True,
                    "not_assay_result": True,
                    "not_evidence_item": True,
                },
            )
        )
    return predictions


def _predict_estimates(model: Any, x: list[list[float]], label_type: str) -> list[float]:
    if label_type == "binary" and hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(x)
        return [float(row[-1]) for row in probabilities]
    raw_predictions = model.predict(x)
    return [float(value) for value in raw_predictions]


def _accuracy(labels: Sequence[float], predictions: Sequence[float]) -> float:
    if not labels:
        return 0.0
    correct = sum(
        1 for label, prediction in zip(labels, predictions, strict=False) if label == prediction
    )
    return correct / len(labels)


def _mean_squared_error(labels: Sequence[float], estimates: Sequence[float]) -> float:
    if not labels:
        return 0.0
    squared_error = sum(
        (label - estimate) ** 2 for label, estimate in zip(labels, estimates, strict=False)
    )
    return squared_error / len(labels)


def _label_value(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _candidate_origin(value: Any) -> CandidateOrigin:
    origin = str(value or "unknown")
    if origin == "existing":
        return "existing"
    if origin == "generated":
        return "generated"
    return "unknown"


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _stable_id(prefix: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "::".join([prefix, *parts])))


def _load_sklearn_estimators() -> _Estimators | None:
    try:
        ensemble = import_module("sklearn.ensemble")
        linear_model = import_module("sklearn.linear_model")
        dummy = import_module("sklearn.dummy")
    except Exception:
        return None
    return _Estimators(
        RandomForestClassifier=ensemble.RandomForestClassifier,
        RandomForestRegressor=ensemble.RandomForestRegressor,
        LogisticRegression=linear_model.LogisticRegression,
        DummyClassifier=dummy.DummyClassifier,
        DummyRegressor=dummy.DummyRegressor,
    )


__all__ = [
    "BaselineTrainingResult",
    "ModelCard",
    "ModelPrediction",
    "ModelTrainingRun",
    "train_baseline_surrogate_model",
]
