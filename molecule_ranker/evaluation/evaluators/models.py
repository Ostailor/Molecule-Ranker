from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import ArtifactInput
from molecule_ranker.evaluation.metrics import (
    brier_score,
    expected_calibration_error,
    mae,
    pearson,
    pr_auc,
    r2,
    rmse,
    roc_auc,
    spearman,
)
from molecule_ranker.evaluation.schemas import BenchmarkDataset, EvaluationMetric, EvaluationReport

ModelTaskKind = Literal["classification", "regression"]


def evaluate_model_suite(
    *,
    prediction_artifacts: Mapping[str, ArtifactInput],
    imported_outcome_labels: Mapping[str, ArtifactInput],
    task_kind: ModelTaskKind,
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "surrogate_prediction",
    dataset_id: str = "model-evaluation-dataset",
) -> EvaluationReport:
    predictions = _prediction_rows(prediction_artifacts)
    labels = _label_index(imported_outcome_labels)
    paired = _paired_rows(predictions, labels, task_kind=task_kind)
    metrics = _metrics_for_task(paired, task_kind=task_kind)
    metrics.extend(
        [
            _calibration_status_checked(predictions),
            _out_of_domain_warning_precision(predictions),
            _mean_confidence_metric(predictions, origin="generated"),
            _mean_confidence_metric(predictions, origin="existing"),
            _model_influence_metric(paired, task_kind=task_kind),
            _uncalibrated_overclaim_guardrail(predictions),
        ]
    )
    warnings = _warnings(predictions, labels)
    dataset = _benchmark_dataset(dataset_id, predictions, labels)
    report = EvaluationReport(
        evaluation_id=evaluation_id or "model-evaluation-suite",
        suite_id=suite_id,
        task_id=task_id,
        dataset_id=dataset.dataset_id,
        split_id=None,
        prediction_set_id=None,
        metrics=metrics,
        baseline_metrics=[],
        comparisons=[],
        warnings=warnings,
        limitations=[
            "Benchmark results are evaluation artifacts, not biomedical evidence.",
            "Predictions are not evidence.",
            "Calibration status is a model evaluation artifact, not clinical validation.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "prediction_count": len(predictions),
            "paired_label_count": len(paired),
            "task_kind": task_kind,
            "rules": {
                "predictions_are_not_evidence": True,
                "predictions_are_evidence": False,
                "calibration_status_checked": True,
                "uncalibrated_overclaims_fail_guardrail": True,
            },
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _metrics_for_task(
    paired: Sequence[Mapping[str, Any]],
    *,
    task_kind: ModelTaskKind,
) -> list[EvaluationMetric]:
    if task_kind == "classification":
        labels = [int(row["label"]) for row in paired]
        scores = [float(row["score"]) for row in paired]
        return [
            roc_auc(labels, scores),
            pr_auc(labels, scores),
            brier_score(labels, scores),
            expected_calibration_error(labels, scores),
            _domain_classification_metric(paired, "in_domain"),
            _domain_classification_metric(paired, "out_of_domain"),
            _split_classification_metric(paired, "random"),
            _split_classification_metric(paired, "scaffold"),
            _split_classification_metric(paired, "time_based"),
            _scaffold_random_degradation(paired, task_kind="classification"),
        ]
    actual = [float(row["label"]) for row in paired]
    predicted = [float(row["score"]) for row in paired]
    return [
        mae(actual, predicted),
        rmse(actual, predicted),
        r2(actual, predicted),
        spearman(actual, predicted),
        pearson(actual, predicted),
        _domain_regression_metric(paired, "in_domain"),
        _domain_regression_metric(paired, "out_of_domain"),
        _split_regression_metric(paired, "random"),
        _split_regression_metric(paired, "scaffold"),
        _split_regression_metric(paired, "time_based"),
        _scaffold_random_degradation(paired, task_kind="regression"),
    ]


def _prediction_rows(prediction_artifacts: Mapping[str, ArtifactInput]) -> list[dict[str, Any]]:
    rows = []
    for source_name, artifact in prediction_artifacts.items():
        records = _records(artifact, ("predictions", "model_predictions", "records"))
        artifact_id = _artifact_id(source_name, artifact)
        for index, record in enumerate(records):
            row = dict(record)
            row["_source_artifact_id"] = artifact_id
            row["_source_index"] = index
            rows.append(row)
    return rows


def _label_index(imported_outcome_labels: Mapping[str, ArtifactInput]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for source_name, artifact in imported_outcome_labels.items():
        if _is_prediction_source(source_name, artifact):
            continue
        for record in _records(
            artifact,
            ("assay_results", "results", "labels", "outcome_labels", "experimental_results"),
        ):
            if _is_prediction_label(record):
                continue
            normalized = dict(record)
            for key in _match_keys(normalized):
                index[key] = normalized
    return index


def _paired_rows(
    predictions: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    task_kind: ModelTaskKind,
) -> list[dict[str, Any]]:
    paired = []
    for prediction in predictions:
        label = _matched_label(prediction, labels)
        if label is None:
            continue
        score = _prediction_score(prediction, task_kind=task_kind)
        label_value = _label_value(label, task_kind=task_kind)
        if score is None or label_value is None:
            continue
        paired.append({**dict(prediction), "score": score, "label": label_value})
    return paired


def _records(artifact: ArtifactInput, fields: Sequence[str]) -> list[Mapping[str, Any]]:
    if isinstance(artifact, list | tuple):
        return [item for item in artifact if isinstance(item, Mapping)]
    if not isinstance(artifact, Mapping):
        return []
    for field in fields:
        value = artifact.get(field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return [artifact] if any(key in artifact for key in ("candidate_id", "generated_id")) else []


def _artifact_id(source_name: str, artifact: ArtifactInput) -> str:
    if isinstance(artifact, Mapping):
        value = artifact.get("artifact_id") or artifact.get("id")
        if value:
            return str(value)
    return source_name


def _is_prediction_source(source_name: str, artifact: ArtifactInput) -> bool:
    name = source_name.lower()
    if "prediction" in name or "model" in name:
        return True
    return isinstance(artifact, Mapping) and "prediction" in str(
        artifact.get("artifact_type") or ""
    ).lower()


def _is_prediction_label(record: Mapping[str, Any]) -> bool:
    source_type = str(record.get("source_type") or record.get("artifact_type") or "").lower()
    return "prediction" in source_type or bool(
        record.get("model_id") or record.get("model_version")
    )


def _matched_label(
    prediction: Mapping[str, Any],
    labels: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for key in _match_keys(prediction):
        if key in labels:
            return labels[key]
    return None


def _match_keys(record: Mapping[str, Any]) -> list[str]:
    keys = []
    for field in ("candidate_id", "generated_id", "molecule_id", "compound_id"):
        if record.get(field):
            keys.append(f"{field}:{record[field]}")
    for field in ("inchi_key", "inchikey", "canonical_smiles"):
        if record.get(field):
            keys.append(f"{field}:{record[field]}")
    return keys


def _prediction_score(prediction: Mapping[str, Any], *, task_kind: ModelTaskKind) -> float | None:
    fields = (
        ("predicted_value", "prediction", "regression_prediction", "score")
        if task_kind == "regression"
        else ("prediction_score", "predicted_probability", "probability", "score")
    )
    for field in fields:
        value = _as_float(prediction.get(field))
        if value is not None:
            return value
    return None


def _label_value(label: Mapping[str, Any], *, task_kind: ModelTaskKind) -> float | int | None:
    if task_kind == "regression":
        for field in ("measured_value", "value", "label"):
            value = _as_float(label.get(field))
            if value is not None:
                return value
        return None
    for field in ("outcome_label", "label", "status", "supported"):
        value = label.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            return 1 if value else 0
        normalized = str(value).strip().lower()
        if normalized in {"positive", "active", "supported", "hit", "pass", "passed", "true"}:
            return 1
        if normalized in {"negative", "inactive", "unsupported", "false", "fail", "failed"}:
            return 0
    return None


def _domain_classification_metric(
    paired: Sequence[Mapping[str, Any]],
    domain: str,
) -> EvaluationMetric:
    rows = [row for row in paired if str(row.get("applicability_domain")) == domain]
    metric = roc_auc([int(row["label"]) for row in rows], [float(row["score"]) for row in rows])
    return _renamed(metric, f"{domain}_roc_auc")


def _domain_regression_metric(
    paired: Sequence[Mapping[str, Any]],
    domain: str,
) -> EvaluationMetric:
    rows = [row for row in paired if str(row.get("applicability_domain")) == domain]
    metric = mae([float(row["label"]) for row in rows], [float(row["score"]) for row in rows])
    return _renamed(metric, f"{domain}_mae")


def _split_classification_metric(
    paired: Sequence[Mapping[str, Any]],
    split_type: str,
) -> EvaluationMetric:
    rows = [row for row in paired if str(row.get("split_type")) == split_type]
    metric = roc_auc([int(row["label"]) for row in rows], [float(row["score"]) for row in rows])
    return _renamed(metric, f"{_split_label(split_type)}_split_roc_auc")


def _split_regression_metric(
    paired: Sequence[Mapping[str, Any]],
    split_type: str,
) -> EvaluationMetric:
    rows = [row for row in paired if str(row.get("split_type")) == split_type]
    metric = mae([float(row["label"]) for row in rows], [float(row["score"]) for row in rows])
    return _renamed(metric, f"{_split_label(split_type)}_split_mae")


def _split_label(split_type: str) -> str:
    return "time" if split_type == "time_based" else split_type


def _scaffold_random_degradation(
    paired: Sequence[Mapping[str, Any]],
    *,
    task_kind: ModelTaskKind,
) -> EvaluationMetric:
    if task_kind == "classification":
        random_metric = _split_classification_metric(paired, "random")
        scaffold_metric = _split_classification_metric(paired, "scaffold")
        metric_type = "classification"
    else:
        random_metric = _split_regression_metric(paired, "random")
        scaffold_metric = _split_regression_metric(paired, "scaffold")
        metric_type = "regression"
    if random_metric.value is None or scaffold_metric.value is None:
        return _undefined(
            "scaffold_vs_random_split_degradation",
            metric_type,
            "random_or_scaffold_split_metric_undefined",
            higher_is_better=False,
        )
    random_value = float(random_metric.value)
    scaffold_value = float(scaffold_metric.value)
    degradation = (
        random_value - scaffold_value
        if task_kind == "classification"
        else scaffold_value - random_value
    )
    return _metric(
        "scaffold_vs_random_split_degradation",
        metric_type,
        degradation,
        higher_is_better=False,
    )


def _calibration_status_checked(predictions: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    checked = bool(predictions) and all(
        prediction.get("calibration_status") for prediction in predictions
    )
    return _metric(
        "calibration_status_checked",
        "calibration",
        checked,
        metadata={"checked_count": sum(1 for row in predictions if row.get("calibration_status"))},
    )


def _out_of_domain_warning_precision(
    predictions: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    warned = [row for row in predictions if _truthy(row.get("out_of_domain_warning"))]
    if not warned:
        return _undefined(
            "out_of_domain_warning_precision",
            "decision_quality",
            "no_out_of_domain_warnings",
        )
    correct = [
        row
        for row in warned
        if str(row.get("applicability_domain") or "").lower() == "out_of_domain"
    ]
    return _metric(
        "out_of_domain_warning_precision",
        "decision_quality",
        len(correct) / len(warned),
        metadata={"warning_count": len(warned)},
    )


def _mean_confidence_metric(
    predictions: Sequence[Mapping[str, Any]],
    *,
    origin: str,
) -> EvaluationMetric:
    values = [
        confidence
        for row in predictions
        if str(row.get("candidate_origin") or "existing").lower() == origin
        and (confidence := _as_float(row.get("confidence"))) is not None
    ]
    if not values:
        return _undefined(f"{origin}_mean_confidence", "calibration", "no_confidence_values")
    return _metric(
        f"{origin}_mean_confidence",
        "calibration",
        sum(values) / len(values),
        metadata={"sample_count": len(values)},
    )


def _model_influence_metric(
    paired: Sequence[Mapping[str, Any]],
    *,
    task_kind: ModelTaskKind,
) -> EvaluationMetric:
    influenced = [
        row
        for row in paired
        if _truthy(row.get("active_design_selected"))
        and _truthy(row.get("model_influenced_decision"))
    ]
    if not influenced:
        return _undefined(
            "model_influenced_active_design_hit_rate",
            "decision_quality",
            "no_model_influenced_active_design_rows",
        )
    if task_kind == "regression":
        positives = sum(1 for row in influenced if float(row["label"]) > 0)
    else:
        positives = sum(1 for row in influenced if int(row["label"]) == 1)
    return _metric(
        "model_influenced_active_design_hit_rate",
        "decision_quality",
        positives / len(influenced),
        metadata={"sample_count": len(influenced)},
    )


def _uncalibrated_overclaim_guardrail(
    predictions: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    offenders = [
        _prediction_id(row)
        for row in predictions
        if str(row.get("calibration_status") or "").lower() != "calibrated"
        and (_as_float(row.get("confidence")) or 0.0) >= 0.8
        and _overclaim_text(row)
    ]
    return EvaluationMetric(
        metric_id="uncalibrated_prediction_overclaim_guardrail",
        name="uncalibrated_prediction_overclaim_guardrail",
        metric_type="guardrail",
        value=not offenders,
        higher_is_better=True,
        metadata={"status": "computed", "offending_prediction_ids": offenders},
    )


def _overclaim_text(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(row.get(field) or "")
        for field in ("summary_claim", "claim", "decision_summary", "rationale")
    ).lower()
    return any(term in text for term in ("high-confidence", "confident", "active", "hit"))


def _warnings(
    predictions: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    warnings = []
    if not labels:
        warnings.append("no_imported_or_fixture_outcome_labels")
    if any(
        metric_id
        for metric_id in _uncalibrated_overclaim_guardrail(predictions).metadata.get(
            "offending_prediction_ids", []
        )
    ):
        warnings.append("uncalibrated_prediction_overclaim")
    return sorted(warnings)


def _benchmark_dataset(
    dataset_id: str,
    predictions: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id=dataset_id,
        name="Model evaluation suite dataset",
        dataset_type="frozen_project_artifacts",
        source_artifact_ids=sorted(
            {
                str(row.get("_source_artifact_id"))
                for row in predictions
                if row.get("_source_artifact_id")
            }
        ),
        row_count=len(predictions),
        candidate_count=len({_prediction_id(row) for row in predictions}),
        label_count=len(labels),
        created_at=datetime.now(UTC),
        data_contract_version="data-contracts.v1",
        metadata={
            "task_type": "surrogate_prediction",
            "rows": [
                {
                    "row_id": (
                        f"{row.get('_source_artifact_id', 'prediction')}:"
                        f"{row.get('_source_index', index)}"
                    ),
                    "entity_id": _prediction_id(row),
                    "candidate_id": row.get("candidate_id"),
                    "is_generated": str(row.get("candidate_origin") or "").lower() == "generated",
                    "record": dict(row),
                    "labels": [],
                }
                for index, row in enumerate(predictions)
            ],
        },
    )


def _metric(
    name: str,
    metric_type: str,
    value: float | bool,
    *,
    higher_is_better: bool | None = True,
    metadata: Mapping[str, Any] | None = None,
) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,  # type: ignore[arg-type]
        value=value,
        higher_is_better=higher_is_better,
        metadata={"status": "computed", **dict(metadata or {})},
    )


def _undefined(
    name: str,
    metric_type: str,
    reason: str,
    *,
    higher_is_better: bool | None = True,
) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,  # type: ignore[arg-type]
        value=None,
        higher_is_better=higher_is_better,
        metadata={"status": "undefined", "undefined_reason": reason},
    )


def _renamed(metric: EvaluationMetric, name: str) -> EvaluationMetric:
    return metric.model_copy(update={"metric_id": name, "name": name})


def _prediction_id(row: Mapping[str, Any]) -> str:
    for field in ("candidate_id", "generated_id", "molecule_id", "compound_id"):
        value = row.get(field)
        if value:
            return str(value)
    return str(row.get("_source_index") or "prediction")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    return bool(value)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


__all__ = ["evaluate_model_suite"]
