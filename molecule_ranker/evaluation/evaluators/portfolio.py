from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import ArtifactInput
from molecule_ranker.evaluation.metrics import scaffold_diversity
from molecule_ranker.evaluation.schemas import BenchmarkDataset, EvaluationMetric, EvaluationReport


def evaluate_portfolio(
    *,
    portfolio_artifacts: Mapping[str, ArtifactInput],
    imported_outcome_labels: Mapping[str, ArtifactInput],
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "portfolio_selection",
    dataset_id: str = "portfolio-evaluation-dataset",
) -> EvaluationReport:
    candidates, policy = _portfolio_candidates(portfolio_artifacts)
    labels = _label_index(imported_outcome_labels)
    selected = [candidate for candidate in candidates if _selected(candidate)]
    selected_hit = _selected_hit_rate(selected, labels)
    metrics = [
        selected_hit,
        _target_coverage(selected, candidates),
        _renamed(
            scaffold_diversity(_present_strings(_field(row, "scaffold") for row in selected)),
            "scaffold_diversity",
        ),
        _risk_concentration(selected),
        _generated_fraction_policy_compliance(selected, policy),
        _review_gate_compliance(selected),
        _scenario_robustness(selected),
        _baseline_improvement(selected_hit, candidates, labels, baseline="random"),
        _baseline_improvement(selected_hit, candidates, labels, baseline="evidence_only"),
    ]
    warnings = []
    if not labels:
        warnings.append("no_imported_or_fixture_outcome_labels")
    dataset = _benchmark_dataset(dataset_id, candidates, "portfolio_selection")
    report = EvaluationReport(
        evaluation_id=evaluation_id or "portfolio-evaluation",
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
            "Imported outcomes are required for portfolio hit metrics.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "rules": {"imported_outcomes_required_for_hit_metrics": True},
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _portfolio_candidates(
    artifacts: Mapping[str, ArtifactInput],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    policy: dict[str, Any] = {}
    for artifact in artifacts.values():
        if not isinstance(artifact, Mapping):
            continue
        artifact_policy = artifact.get("policy")
        if isinstance(artifact_policy, Mapping):
            policy.update(dict(artifact_policy))
        for field in (
            "selected_candidates",
            "portfolio_candidates",
            "ranked_candidates",
            "candidates",
        ):
            value = artifact.get(field)
            if isinstance(value, list):
                candidates.extend(dict(item) for item in value if isinstance(item, Mapping))
    return candidates, policy


def _selected(candidate: Mapping[str, Any]) -> bool:
    status = str(candidate.get("selection_status") or candidate.get("status") or "").lower()
    return status in {"selected", "approved", "chosen"} or bool(candidate.get("selected"))


def _selected_hit_rate(
    selected: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
) -> EvaluationMetric:
    if not selected:
        return _undefined("selected_hit_rate", "decision_quality", "no_selected_candidates")
    if not labels:
        return _undefined("selected_hit_rate", "decision_quality", "no_imported_outcome_labels")
    flags = [
        _positive_label(labels[_candidate_id(row)])
        for row in selected
        if _candidate_id(row) in labels
    ]
    if not flags:
        return _undefined(
            "selected_hit_rate",
            "decision_quality",
            "no_selected_candidates_with_labels",
        )
    return _metric("selected_hit_rate", "decision_quality", sum(flags) / len(flags))


def _target_coverage(
    selected: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    all_targets = {
        _field(candidate, "target_symbol")
        for candidate in candidates
        if _field(candidate, "target_symbol")
    }
    selected_targets = {
        _field(candidate, "target_symbol")
        for candidate in selected
        if _field(candidate, "target_symbol")
    }
    if not all_targets:
        return _undefined("target_coverage", "decision_quality", "no_targets")
    return _metric("target_coverage", "decision_quality", len(selected_targets) / len(all_targets))


def _risk_concentration(selected: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not selected:
        return _undefined(
            "risk_concentration",
            "decision_quality",
            "no_selected_candidates",
            higher_is_better=False,
        )
    high_risk = sum((_as_float(row.get("risk_score")) or 0.0) >= 0.7 for row in selected)
    return _metric(
        "risk_concentration",
        "decision_quality",
        high_risk / len(selected),
        higher_is_better=False,
    )


def _generated_fraction_policy_compliance(
    selected: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> EvaluationMetric:
    if not selected:
        return _undefined(
            "generated_fraction_policy_compliance",
            "decision_quality",
            "no_selected_candidates",
        )
    generated_fraction = sum(_generated(row) for row in selected) / len(selected)
    min_fraction = _as_float(policy.get("generated_fraction_min"))
    max_fraction = _as_float(policy.get("generated_fraction_max"))
    compliant = True
    if min_fraction is not None:
        compliant = compliant and generated_fraction >= min_fraction
    if max_fraction is not None:
        compliant = compliant and generated_fraction <= max_fraction
    return _metric(
        "generated_fraction_policy_compliance",
        "decision_quality",
        compliant,
        metadata={"generated_fraction": generated_fraction},
    )


def _review_gate_compliance(selected: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not selected:
        return _undefined("review_gate_compliance", "decision_quality", "no_selected_candidates")
    approved = sum(
        str(row.get("review_gate_status") or "").lower() in {"approved", "passed", "pass"}
        or bool(row.get("review_gate_approved"))
        for row in selected
    )
    return _metric("review_gate_compliance", "decision_quality", approved / len(selected))


def _scenario_robustness(selected: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    values = []
    for row in selected:
        scores = row.get("scenario_scores")
        if isinstance(scores, list) and scores:
            numeric = [_as_float(score) for score in scores]
            observed = [score for score in numeric if score is not None]
            if observed:
                values.append(min(observed))
    if not values:
        return _undefined("scenario_robustness", "decision_quality", "no_scenario_scores")
    return _metric("scenario_robustness", "decision_quality", sum(values) / len(values))


def _baseline_improvement(
    selected_hit: EvaluationMetric,
    candidates: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    baseline: str,
) -> EvaluationMetric:
    name = f"baseline_improvement_over_{baseline}"
    if selected_hit.value is None:
        return _undefined(name, "decision_quality", "selected_hit_rate_undefined")
    selected_count = sum(_selected(candidate) for candidate in candidates)
    if selected_count <= 0 or not labels:
        return _undefined(name, "decision_quality", "no_baseline_candidates")
    ordered = (
        sorted(
            candidates,
            key=lambda row: (
                -(_as_float(row.get("evidence_score")) or 0.0),
                _candidate_id(row),
            ),
        )
        if baseline == "evidence_only"
        else list(candidates)
    )
    baseline_rows = ordered[:selected_count]
    flags = [
        _positive_label(labels[_candidate_id(row)])
        for row in baseline_rows
        if _candidate_id(row) in labels
    ]
    if not flags:
        return _undefined(name, "decision_quality", "baseline_labels_missing")
    return _metric(name, "decision_quality", float(selected_hit.value) - (sum(flags) / len(flags)))


def _label_index(imported_outcome_labels: Mapping[str, ArtifactInput]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for artifact in imported_outcome_labels.values():
        for record in _records(artifact):
            if str(record.get("qc_status") or "").lower() in {"failed", "fail", "qc_failed"}:
                continue
            candidate_id = _candidate_id(record)
            if candidate_id:
                labels[candidate_id] = dict(record)
    return labels


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


def _benchmark_dataset(
    dataset_id: str,
    rows: Sequence[Mapping[str, Any]],
    task_type: str,
) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id=dataset_id,
        name="Portfolio evaluation dataset",
        dataset_type="frozen_project_artifacts",
        source_artifact_ids=[],
        row_count=len(rows),
        candidate_count=len({_candidate_id(row) for row in rows}),
        label_count=None,
        created_at=datetime.now(UTC),
        data_contract_version="data-contracts.v1",
        metadata={
            "task_type": task_type,
            "rows": [
                {
                    "row_id": f"portfolio:{index}",
                    "entity_id": _candidate_id(row),
                    "candidate_id": _candidate_id(row),
                    "is_generated": _generated(row),
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


def _generated(row: Mapping[str, Any]) -> bool:
    return str(row.get("candidate_origin") or "").lower() == "generated" or bool(
        row.get("generated_id")
    )


def _field(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    return str(value) if value not in {None, ""} else None


def _present_strings(values: Iterable[str | None]) -> list[str]:
    return [value for value in values if value]


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


__all__ = ["evaluate_portfolio"]
