"""Predictive model plugin interfaces and base implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from typing import Any, NoReturn, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEvaluationReport,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)

SAFE_FEATURE_FAMILIES = {
    "rdkit_descriptors",
    "morgan_fingerprint",
    "developability",
    "oracle_scores",
    "literature_counts",
    "target_context",
    "generation_metadata",
    "review_context",
}
PREDICTION_WARNING = "Model prediction is not experimental evidence and not an assay result."


@runtime_checkable
class ModelPlugin(Protocol):
    plugin_name: str
    plugin_version: str
    supported_label_types: list[str]
    supported_feature_families: list[str]

    def train(
        self,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        config: dict[str, Any],
    ) -> ModelTrainingRun: ...

    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]: ...

    def evaluate(
        self,
        model_card: ModelCard,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        splits: dict[str, Any],
    ) -> ModelEvaluationReport: ...


class RuleBasedSurrogatePlugin:
    plugin_name = "rule_based_surrogate"
    plugin_version = "1.2.0"
    supported_label_types = ["binary", "regression"]
    supported_feature_families = [
        "rdkit_descriptors",
        "morgan_fingerprint",
        "developability",
        "oracle_scores",
        "literature_counts",
        "target_context",
        "generation_metadata",
        "review_context",
    ]

    def train(
        self,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        config: dict[str, Any],
    ) -> ModelTrainingRun:
        _validate_dataset_support(self, dataset)
        now = datetime.now(UTC)
        model_id = str(config.get("model_id") or _model_id(self.plugin_name, dataset.dataset_id))
        feature_count = _row_count(features)
        label_count = _row_count(labels)
        status = "succeeded" if dataset.row_count > 0 else "skipped_insufficient_data"
        warnings = [PREDICTION_WARNING]
        if status == "skipped_insufficient_data":
            warnings.insert(0, "Insufficient rows for rule-based surrogate training.")
        return ModelTrainingRun(
            training_run_id=_stable_id(
                "training-run",
                model_id,
                dataset.dataset_id,
                now.isoformat(),
            ),
            model_id=model_id,
            dataset_id=dataset.dataset_id,
            status=status,
            started_at=now,
            completed_at=datetime.now(UTC),
            metrics={
                "row_count": dataset.row_count,
                "feature_count": feature_count,
                "label_count": label_count,
                "plugin_name": self.plugin_name,
            },
            calibration_metrics={"status": "not_applicable"},
            artifact_paths={},
            warnings=warnings,
            error_summary=None,
            metadata={
                "not_experimental_evidence": True,
                "not_assay_result": True,
                "creates_evidence_items": False,
                "creates_assay_results": False,
            },
        )

    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]:
        feature_rows = _feature_rows(features, len(candidates))
        predictions = [
            _prediction_from_candidate(
                model_card=model_card,
                candidate=candidate,
                feature_row=feature_rows[index],
                plugin_name=self.plugin_name,
                default_probability=config.get("default_probability"),
            )
            for index, candidate in enumerate(candidates)
        ]
        _assert_prediction_outputs(predictions)
        return predictions

    def evaluate(
        self,
        model_card: ModelCard,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        splits: dict[str, Any],
    ) -> ModelEvaluationReport:
        _validate_dataset_support(self, dataset)
        split_strategy = str(
            splits.get("strategy") or splits.get("split_strategy") or "unspecified"
        )
        label_values = _as_list(labels)
        feature_rows = _feature_rows(features, len(label_values))
        metrics = _simple_metrics(feature_rows, label_values)
        return ModelEvaluationReport(
            evaluation_id=_stable_id("evaluation", model_card.model_id, dataset.dataset_id),
            model_id=model_card.model_id,
            dataset_id=dataset.dataset_id,
            split_strategy=split_strategy,
            metrics=metrics,
            calibration_metrics={"status": "not_applicable"},
            leakage_checks={
                "test_labels_excluded_from_training": True,
                "split_result_ids": {
                    key: value
                    for key, value in splits.items()
                    if key.endswith("_result_ids") or key.endswith("_ids")
                },
            },
            applicability_domain_summary={"method": model_card.applicability_domain_method},
            warnings=[PREDICTION_WARNING],
            created_at=datetime.now(UTC),
            metadata={
                "plugin_name": self.plugin_name,
                "not_experimental_evidence": True,
                "not_assay_result": True,
            },
        )


class SklearnSurrogatePlugin(RuleBasedSurrogatePlugin):
    plugin_name = "sklearn_surrogate"
    plugin_version = "1.2.0"
    supported_label_types = ["binary", "regression", "multiclass"]

    def train(
        self,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        config: dict[str, Any],
    ) -> ModelTrainingRun:
        _ensure_sklearn_available()
        run = super().train(dataset, features, labels, config)
        return run.model_copy(
            update={
                "metrics": {
                    **run.metrics,
                    "model_family": "scikit-learn",
                    "optional_dependency": "scikit-learn",
                }
            }
        )

    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]:
        _ensure_sklearn_available()
        return super().predict(model_card, candidates, features, config)

    def evaluate(
        self,
        model_card: ModelCard,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        splits: dict[str, Any],
    ) -> ModelEvaluationReport:
        _ensure_sklearn_available()
        report = super().evaluate(model_card, dataset, features, labels, splits)
        return report.model_copy(
            update={"metadata": {**report.metadata, "model_family": "scikit-learn"}}
        )


class ExternalModelPluginPlaceholder:
    plugin_name = "external_model_placeholder"
    plugin_version = "1.2.0"
    supported_label_types = ["binary", "regression", "multiclass", "ordinal"]
    supported_feature_families = list(SAFE_FEATURE_FAMILIES)

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def train(
        self,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        config: dict[str, Any],
    ) -> ModelTrainingRun:
        self._raise_disabled()

    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]:
        self._raise_disabled()

    def evaluate(
        self,
        model_card: ModelCard,
        dataset: ModelTrainingDataset,
        features: Any,
        labels: Any,
        splits: dict[str, Any],
    ) -> ModelEvaluationReport:
        self._raise_disabled()

    def _raise_disabled(self) -> NoReturn:
        if not self.enabled:
            raise RuntimeError("External model plugins are disabled by default.")
        raise NotImplementedError("External model provider execution is not implemented.")


def _validate_dataset_support(plugin: ModelPlugin, dataset: ModelTrainingDataset) -> None:
    label_type = dataset.endpoint.label_type
    if label_type not in plugin.supported_label_types:
        raise ValueError(f"Unsupported label type for {plugin.plugin_name}: {label_type}")
    unsupported = [
        family
        for family in dataset.feature_spec.feature_families
        if family not in plugin.supported_feature_families or family not in SAFE_FEATURE_FAMILIES
    ]
    if unsupported:
        raise ValueError(
            f"Unsupported feature families for {plugin.plugin_name}: {', '.join(unsupported)}"
        )


def _prediction_from_candidate(
    *,
    model_card: ModelCard,
    candidate: Any,
    feature_row: dict[str, Any],
    plugin_name: str,
    default_probability: Any,
) -> ModelPrediction:
    candidate_id = _value(candidate, "candidate_id") or _value(candidate, "generated_id")
    candidate_name = str(
        _value(candidate, "candidate_name") or _value(candidate, "name") or "candidate"
    )
    origin = str(_value(candidate, "candidate_origin") or _value(candidate, "origin") or "unknown")
    if origin not in {"existing", "generated", "unknown"}:
        origin = "unknown"
    probability = feature_row.get("predicted_probability", default_probability)
    predicted_value = feature_row.get("predicted_value")
    if predicted_value is None and isinstance(probability, int | float):
        predicted_value = float(probability)
    uncertainty = _bounded_float(feature_row.get("uncertainty"), default=1.0)
    applicability_domain = str(feature_row.get("applicability_domain") or "in_domain")
    if applicability_domain not in {"in_domain", "near_domain", "out_of_domain", "unknown"}:
        applicability_domain = "unknown"
    warnings = [str(item) for item in feature_row.get("warnings", []) if item]
    if PREDICTION_WARNING not in warnings:
        warnings.append(PREDICTION_WARNING)
    return ModelPrediction(
        prediction_id=_stable_id("prediction", model_card.model_id, candidate_id, candidate_name),
        model_id=model_card.model_id,
        model_version=model_card.model_version,
        endpoint_id=model_card.endpoint.endpoint_id,
        candidate_id=str(candidate_id) if candidate_id else None,
        candidate_name=candidate_name,
        candidate_origin=origin,  # type: ignore[arg-type]
        canonical_smiles=_optional_string(_value(candidate, "canonical_smiles")),
        inchi_key=_optional_string(_value(candidate, "inchi_key")),
        predicted_value=predicted_value,
        predicted_probability=_optional_probability(probability),
        prediction_label=_optional_string(feature_row.get("prediction_label"))
        or "surrogate model estimate",
        uncertainty=uncertainty,
        confidence=_bounded_float(feature_row.get("confidence"), default=1.0 - uncertainty),
        applicability_domain=applicability_domain,  # type: ignore[arg-type]
        calibration_status=str(
            feature_row.get("calibration_status")
            or model_card.calibration_metrics.get("status")
            or "not_applicable"
        ),  # type: ignore[arg-type]
        explanation=str(
            feature_row.get("explanation")
            or f"{plugin_name} produced a computational prioritization estimate."
        ),
        warnings=warnings,
        created_at=datetime.now(UTC),
        metadata={
            "plugin_name": plugin_name,
            "not_experimental_evidence": True,
            "not_assay_result": True,
            "creates_evidence_items": False,
            "creates_assay_results": False,
            **dict(feature_row.get("metadata") or {}),
        },
    )


def _assert_prediction_outputs(predictions: list[ModelPrediction]) -> None:
    for prediction in predictions:
        if not isinstance(prediction, ModelPrediction):
            raise TypeError("Model plugins may only emit ModelPrediction objects.")
        if not prediction.model_id or not prediction.endpoint_id:
            raise ValueError("Model predictions require model_id and endpoint_id.")
        if prediction.uncertainty < 0.0 or prediction.uncertainty > 1.0:
            raise ValueError("Model prediction uncertainty must be in [0, 1].")
        if not prediction.applicability_domain:
            raise ValueError("Model predictions require an applicability domain.")
        if not prediction.warnings:
            raise ValueError("Model predictions require warnings.")


def _ensure_sklearn_available() -> None:
    if _load_sklearn() is None:
        raise RuntimeError(
            "scikit-learn is required for SklearnSurrogatePlugin. "
            "Install the optional surrogate dependency group."
        )


def _load_sklearn() -> object | None:
    try:
        return import_module("sklearn")
    except Exception:
        return None


def _feature_rows(features: Any, count: int) -> list[dict[str, Any]]:
    if isinstance(features, list):
        rows = [dict(row) if isinstance(row, dict) else {} for row in features]
    else:
        rows = []
    while len(rows) < count:
        rows.append({})
    return rows[:count]


def _simple_metrics(feature_rows: list[dict[str, Any]], labels: list[Any]) -> dict[str, Any]:
    if not labels:
        return {"row_count": 0}
    predicted = []
    for row in feature_rows:
        probability = _optional_probability(row.get("predicted_probability"))
        predicted.append(1 if (probability or 0.0) >= 0.5 else 0)
    binary_labels = [
        1 if label is True or label in {1, "1", "positive", "active"} else 0
        for label in labels
    ]
    compared = list(zip(predicted, binary_labels, strict=False))
    accuracy = (
        sum(1 for pred, label in compared if pred == label) / len(compared)
        if compared
        else 0.0
    )
    return {"row_count": len(labels), "accuracy": round(accuracy, 6)}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _row_count(value: Any) -> int:
    if isinstance(value, list | tuple):
        return len(value)
    return 0


def _bounded_float(value: Any, *, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(float(value), 1.0))
    return max(0.0, min(default, 1.0))


def _optional_probability(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _bounded_float(value, default=0.0)
    return None


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _model_id(plugin_name: str, dataset_id: str) -> str:
    return _stable_id("model", plugin_name, dataset_id)


def _stable_id(prefix: str, *parts: Any) -> str:
    basis = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{uuid5(NAMESPACE_URL, basis).hex[:16]}"


__all__ = [
    "ExternalModelPluginPlaceholder",
    "ModelPlugin",
    "RuleBasedSurrogatePlugin",
    "SklearnSurrogatePlugin",
]
