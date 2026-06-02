from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import (
    BaselineResult,
    ensure_baseline_comparison,
)
from molecule_ranker.evaluation.datasets import ArtifactInput, build_candidate_ranking_dataset
from molecule_ranker.evaluation.metrics import (
    enrichment_factor_at_k,
    exact_result_hit_rate,
    top_k_hit_rate,
)
from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    BenchmarkSplit,
    EvaluationMetric,
    EvaluationReport,
)

BaselineComparator = BaselineResult | Callable[[BenchmarkDataset], BaselineResult]


def evaluate_candidate_ranking(
    *,
    ranked_candidate_artifacts: Mapping[str, ArtifactInput],
    imported_outcome_labels: Mapping[str, ArtifactInput],
    split: BenchmarkSplit | None = None,
    baseline_comparators: Sequence[BaselineComparator] | None = None,
    top_k: Sequence[int] = (1, 5, 10),
    combined_ranking_enabled: bool = False,
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "candidate_ranking",
    dataset_id: str | None = None,
    include_failed_qc_labels: bool = False,
) -> EvaluationReport:
    dataset = build_candidate_ranking_dataset(
        {**ranked_candidate_artifacts, **imported_outcome_labels},
        dataset_id=dataset_id or split.dataset_id if split else dataset_id,
        include_failed_qc_labels=include_failed_qc_labels,
        metadata={
            "task_type": "candidate_ranking",
            "label_rules": {
                "outcome_labels": "imported_results_or_benchmark_fixtures_only",
                "generated_requires_exact_result_match": True,
                "seed_result_counts_as_generated_success": False,
            },
        },
    )
    rows = _evaluated_rows(dataset, split)
    warnings = _warnings(dataset, rows)
    metrics: list[EvaluationMetric] = []
    generated_seed_label_reused = False

    existing_rows = [row for row in rows if not _is_generated(row)]
    generated_rows = [row for row in rows if _is_generated(row)]
    for scope, scoped_rows in (("existing", existing_rows), ("generated", generated_rows)):
        labels, scores = _labels_and_scores(scoped_rows)
        for k in top_k:
            metrics.append(
                _renamed(
                    top_k_hit_rate(labels, scores, k=k),
                    f"{scope}_top_{k}_hit_rate",
                )
            )

    combined_rows = rows if combined_ranking_enabled else existing_rows
    combined_labels, combined_scores = _labels_and_scores(combined_rows)
    for k in top_k:
        metrics.append(
            _renamed(
                top_k_hit_rate(combined_labels, combined_scores, k=k),
                f"combined_top_{k}_hit_rate",
            )
        )
        metrics.append(
            _renamed(
                enrichment_factor_at_k(combined_labels, combined_scores, k=k),
                f"enrichment_over_random_at_{k}",
            )
        )

    generated_exact_flags = []
    for row in generated_rows:
        hit, reused_seed_result = _generated_exact_result_hit(row)
        generated_exact_flags.append(hit)
        generated_seed_label_reused = generated_seed_label_reused or reused_seed_result
    metrics.append(
        _renamed(
            exact_result_hit_rate(generated_exact_flags),
            "generated_exact_result_hit_rate",
        )
    )
    if generated_seed_label_reused:
        warnings.append("generated_seed_result_not_counted")

    metrics.extend(
        [
            _safety_developability_false_positive_rate(rows),
            EvaluationMetric(
                metric_id="failed_qc_label_exclusion_count",
                name="failed_qc_label_exclusion_count",
                metric_type="decision_quality",
                value=float(_failed_qc_exclusion_count(dataset)),
                higher_is_better=False,
                metadata={"status": "computed"},
            ),
        ]
    )

    report = EvaluationReport(
        evaluation_id=evaluation_id or "candidate-ranking-evaluation",
        suite_id=suite_id,
        task_id=task_id,
        dataset_id=dataset.dataset_id,
        split_id=split.split_id if split else None,
        prediction_set_id=None,
        metrics=metrics,
        baseline_metrics=[],
        comparisons=_baseline_comparisons(dataset, baseline_comparators),
        warnings=warnings,
        limitations=[
            "Benchmark results are evaluation artifacts, not biomedical evidence.",
            "Prospective validation analytics are not clinical validation.",
            "Outcome labels are limited to imported results or benchmark fixtures.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "row_count": len(rows),
            "existing_row_count": len(existing_rows),
            "generated_row_count": len(generated_rows),
            "combined_ranking_enabled": combined_ranking_enabled,
            "label_rules": [
                "outcome_labels_from_imported_results_or_benchmark_fixtures_only",
                "generated_requires_exact_result_match",
                "seed_result_not_counted_as_generated_analog_success",
            ],
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _evaluated_rows(
    dataset: BenchmarkDataset,
    split: BenchmarkSplit | None,
) -> list[Mapping[str, Any]]:
    rows = _dataset_rows(dataset)
    if split is None:
        return _ranked_rows(rows)
    selected_ids = split.test_ids or split.validation_ids or split.train_ids
    if not selected_ids:
        return _ranked_rows(rows)
    selected = {str(row_id) for row_id in selected_ids}
    return _ranked_rows([row for row in rows if _row_id(row) in selected])


def _dataset_rows(dataset: BenchmarkDataset) -> list[Mapping[str, Any]]:
    rows = dataset.metadata.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _ranked_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: (_rank_sort_key(row), _row_id(row)))


def _rank_sort_key(row: Mapping[str, Any]) -> float:
    record = _record(row)
    rank = _as_float(record.get("rank") or row.get("rank"))
    if rank is not None:
        return rank
    score = _as_float(
        record.get("ranking_score")
        or record.get("score")
        or record.get("prediction_score")
        or row.get("ranking_score")
    )
    if score is not None:
        return -score
    return float(row.get("source_index") or 0)


def _labels_and_scores(rows: Sequence[Mapping[str, Any]]) -> tuple[list[int], list[float]]:
    labels = [1 if _has_positive_label(row) else 0 for row in rows]
    scores = [float(len(rows) - index) for index, _row in enumerate(rows)]
    return labels, scores


def _has_positive_label(row: Mapping[str, Any]) -> bool:
    return any(_positive_label(label) for label in _labels(row))


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


def _generated_exact_result_hit(row: Mapping[str, Any]) -> tuple[bool, bool]:
    reused_seed_result = False
    for label in _labels(row):
        if not _positive_label(label):
            continue
        if _uses_seed_result(row, label):
            reused_seed_result = True
            continue
        if _exact_generated_match(row, label):
            return True, reused_seed_result
    return False, reused_seed_result


def _exact_generated_match(row: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    record = _record(row)
    generated_id = _first_value(row, record, ("generated_id",))
    label_generated_id = _first_value(label, {}, ("generated_id",))
    if label_generated_id and generated_id and str(label_generated_id) != str(generated_id):
        return False
    row_structure = _structure_key(row)
    label_structure = _structure_key(label)
    if row_structure and label_structure:
        return row_structure == label_structure
    return bool(
        generated_id
        and label_generated_id
        and str(generated_id) == str(label_generated_id)
    )


def _uses_seed_result(row: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    record = _record(row)
    source_seed_result_id = _first_value(row, record, ("source_seed_result_id",))
    label_record_id = _first_value(label, {}, ("source_record_id", "result_id", "record_id", "id"))
    return bool(source_seed_result_id and label_record_id == source_seed_result_id)


def _structure_key(mapping: Mapping[str, Any]) -> str | None:
    record = _record(mapping)
    for key in ("inchi_key", "inchikey", "canonical_smiles"):
        value = _first_value(mapping, record, (key,))
        if value not in {None, ""}:
            return f"{key}:{value}"
    return None


def _safety_developability_false_positive_rate(
    rows: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    positives = [row for row in rows if _has_positive_label(row)]
    if not positives:
        return EvaluationMetric(
            metric_id="safety_developability_false_positive_rate",
            name="safety_developability_false_positive_rate",
            metric_type="decision_quality",
            value=None,
            higher_is_better=False,
            metadata={"status": "undefined", "undefined_reason": "no_positive_labels"},
        )
    failures = [row for row in positives if _safety_or_developability_failure(row)]
    return EvaluationMetric(
        metric_id="safety_developability_false_positive_rate",
        name="safety_developability_false_positive_rate",
        metric_type="decision_quality",
        value=len(failures) / len(positives),
        higher_is_better=False,
        metadata={
            "status": "computed",
            "positive_count": len(positives),
            "false_positive_count": len(failures),
        },
    )


def _safety_or_developability_failure(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    if bool(_first_value(row, record, ("safety_flag", "toxicity_flag", "unsafe_flag"))):
        return True
    risk = str(_first_value(row, record, ("developability_risk", "risk_level")) or "").lower()
    if risk in {"high", "severe", "fail", "failed"}:
        return True
    developability_score = _as_float(
        _first_value(row, record, ("developability_score", "overall_developability_score"))
    )
    return developability_score is not None and developability_score < 0.5


def _failed_qc_exclusion_count(dataset: BenchmarkDataset) -> int:
    excluded = dataset.metadata.get("excluded_labels", [])
    if not isinstance(excluded, list):
        return 0
    return sum(
        1
        for item in excluded
        if isinstance(item, Mapping) and item.get("reason") == "failed_qc"
    )


def _baseline_comparisons(
    dataset: BenchmarkDataset,
    baseline_comparators: Sequence[BaselineComparator] | None,
) -> list[dict[str, Any]]:
    comparisons = []
    for comparator in baseline_comparators or []:
        baseline = comparator(dataset) if callable(comparator) else comparator
        comparisons.append(baseline.as_comparison())
    return comparisons


def _warnings(dataset: BenchmarkDataset, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings = list(dataset.metadata.get("warnings", []))
    if not any(_labels(row) for row in rows):
        warnings.append("no_eligible_outcome_labels")
    if _failed_qc_exclusion_count(dataset):
        warnings.append("failed_qc_labels_excluded")
    return sorted(set(str(warning) for warning in warnings))


def _renamed(metric: EvaluationMetric, name: str) -> EvaluationMetric:
    return metric.model_copy(update={"metric_id": name, "name": name})


def _is_generated(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    return bool(row.get("is_generated") or record.get("generated_id"))


def _row_id(row: Mapping[str, Any]) -> str:
    return str(row.get("row_id") or row.get("candidate_id") or row.get("entity_id"))


def _record(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get("record")
    return record if isinstance(record, Mapping) else {}


def _first_value(
    mapping: Mapping[str, Any],
    nested: Mapping[str, Any],
    keys: Sequence[str],
) -> Any:
    for key in keys:
        if mapping.get(key) not in {None, ""}:
            return mapping[key]
        if nested.get(key) not in {None, ""}:
            return nested[key]
    return None


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


__all__ = ["evaluate_candidate_ranking"]
