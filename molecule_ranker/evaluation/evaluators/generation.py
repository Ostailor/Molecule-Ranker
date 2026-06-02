from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import (
    ArtifactInput,
    build_generated_molecule_prioritization_dataset,
)
from molecule_ranker.evaluation.metrics import (
    exact_result_hit_rate,
    experiment_readiness_distribution,
    novelty_rate,
    scaffold_diversity,
    uniqueness_rate,
    validity_rate,
)
from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    BenchmarkSplit,
    EvaluationMetric,
    EvaluationMetricType,
    EvaluationReport,
)


def evaluate_generation(
    *,
    generated_molecule_artifacts: Mapping[str, ArtifactInput],
    imported_outcome_labels: Mapping[str, ArtifactInput] | None = None,
    split: BenchmarkSplit | None = None,
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "molecule_generation",
    dataset_id: str | None = None,
    include_failed_qc_labels: bool = False,
) -> EvaluationReport:
    dataset = build_generated_molecule_prioritization_dataset(
        {**generated_molecule_artifacts, **dict(imported_outcome_labels or {})},
        dataset_id=dataset_id or (split.dataset_id if split else None),
        include_failed_qc_labels=include_failed_qc_labels,
        metadata={
            "label_rules": {
                "generated_molecule_validated_only_by_exact_linked_result": True,
                "docking_scores_are_hits": False,
                "model_predictions_are_hits": False,
            },
        },
    )
    rows = _evaluated_rows(dataset, split)
    exact_hit_flags, exact_warnings = _exact_hit_flags(rows)
    sampled_rows = [row for row in rows if _sampled_for_experiment(row)]

    metrics = [
        validity_rate([_valid(row) for row in rows]),
        uniqueness_rate([_identity(row) for row in rows]),
        novelty_rate([_novel(row) for row in rows]),
        scaffold_diversity(_present_strings(_scaffold(row) for row in rows)),
        _rate_metric(
            "developability_pass_rate",
            "generation",
            [_developability_pass(row) for row in rows],
        ),
        _rate_metric(
            "critical_alert_rate",
            "generation",
            [_critical_alert(row) for row in rows],
            higher_is_better=False,
        ),
        _rate_metric(
            "structure_qc_pass_rate",
            "generation",
            [_structure_qc_pass(row) for row in rows],
        ),
        experiment_readiness_distribution(
            _present_strings(_experiment_readiness(row) for row in rows)
        ),
        _distribution_metric(
            "medchem_critique_distribution",
            _present_strings(_medchem_decision(row) for row in rows),
        ),
        _renamed(exact_result_hit_rate(exact_hit_flags), "exact_experimental_hit_rate"),
        _rate_metric(
            "active_learning_sample_efficiency",
            "cost_efficiency",
            [_exact_hit(row) for row in sampled_rows],
        ),
    ]
    warnings = _warnings(dataset, rows, exact_hit_flags, exact_warnings)
    report = EvaluationReport(
        evaluation_id=evaluation_id or "generation-evaluation",
        suite_id=suite_id,
        task_id=task_id,
        dataset_id=dataset.dataset_id,
        split_id=split.split_id if split else None,
        prediction_set_id=None,
        metrics=metrics,
        baseline_metrics=[],
        comparisons=[],
        warnings=warnings,
        limitations=[
            "Benchmark results are evaluation artifacts, not biomedical evidence.",
            "Generated molecules are not considered validated without exact linked results.",
            "Docking scores and model predictions are not experimental hits.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "row_count": len(rows),
            "exact_hit_count": sum(1 for flag in exact_hit_flags if flag),
            "sampled_for_experiment_count": len(sampled_rows),
            "label_rules": {
                "generated_molecule_validated_only_by_exact_linked_result": True,
                "docking_scores_are_hits": False,
                "model_predictions_are_hits": False,
            },
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _evaluated_rows(
    dataset: BenchmarkDataset,
    split: BenchmarkSplit | None,
) -> list[Mapping[str, Any]]:
    rows = _dataset_rows(dataset)
    if split is None:
        return rows
    selected_ids = split.test_ids or split.validation_ids or split.train_ids
    if not selected_ids:
        return rows
    selected = {str(row_id) for row_id in selected_ids}
    return [row for row in rows if _row_id(row) in selected]


def _dataset_rows(dataset: BenchmarkDataset) -> list[Mapping[str, Any]]:
    rows = dataset.metadata.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _exact_hit_flags(rows: Sequence[Mapping[str, Any]]) -> tuple[list[bool], list[str]]:
    flags = []
    warnings = []
    for row in rows:
        hit, mismatched = _exact_hit_and_mismatch(row)
        flags.append(hit)
        if mismatched:
            warnings.append("generated_result_without_exact_structure_match")
    return flags, warnings


def _exact_hit(row: Mapping[str, Any]) -> bool:
    hit, _mismatched = _exact_hit_and_mismatch(row)
    return hit


def _exact_hit_and_mismatch(row: Mapping[str, Any]) -> tuple[bool, bool]:
    mismatched = False
    for label in _labels(row):
        if not _positive_label(label):
            continue
        if _model_prediction_label(label):
            continue
        if _exact_linked_result(row, label):
            return True, mismatched
        mismatched = True
    return False, mismatched


def _exact_linked_result(row: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    record = _record(row)
    generated_id = _first_value(row, record, ("generated_id",))
    label_generated_id = _first_value(label, {}, ("generated_id",))
    if generated_id and label_generated_id and str(generated_id) != str(label_generated_id):
        return False
    row_structure = _structure_key(row)
    label_structure = _structure_key(label)
    if row_structure and label_structure:
        return row_structure == label_structure and bool(generated_id or label_generated_id)
    return bool(
        generated_id
        and label_generated_id
        and str(generated_id) == str(label_generated_id)
    )


def _labels(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    labels = row.get("labels", [])
    if not isinstance(labels, list):
        return []
    return [label for label in labels if isinstance(label, Mapping)]


def _positive_label(label: Mapping[str, Any]) -> bool:
    for key in ("outcome_label", "label", "supported", "result", "status"):
        value = label.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        return normalized in {"positive", "active", "supported", "hit", "pass", "passed", "true"}
    value = label.get("measured_value", label.get("value"))
    numeric = _as_float(value)
    return numeric is not None and numeric > 0


def _model_prediction_label(label: Mapping[str, Any]) -> bool:
    source_type = str(label.get("source_type") or label.get("artifact_type") or "").lower()
    return "prediction" in source_type or bool(label.get("model_id") or label.get("model_version"))


def _valid(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = _first_value(row, record, ("valid", "is_valid", "passes_validity"))
    if value is not None:
        return bool(value)
    return bool(_first_value(row, record, ("canonical_smiles", "inchi_key", "inchikey")))


def _identity(row: Mapping[str, Any]) -> str:
    record = _record(row)
    for key in ("canonical_smiles", "inchi_key", "inchikey", "generated_id", "candidate_id"):
        value = _first_value(row, record, (key,))
        if value not in {None, ""}:
            return str(value)
    return _row_id(row)


def _novel(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = _first_value(row, record, ("is_novel", "novel", "novelty_pass"))
    if value is not None:
        return bool(value)
    return not bool(_first_value(row, record, ("known_reference_id", "existing_candidate_id")))


def _scaffold(row: Mapping[str, Any]) -> str | None:
    record = _record(row)
    value = _first_value(row, record, ("scaffold", "bemis_murcko_scaffold", "scaffold_id"))
    return str(value) if value not in {None, ""} else None


def _developability_pass(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = _first_value(row, record, ("developability_pass", "passes_developability"))
    if value is not None:
        return bool(value)
    score = _as_float(
        _first_value(row, record, ("developability_score", "overall_developability_score"))
    )
    if score is not None:
        return score >= 0.5
    risk = str(_first_value(row, record, ("risk_level", "developability_risk")) or "").lower()
    return risk not in {"high", "severe", "fail", "failed"}


def _critical_alert(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = _first_value(row, record, ("critical_alert", "has_critical_alert"))
    if value is not None:
        return bool(value)
    alerts = _first_value(row, record, ("critical_alerts", "alerts"))
    if isinstance(alerts, Sequence) and not isinstance(alerts, str | bytes):
        return bool(alerts)
    alert_level = str(_first_value(row, record, ("alert_level", "highest_alert")) or "").lower()
    return alert_level in {"critical", "severe", "high"}


def _structure_qc_pass(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = _first_value(row, record, ("structure_qc_pass", "pose_qc_pass"))
    if value is not None:
        return bool(value)
    status = str(
        _first_value(
            row,
            record,
            ("structure_qc_status", "pose_qc", "pose_qc_status", "docking_qc_status"),
        )
        or ""
    ).lower()
    return status in {"pass", "passed", "ok", "valid", "accepted"}


def _experiment_readiness(row: Mapping[str, Any]) -> str | None:
    record = _record(row)
    value = _first_value(
        row,
        record,
        ("experiment_readiness", "experiment_readiness_status", "readiness"),
    )
    return str(value) if value not in {None, ""} else None


def _medchem_decision(row: Mapping[str, Any]) -> str | None:
    record = _record(row)
    value = _first_value(
        row,
        record,
        ("medchem_decision", "medchem_critique", "medchem_recommendation"),
    )
    if value in {None, ""}:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"retain", "retained", "keep"}:
        return "retain"
    if normalized in {"deprioritize", "deprioritized", "defer", "lower_priority"}:
        return "deprioritize"
    if normalized in {"reject", "rejected", "drop"}:
        return "reject"
    return normalized


def _sampled_for_experiment(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    return bool(
        _first_value(
            row,
            record,
            (
                "sampled_for_experiment",
                "active_learning_selected",
                "selected_for_experiment",
            ),
        )
    )


def _warnings(
    dataset: BenchmarkDataset,
    rows: Sequence[Mapping[str, Any]],
    exact_hit_flags: Sequence[bool],
    exact_warnings: Sequence[str],
) -> list[str]:
    warnings = [str(warning) for warning in dataset.metadata.get("warnings", [])]
    warnings.extend(exact_warnings)
    if rows and not any(exact_hit_flags):
        warnings.append("no_exact_experimental_hits")
    if not any(_labels(row) for row in rows):
        warnings.append("no_eligible_outcome_labels")
    return sorted(set(warnings))


def _rate_metric(
    name: str,
    metric_type: EvaluationMetricType,
    flags: Sequence[bool],
    *,
    higher_is_better: bool | None = True,
) -> EvaluationMetric:
    if not flags:
        return EvaluationMetric(
            metric_id=name,
            name=name,
            metric_type=metric_type,
            value=None,
            higher_is_better=higher_is_better,
            metadata={"status": "undefined", "undefined_reason": "no_observations"},
        )
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,
        value=sum(1 for flag in flags if flag) / len(flags),
        higher_is_better=higher_is_better,
        metadata={"status": "computed", "sample_count": len(flags)},
    )


def _present_strings(values: Iterable[str | None]) -> list[str]:
    return [value for value in values if value]


def _distribution_metric(name: str, labels: Sequence[str]) -> EvaluationMetric:
    if not labels:
        return EvaluationMetric(
            metric_id=name,
            name=name,
            metric_type="generation",
            value=None,
            higher_is_better=True,
            metadata={"status": "undefined", "undefined_reason": "no_distribution_labels"},
        )
    distribution = dict(sorted(Counter(labels).items()))
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type="generation",
        value=json.dumps(distribution, sort_keys=True),
        higher_is_better=True,
        metadata={"status": "computed", "distribution": distribution, "sample_count": len(labels)},
    )


def _renamed(metric: EvaluationMetric, name: str) -> EvaluationMetric:
    return metric.model_copy(update={"metric_id": name, "name": name})


def _structure_key(mapping: Mapping[str, Any]) -> str | None:
    record = _record(mapping)
    for key in ("inchi_key", "inchikey", "canonical_smiles"):
        value = _first_value(mapping, record, (key,))
        if value not in {None, ""}:
            return f"{key}:{value}"
    return None


def _row_id(row: Mapping[str, Any]) -> str:
    return str(row.get("row_id") or row.get("entity_id") or row.get("candidate_id"))


def _record(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get("record")
    return record if isinstance(record, Mapping) else {}


def _first_value(
    mapping: Mapping[str, Any],
    nested: Mapping[str, Any],
    keys: Sequence[str],
) -> Any:
    for key in keys:
        if _present(mapping.get(key)):
            return mapping[key]
        if _present(nested.get(key)):
            return nested[key]
    return None


def _present(value: Any) -> bool:
    return value is not None and value != ""


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


__all__ = ["evaluate_generation"]
