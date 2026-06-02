from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import ArtifactInput
from molecule_ranker.evaluation.schemas import BenchmarkDataset, EvaluationMetric, EvaluationReport


def evaluate_hypotheses(
    *,
    hypothesis_artifacts: Mapping[str, ArtifactInput],
    imported_evidence_context: Mapping[str, ArtifactInput],
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "hypothesis_prioritization",
    dataset_id: str = "hypothesis-evaluation-dataset",
) -> EvaluationReport:
    hypotheses = _hypotheses(hypothesis_artifacts)
    evidence = _evidence_index(imported_evidence_context)
    metrics = [
        _support_rate_after_outcomes(hypotheses, evidence),
        _contradiction_resolution_rate(hypotheses),
        _retirement_rate(hypotheses),
        _stale_hypothesis_detection_rate(hypotheses),
        _evidence_gap_closure_rate(hypotheses),
        _generated_hypothesis_exact_result_rate(hypotheses, evidence),
        _review_acceptance_vs_outcome_alignment(hypotheses, evidence),
    ]
    warnings = []
    if not evidence:
        warnings.append("no_imported_evidence_context")
    dataset = _benchmark_dataset(dataset_id, hypotheses)
    report = EvaluationReport(
        evaluation_id=evaluation_id or "hypothesis-evaluation",
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
            "Hypothesis support is scoped to imported evidence context.",
            "mechanism_hypotheses_are_not_causality_proof",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "hypothesis_count": len(hypotheses),
            "imported_evidence_count": len(evidence),
            "rules": {
                "hypothesis_support_scoped_to_imported_evidence": True,
                "mechanism_hypotheses_are_causality_proof": False,
            },
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _hypotheses(artifacts: Mapping[str, ArtifactInput]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts.values():
        if isinstance(artifact, list | tuple):
            rows.extend(dict(item) for item in artifact if isinstance(item, Mapping))
            continue
        if not isinstance(artifact, Mapping):
            continue
        for field in ("hypotheses", "ranked_hypotheses", "research_hypotheses"):
            value = artifact.get(field)
            if isinstance(value, list):
                rows.extend(dict(item) for item in value if isinstance(item, Mapping))
    return rows


def _evidence_index(artifacts: Mapping[str, ArtifactInput]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts.values():
        for record in _records(artifact):
            if _is_prediction(record):
                continue
            hypothesis_id = str(record.get("hypothesis_id") or "")
            if not hypothesis_id:
                continue
            index.setdefault(hypothesis_id, []).append(dict(record))
    return index


def _records(artifact: ArtifactInput) -> list[Mapping[str, Any]]:
    if isinstance(artifact, list | tuple):
        return [item for item in artifact if isinstance(item, Mapping)]
    if not isinstance(artifact, Mapping):
        return []
    for field in ("evidence", "experimental_evidence", "assay_results", "labels", "results"):
        value = artifact.get(field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _support_rate_after_outcomes(
    hypotheses: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> EvaluationMetric:
    if not evidence:
        return _undefined(
            "support_rate_after_outcomes",
            "decision_quality",
            "no_imported_evidence_context",
        )
    statuses = [_supported(evidence_item) for items in evidence.values() for evidence_item in items]
    return _rate("support_rate_after_outcomes", statuses)


def _contradiction_resolution_rate(hypotheses: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    contradicted = [row for row in hypotheses if _truthy(row.get("has_contradiction"))]
    if not contradicted:
        return _undefined(
            "contradiction_resolution_rate",
            "decision_quality",
            "no_contradicted_hypotheses",
        )
    return _rate(
        "contradiction_resolution_rate",
        [_truthy(row.get("contradiction_resolved")) for row in contradicted],
    )


def _retirement_rate(hypotheses: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not hypotheses:
        return _undefined("retirement_rate", "decision_quality", "no_hypotheses")
    return _rate("retirement_rate", [_truthy(row.get("retired")) for row in hypotheses])


def _stale_hypothesis_detection_rate(hypotheses: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    stale = [row for row in hypotheses if _truthy(row.get("is_stale"))]
    if not stale:
        return _undefined(
            "stale_hypothesis_detection_rate",
            "decision_quality",
            "no_stale_hypotheses",
        )
    return _rate(
        "stale_hypothesis_detection_rate",
        [_truthy(row.get("stale_detected")) for row in stale],
    )


def _evidence_gap_closure_rate(hypotheses: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    open_gaps = [row for row in hypotheses if _truthy(row.get("evidence_gap_open"))]
    if not open_gaps:
        return _undefined(
            "evidence_gap_closure_rate",
            "decision_quality",
            "no_open_evidence_gaps",
        )
    return _rate(
        "evidence_gap_closure_rate",
        [_truthy(row.get("evidence_gap_closed")) for row in open_gaps],
    )


def _generated_hypothesis_exact_result_rate(
    hypotheses: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> EvaluationMetric:
    generated = [row for row in hypotheses if _generated(row)]
    if not generated:
        return _undefined(
            "generated_hypothesis_exact_result_rate",
            "decision_quality",
            "no_generated_hypotheses",
        )
    flags = [_generated_exact_hit(row, evidence.get(_hypothesis_id(row), [])) for row in generated]
    return _rate("generated_hypothesis_exact_result_rate", flags)


def _review_acceptance_vs_outcome_alignment(
    hypotheses: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> EvaluationMetric:
    paired = [
        (row, evidence[_hypothesis_id(row)])
        for row in hypotheses
        if _hypothesis_id(row) in evidence
    ]
    if not paired:
        return _undefined(
            "review_acceptance_vs_outcome_alignment",
            "decision_quality",
            "no_review_outcome_pairs",
        )
    flags = []
    for row, evidence_items in paired:
        accepted = str(row.get("review_decision") or "").lower() in {"accept", "approved"}
        supported = any(_supported(item) for item in evidence_items)
        flags.append(accepted == supported)
    return _rate("review_acceptance_vs_outcome_alignment", flags)


def _generated_exact_hit(
    hypothesis: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> bool:
    for evidence_item in evidence_items:
        if not _supported(evidence_item):
            continue
        evidence_generated_id = str(evidence_item.get("generated_id") or "")
        hypothesis_generated_id = str(hypothesis.get("generated_id") or "")
        if evidence_generated_id != hypothesis_generated_id:
            continue
        row_structure = _structure_key(hypothesis)
        evidence_structure = _structure_key(evidence_item)
        if row_structure and evidence_structure and row_structure == evidence_structure:
            return True
    return False


def _benchmark_dataset(dataset_id: str, rows: Sequence[Mapping[str, Any]]) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id=dataset_id,
        name="Hypothesis evaluation dataset",
        dataset_type="frozen_project_artifacts",
        source_artifact_ids=[],
        row_count=len(rows),
        candidate_count=None,
        label_count=None,
        created_at=datetime.now(UTC),
        data_contract_version="data-contracts.v1",
        metadata={
            "task_type": "hypothesis_prioritization",
            "rows": [
                {
                    "row_id": f"hypothesis:{index}",
                    "entity_id": _hypothesis_id(row),
                    "record": dict(row),
                    "labels": [],
                }
                for index, row in enumerate(rows)
            ],
        },
    )


def _rate(name: str, flags: Sequence[bool]) -> EvaluationMetric:
    if not flags:
        return _undefined(name, "decision_quality", "no_observations")
    return _metric(name, "decision_quality", sum(flags) / len(flags))


def _supported(evidence_item: Mapping[str, Any]) -> bool:
    value = (
        evidence_item.get("support_status")
        or evidence_item.get("outcome_label")
        or evidence_item.get("label")
        or evidence_item.get("status")
    )
    return str(value).strip().lower() in {
        "supported",
        "positive",
        "active",
        "hit",
        "pass",
        "passed",
    }


def _generated(row: Mapping[str, Any]) -> bool:
    return str(row.get("hypothesis_type") or "").lower() == "generated_molecule" or bool(
        row.get("generated_id")
    )


def _hypothesis_id(row: Mapping[str, Any]) -> str:
    return str(row.get("hypothesis_id") or row.get("id") or "")


def _structure_key(row: Mapping[str, Any]) -> str | None:
    for field in ("inchi_key", "inchikey", "canonical_smiles"):
        if row.get(field):
            return f"{field}:{row[field]}"
    return None


def _is_prediction(record: Mapping[str, Any]) -> bool:
    source_type = str(record.get("source_type") or record.get("artifact_type") or "").lower()
    return "prediction" in source_type or bool(
        record.get("model_id") or record.get("model_version")
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y", "resolved", "closed"}
    return bool(value)


def _metric(name: str, metric_type: str, value: float | bool) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,  # type: ignore[arg-type]
        value=value,
        higher_is_better=True,
        metadata={"status": "computed"},
    )


def _undefined(name: str, metric_type: str, reason: str) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,  # type: ignore[arg-type]
        value=None,
        higher_is_better=True,
        metadata={"status": "undefined", "undefined_reason": reason},
    )


__all__ = ["evaluate_hypotheses"]
