from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import ArtifactInput
from molecule_ranker.evaluation.metrics import (
    budget_utilization,
    learning_value_realized,
    stop_trigger_accuracy,
)
from molecule_ranker.evaluation.schemas import BenchmarkDataset, EvaluationMetric, EvaluationReport


def evaluate_campaign(
    *,
    campaign_artifacts: Mapping[str, ArtifactInput],
    imported_outcome_labels: Mapping[str, ArtifactInput],
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "campaign_planning",
    dataset_id: str = "campaign-evaluation-dataset",
) -> EvaluationReport:
    work_packages, summary = _work_packages(campaign_artifacts)
    labels, failed_qc_count = _label_index(imported_outcome_labels)
    metrics = [
        _work_package_completion_rate(work_packages),
        _replan_trigger_precision(work_packages),
        budget_utilization(
            _as_float(summary.get("budget_spent")) or 0.0,
            _as_float(summary.get("budget_allocated")) or 0.0,
        ),
        _assay_slot_efficiency(work_packages, labels, summary),
        learning_value_realized(
            _as_float(summary.get("realized_learning_value")) or 0.0,
            _as_float(summary.get("expected_learning_value")) or 0.0,
        ),
        _failed_qc_handling(failed_qc_count),
        _renamed(
            stop_trigger_accuracy(
                [_truthy(row.get("predicted_stop")) for row in work_packages],
                [_truthy(row.get("actual_should_stop")) for row in work_packages],
            ),
            "stop_continue_decision_quality",
        ),
        _review_gate_outcome_alignment(work_packages, labels),
    ]
    warnings = []
    if not labels:
        warnings.append("no_imported_or_fixture_outcome_labels")
    if failed_qc_count:
        warnings.append("failed_qc_labels_excluded")
    dataset = _benchmark_dataset(dataset_id, work_packages)
    report = EvaluationReport(
        evaluation_id=evaluation_id or "campaign-evaluation",
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
            "campaign_completion_is_not_scientific_success",
            "Imported outcomes are required for campaign hit metrics.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "work_package_count": len(work_packages),
            "rules": {
                "completion_is_scientific_success": False,
                "imported_outcomes_required_for_hit_metrics": True,
            },
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _work_packages(
    artifacts: Mapping[str, ArtifactInput],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for artifact in artifacts.values():
        if not isinstance(artifact, Mapping):
            continue
        for key in (
            "budget_spent",
            "budget_allocated",
            "expected_learning_value",
            "realized_learning_value",
            "assay_slots",
        ):
            if key in artifact:
                summary[key] = artifact[key]
        for field in ("work_packages", "candidate_batches", "stage_gates"):
            value = artifact.get(field)
            if isinstance(value, list):
                rows.extend(dict(item) for item in value if isinstance(item, Mapping))
    return rows, summary


def _work_package_completion_rate(work_packages: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not work_packages:
        return _undefined("work_package_completion_rate", "decision_quality", "no_work_packages")
    completed = sum(
        str(row.get("status") or "").lower() in {"completed", "done"}
        for row in work_packages
    )
    return _metric(
        "work_package_completion_rate",
        "decision_quality",
        completed / len(work_packages),
    )


def _replan_trigger_precision(work_packages: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    triggered = [row for row in work_packages if _truthy(row.get("replan_triggered"))]
    if not triggered:
        return _undefined("replan_trigger_precision", "decision_quality", "no_replan_triggers")
    useful = sum(_truthy(row.get("replan_was_useful")) for row in triggered)
    return _metric("replan_trigger_precision", "decision_quality", useful / len(triggered))


def _assay_slot_efficiency(
    work_packages: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> EvaluationMetric:
    if not labels:
        return _undefined("assay_slot_efficiency", "cost_efficiency", "no_imported_outcome_labels")
    assay_slots = int(_as_float(summary.get("assay_slots")) or 0)
    if assay_slots <= 0:
        return _undefined("assay_slot_efficiency", "cost_efficiency", "no_assay_slots")
    candidate_ids = {_candidate_id(row) for row in work_packages}
    positive_count = sum(
        _positive_label(label)
        for candidate_id, label in labels.items()
        if candidate_id in candidate_ids
    )
    return _metric("assay_slot_efficiency", "cost_efficiency", positive_count / assay_slots)


def _failed_qc_handling(failed_qc_count: int) -> EvaluationMetric:
    return _metric(
        "failed_QC_handling",
        "decision_quality",
        failed_qc_count >= 0,
        metadata={"failed_qc_exclusion_count": failed_qc_count},
    )


def _review_gate_outcome_alignment(
    work_packages: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
) -> EvaluationMetric:
    paired = [
        (row, labels[_candidate_id(row)])
        for row in work_packages
        if _candidate_id(row) in labels
    ]
    if not paired:
        return _undefined("review_gate_outcome_alignment", "decision_quality", "no_review_outcomes")
    aligned = sum(
        _truthy(row.get("review_gate_approved")) == _positive_label(label)
        for row, label in paired
    )
    return _metric("review_gate_outcome_alignment", "decision_quality", aligned / len(paired))


def _label_index(
    imported_outcome_labels: Mapping[str, ArtifactInput],
) -> tuple[dict[str, dict[str, Any]], int]:
    labels: dict[str, dict[str, Any]] = {}
    failed_qc_count = 0
    for artifact in imported_outcome_labels.values():
        for record in _records(artifact):
            candidate_id = _candidate_id(record)
            if not candidate_id:
                continue
            if str(record.get("qc_status") or "").lower() in {"failed", "fail", "qc_failed"}:
                failed_qc_count += 1
                continue
            labels[candidate_id] = dict(record)
    return labels, failed_qc_count


def _records(artifact: ArtifactInput) -> list[Mapping[str, Any]]:
    if isinstance(artifact, list | tuple):
        return [item for item in artifact if isinstance(item, Mapping)]
    if not isinstance(artifact, Mapping):
        return []
    for field in ("assay_results", "results", "labels", "outcome_labels"):
        value = artifact.get(field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _benchmark_dataset(dataset_id: str, rows: Sequence[Mapping[str, Any]]) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id=dataset_id,
        name="Campaign evaluation dataset",
        dataset_type="frozen_project_artifacts",
        source_artifact_ids=[],
        row_count=len(rows),
        candidate_count=len({_candidate_id(row) for row in rows}),
        label_count=None,
        created_at=datetime.now(UTC),
        data_contract_version="data-contracts.v1",
        metadata={
            "task_type": "campaign_planning",
            "rows": [
                {
                    "row_id": f"campaign:{index}",
                    "entity_id": row.get("work_package_id") or _candidate_id(row),
                    "candidate_id": _candidate_id(row),
                    "record": dict(row),
                    "labels": [],
                }
                for index, row in enumerate(rows)
            ],
        },
    )


def _candidate_id(row: Mapping[str, Any]) -> str:
    for field in ("candidate_id", "generated_id", "molecule_id", "compound_id"):
        if row.get(field):
            return str(row[field])
    return ""


def _positive_label(label: Mapping[str, Any]) -> bool:
    value = label.get("outcome_label") or label.get("label") or label.get("status")
    return str(value).strip().lower() in {
        "positive",
        "active",
        "hit",
        "supported",
        "pass",
        "passed",
    }


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


def _renamed(metric: EvaluationMetric, name: str) -> EvaluationMetric:
    return metric.model_copy(update={"metric_id": name, "name": name})


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


__all__ = ["evaluate_campaign"]
