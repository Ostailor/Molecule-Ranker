from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.baselines import ensure_baseline_comparison
from molecule_ranker.evaluation.datasets import ArtifactInput
from molecule_ranker.evaluation.schemas import BenchmarkDataset, EvaluationMetric, EvaluationReport


def evaluate_graph(
    *,
    graph_artifacts: Mapping[str, ArtifactInput],
    evaluation_id: str | None = None,
    suite_id: str | None = None,
    task_id: str = "integration_data_quality",
    dataset_id: str = "graph-evaluation-dataset",
) -> EvaluationReport:
    graph = _merge_graphs(graph_artifacts)
    entities = graph["entities"]
    relations = graph["relations"]
    contradiction_cases = graph["contradiction_cases"]
    stale_decision_cases = graph["stale_decision_cases"]
    graph_queries = graph["graph_queries"]
    metrics = [
        _entity_deduplication_conflict_rate(entities),
        _provenance_coverage(entities, relations),
        _inferred_relation_fraction(relations),
        _case_detection_coverage(
            "contradiction_detection_coverage",
            contradiction_cases,
        ),
        _case_detection_coverage(
            "stale_decision_detection_coverage",
            stale_decision_cases,
        ),
        _graph_query_grounding_rate(graph_queries),
    ]
    dataset = _benchmark_dataset(dataset_id, entities, relations)
    report = EvaluationReport(
        evaluation_id=evaluation_id or "graph-evaluation",
        suite_id=suite_id,
        task_id=task_id,
        dataset_id=dataset.dataset_id,
        split_id=None,
        prediction_set_id=None,
        metrics=metrics,
        baseline_metrics=[],
        comparisons=[],
        warnings=[],
        limitations=[
            "Benchmark results are evaluation artifacts, not biomedical evidence.",
            "Graph inferred relations are not biomedical evidence by themselves.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "entity_count": len(entities),
            "relation_count": len(relations),
            "rules": {"graph_outputs_are_evaluation_artifacts": True},
        },
    )
    return ensure_baseline_comparison(report, dataset)


def _merge_graphs(artifacts: Mapping[str, ArtifactInput]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {
        "entities": [],
        "relations": [],
        "contradiction_cases": [],
        "stale_decision_cases": [],
        "graph_queries": [],
    }
    for artifact in artifacts.values():
        if not isinstance(artifact, Mapping):
            continue
        for field in merged:
            value = artifact.get(field)
            if isinstance(value, list):
                merged[field].extend(dict(item) for item in value if isinstance(item, Mapping))
    return merged


def _entity_deduplication_conflict_rate(entities: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not entities:
        return _undefined(
            "entity_deduplication_conflict_rate",
            "decision_quality",
            "no_entities",
            higher_is_better=False,
        )
    conflicts = sum(_truthy(entity.get("dedup_conflict")) for entity in entities)
    return _metric(
        "entity_deduplication_conflict_rate",
        "decision_quality",
        conflicts / len(entities),
        higher_is_better=False,
    )


def _provenance_coverage(
    entities: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    items = [*entities, *relations]
    if not items:
        return _undefined("provenance_coverage", "decision_quality", "no_graph_items")
    covered = sum(bool(item.get("provenance_ids") or item.get("provenance")) for item in items)
    return _metric("provenance_coverage", "decision_quality", covered / len(items))


def _inferred_relation_fraction(relations: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not relations:
        return _undefined("inferred_relation_fraction", "decision_quality", "no_relations")
    inferred = sum(_truthy(relation.get("inferred")) for relation in relations)
    return _metric(
        "inferred_relation_fraction",
        "decision_quality",
        inferred / len(relations),
        higher_is_better=False,
    )


def _case_detection_coverage(
    name: str,
    cases: Sequence[Mapping[str, Any]],
) -> EvaluationMetric:
    if not cases:
        return _undefined(name, "decision_quality", "no_cases")
    detected = sum(_truthy(case.get("detected")) for case in cases)
    return _metric(name, "decision_quality", detected / len(cases))


def _graph_query_grounding_rate(queries: Sequence[Mapping[str, Any]]) -> EvaluationMetric:
    if not queries:
        return _undefined("graph_query_grounding_rate", "decision_quality", "no_graph_queries")
    grounded = sum(_truthy(query.get("grounded")) for query in queries)
    return _metric("graph_query_grounding_rate", "decision_quality", grounded / len(queries))


def _benchmark_dataset(
    dataset_id: str,
    entities: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> BenchmarkDataset:
    rows = [
        {"row_id": f"entity:{index}", "entity_id": entity.get("entity_id"), "record": dict(entity)}
        for index, entity in enumerate(entities)
    ] + [
        {
            "row_id": f"relation:{index}",
            "entity_id": relation.get("relation_id"),
            "record": dict(relation),
        }
        for index, relation in enumerate(relations)
    ]
    return BenchmarkDataset(
        dataset_id=dataset_id,
        name="Graph evaluation dataset",
        dataset_type="frozen_project_artifacts",
        source_artifact_ids=[],
        row_count=len(rows),
        candidate_count=None,
        label_count=None,
        created_at=datetime.now(UTC),
        data_contract_version="data-contracts.v1",
        metadata={"task_type": "integration_data_quality", "rows": rows},
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y", "detected", "grounded"}
    return bool(value)


def _metric(
    name: str,
    metric_type: str,
    value: float | bool,
    *,
    higher_is_better: bool | None = True,
) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,  # type: ignore[arg-type]
        value=value,
        higher_is_better=higher_is_better,
        metadata={"status": "computed"},
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


__all__ = ["evaluate_graph"]
