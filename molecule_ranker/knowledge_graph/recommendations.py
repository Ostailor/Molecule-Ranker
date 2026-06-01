from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.reasoning import (
    GraphQueryResult,
    GraphReasoner,
    analyze_cross_program_knowledge,
)
from molecule_ranker.knowledge_graph.schemas import (
    GraphRecommendation,
    GraphRelation,
    KnowledgeGraph,
)

ADVISORY_WARNINGS = [
    "Recommendations are advisory graph-derived summaries, not evidence.",
    "Recommendations must not be read as claims of activity or safety.",
    "Codex may summarize rationale, but deterministic graph queries create this recommendation.",
]

RECOMMENDATION_TYPES = {
    "similar_candidate_from_other_program",
    "scaffold_to_revisit",
    "scaffold_to_avoid",
    "target_to_review",
    "mechanism_to_investigate",
    "assay_gap_to_close",
    "stale_decision_to_review",
    "contradiction_to_resolve",
    "generated_family_to_expand",
    "generated_family_to_stop",
}


def recommend_from_graph(graph: KnowledgeGraph) -> list[GraphRecommendation]:
    return generate_graph_recommendations(graph)


def generate_graph_recommendations(
    graph: KnowledgeGraph,
    *,
    current_project_id: str | None = None,
    current_program_id: str | None = None,
    portfolio_outputs: list[dict[str, Any]] | None = None,
    assay_results: list[dict[str, Any]] | None = None,
    review_decisions: list[dict[str, Any]] | None = None,
    query_outputs: list[GraphQueryResult] | None = None,
) -> list[GraphRecommendation]:
    del portfolio_outputs, assay_results, review_decisions
    reasoner = GraphReasoner(graph)
    query_results = query_outputs or _default_query_outputs(reasoner)
    recommendations: dict[str, GraphRecommendation] = {}

    _add_stale_decision_recommendations(
        graph,
        recommendations,
        current_project_id=current_project_id,
        current_program_id=current_program_id,
    )
    _add_contradiction_recommendations(graph, recommendations)
    _add_scaffold_risk_recommendations(graph, query_results, recommendations)
    _add_assay_gap_recommendations(query_results, recommendations)
    _add_generated_family_recommendations(graph, query_results, recommendations)
    _add_cross_program_recommendations(query_results, recommendations)

    if not recommendations:
        for item in analyze_cross_program_knowledge(graph).recommendations:
            recommendations[item.recommendation_id] = item
    return sorted(recommendations.values(), key=lambda item: item.recommendation_id)


def _default_query_outputs(reasoner: GraphReasoner) -> list[GraphQueryResult]:
    outputs: list[GraphQueryResult] = []
    outputs.extend(reasoner.targets_with_repeated_developability_failures())
    outputs.extend(reasoner.molecules_with_safety_concerns_across_programs())
    outputs.extend(reasoner.portfolios_reusing_same_scaffold_risk())
    outputs.extend(reasoner.projects_with_stale_model_predictions())
    outputs.extend(reasoner.generated_molecules_without_direct_evidence())
    for candidate in reasoner.entities.values():
        if candidate.entity_type in {"molecule", "generated_molecule"}:
            outputs.extend(reasoner.evidence_gaps_for_candidate(candidate.entity_id))
    outputs.extend(reasoner.mechanisms_supported_across_programs())
    return outputs


def _add_stale_decision_recommendations(
    graph: KnowledgeGraph,
    recommendations: dict[str, GraphRecommendation],
    *,
    current_project_id: str | None,
    current_program_id: str | None,
) -> None:
    entities = graph.entity_map()
    for relation in graph.relations:
        if relation.predicate != "stale_due_to":
            continue
        reason = str(relation.metadata.get("reason") or "")
        if not reason.startswith("review_decision"):
            continue
        if not _matches_scope(relation, current_project_id, current_program_id):
            continue
        review = entities.get(relation.subject_entity_id)
        target = entities.get(relation.object_entity_id)
        target_name = target.name if target else relation.object_entity_id
        rationale = (
            f"Review stale decision {review.name if review else relation.subject_entity_id} "
            f"against newer graph-linked result for {target_name}."
        )
        _store(
            recommendations,
            _recommendation(
                "stale_decision_to_review",
                rationale,
                graph,
                [relation.subject_entity_id, relation.object_entity_id],
                [relation],
                confidence=relation.confidence,
                metadata={"reason": reason},
            ),
        )


def _add_contradiction_recommendations(
    graph: KnowledgeGraph,
    recommendations: dict[str, GraphRecommendation],
) -> None:
    for relation in graph.relations:
        if relation.predicate not in {"contradicts", "contradicted_by"}:
            continue
        rationale = (
            "Resolve graph-linked contradiction before reusing this candidate, mechanism, "
            "or decision in the current program."
        )
        _store(
            recommendations,
            _recommendation(
                "contradiction_to_resolve",
                rationale,
                graph,
                [relation.subject_entity_id, relation.object_entity_id],
                [relation],
                confidence=relation.confidence,
                metadata={"reason": relation.metadata.get("reason")},
            ),
        )


def _add_scaffold_risk_recommendations(
    graph: KnowledgeGraph,
    query_outputs: list[GraphQueryResult],
    recommendations: dict[str, GraphRecommendation],
) -> None:
    for result in query_outputs:
        if result.query_name == "portfolios_reusing_same_scaffold_risk":
            scaffold_ids = [
                ref.entity_id for ref in result.entity_refs if ref.entity_type == "scaffold"
            ]
            risk_ids = [
                ref.entity_id
                for ref in result.entity_refs
                if ref.entity_type in {"developability_alert", "developability_risk"}
            ]
            if scaffold_ids and risk_ids:
                _store(
                    recommendations,
                    _recommendation_from_query(
                        "scaffold_to_avoid",
                        (
                            "Avoid or explicitly justify reuse of scaffold family because graph "
                            "history links it to repeated developability risk."
                        ),
                        result,
                        extra_entity_ids=[*scaffold_ids, *risk_ids],
                    ),
                )
        if result.query_name == "scaffolds_with_positive_assay_history":
            _store(
                recommendations,
                _recommendation_from_query(
                    "scaffold_to_revisit",
                    "Revisit scaffold family with prior positive assay history for expert review.",
                    result,
                ),
            )
    _add_scaffold_to_avoid_from_raw_graph(graph, recommendations)


def _add_scaffold_to_avoid_from_raw_graph(
    graph: KnowledgeGraph,
    recommendations: dict[str, GraphRecommendation],
) -> None:
    scaffold_by_candidate: dict[str, list[GraphRelation]] = defaultdict(list)
    risk_by_candidate: dict[str, list[GraphRelation]] = defaultdict(list)
    for relation in graph.relations:
        if relation.predicate == "has_scaffold":
            scaffold_by_candidate[relation.subject_entity_id].append(relation)
        if relation.predicate in {"has_developability_risk", "blocked_by"}:
            risk_by_candidate[relation.subject_entity_id].append(relation)
    grouped: dict[tuple[str, str], list[GraphRelation]] = defaultdict(list)
    for candidate_id, scaffold_relations in scaffold_by_candidate.items():
        for scaffold in scaffold_relations:
            for risk in risk_by_candidate.get(candidate_id, []):
                grouped[(scaffold.object_entity_id, risk.object_entity_id)].extend([scaffold, risk])
    for (scaffold_id, risk_id), relations in grouped.items():
        candidates = {
            relation.subject_entity_id
            for relation in relations
            if relation.predicate == "has_scaffold"
        }
        if len(candidates) < 2:
            continue
        _store(
            recommendations,
            _recommendation(
                "scaffold_to_avoid",
                (
                    "Avoid or review scaffold before reuse because multiple graph-linked "
                    "candidates share the same developability risk."
                ),
                graph,
                [scaffold_id, risk_id, *sorted(candidates)],
                relations,
                confidence=_mean_confidence(relations),
                metadata={"candidate_count": len(candidates)},
            ),
        )


def _add_assay_gap_recommendations(
    query_outputs: list[GraphQueryResult],
    recommendations: dict[str, GraphRecommendation],
) -> None:
    for result in query_outputs:
        if result.query_name != "evidence_gaps_for_candidate":
            continue
        warning_text = " ".join(result.warnings).lower()
        if "direct experimental evidence" not in warning_text and "no positive" not in warning_text:
            continue
        _store(
            recommendations,
            _recommendation_from_query(
                "assay_gap_to_close",
                "Close assay evidence gap before advancing graph-linked candidate.",
                result,
            ),
        )


def _add_generated_family_recommendations(
    graph: KnowledgeGraph,
    query_outputs: list[GraphQueryResult],
    recommendations: dict[str, GraphRecommendation],
) -> None:
    for result in query_outputs:
        if result.query_name == "generated_molecules_without_direct_evidence":
            _store(
                recommendations,
                _recommendation_from_query(
                    "generated_family_to_expand",
                    (
                        "Generated family can be expanded only as a hypothesis if expert review "
                        "accepts the evidence gap."
                    ),
                    result,
                ),
            )
    for relation in graph.relations:
        subject = graph.entity_map().get(relation.subject_entity_id)
        if (
            subject is not None
            and subject.entity_type == "generated_molecule"
            and relation.predicate in {"has_developability_risk", "blocked_by", "contradicts"}
        ):
            _store(
                recommendations,
                _recommendation(
                    "generated_family_to_stop",
                    (
                        "Stop or pause generated family expansion pending contradiction "
                        "or risk review."
                    ),
                    graph,
                    [relation.subject_entity_id, relation.object_entity_id],
                    [relation],
                    confidence=relation.confidence,
                ),
            )


def _add_cross_program_recommendations(
    query_outputs: list[GraphQueryResult],
    recommendations: dict[str, GraphRecommendation],
) -> None:
    for result in query_outputs:
        if result.query_name == "mechanisms_supported_across_programs":
            _store(
                recommendations,
                _recommendation_from_query(
                    "mechanism_to_investigate",
                    "Investigate recurring mechanism across programs with source review.",
                    result,
                ),
            )
        if result.query_name == "candidates_for_target":
            _store(
                recommendations,
                _recommendation_from_query(
                    "similar_candidate_from_other_program",
                    "Review similar candidate from another program before duplicating work.",
                    result,
                ),
            )
        if result.query_name == "targets_with_repeated_developability_failures":
            _store(
                recommendations,
                _recommendation_from_query(
                    "target_to_review",
                    "Review target because multiple candidates show developability blockers.",
                    result,
                ),
            )


def _recommendation_from_query(
    recommendation_type: str,
    rationale: str,
    result: GraphQueryResult,
    *,
    extra_entity_ids: list[str] | None = None,
) -> GraphRecommendation:
    entity_ids = [ref.entity_id for ref in result.entity_refs]
    relation_ids = [ref.relation_id for ref in result.relation_refs]
    provenance = sorted(set(result.provenance))
    return _make_recommendation(
        recommendation_type,
        rationale,
        entity_ids=[*(extra_entity_ids or []), *entity_ids],
        relation_ids=relation_ids,
        provenance=provenance,
        confidence=result.confidence,
        metadata={"query_name": result.query_name, **result.metadata},
    )


def _recommendation(
    recommendation_type: str,
    rationale: str,
    graph: KnowledgeGraph,
    entity_ids: list[str],
    relations: list[GraphRelation],
    *,
    confidence: float,
    metadata: dict[str, Any] | None = None,
) -> GraphRecommendation:
    del graph
    return _make_recommendation(
        recommendation_type,
        rationale,
        entity_ids=entity_ids,
        relation_ids=[relation.relation_id for relation in relations],
        provenance=sorted(
            {ref for relation in relations for ref in _relation_provenance(relation)}
        ),
        confidence=confidence,
        metadata=metadata or {},
    )


def _make_recommendation(
    recommendation_type: str,
    rationale: str,
    *,
    entity_ids: list[str],
    relation_ids: list[str],
    provenance: list[str],
    confidence: float,
    metadata: dict[str, Any],
) -> GraphRecommendation:
    if recommendation_type not in RECOMMENDATION_TYPES:
        raise ValueError(f"unknown graph recommendation type: {recommendation_type}")
    stable_key = "|".join(
        [recommendation_type, *sorted(set(entity_ids)), *sorted(set(relation_ids))]
    )
    recommendation_id = f"{recommendation_type}:{uuid5(NAMESPACE_URL, stable_key).hex[:16]}"
    graph_paths = [
        {
            "entity_ids": sorted(set(entity_ids)),
            "relation_ids": sorted(set(relation_ids)),
            "provenance": sorted(set(provenance)),
        }
    ]
    return GraphRecommendation(
        recommendation_id=recommendation_id,
        rationale=rationale,
        reuse_entity_ids=sorted(set(entity_ids)),
        warnings=list(ADVISORY_WARNINGS),
        graph_paths=graph_paths,
        relation_ids=sorted(set(relation_ids)),
        provenance=sorted(set(provenance)),
        confidence=max(0.0, min(float(confidence), 1.0)),
        recommendation_type=recommendation_type,
        creates_evidence=False,
        claims_activity_or_safety=False,
        **metadata,
    )


def _store(
    recommendations: dict[str, GraphRecommendation],
    recommendation: GraphRecommendation,
) -> None:
    recommendations.setdefault(recommendation.recommendation_id, recommendation)


def _matches_scope(
    relation: GraphRelation,
    current_project_id: str | None,
    current_program_id: str | None,
) -> bool:
    if current_project_id is None and current_program_id is None:
        return True
    project = relation.metadata.get("project_id")
    program = relation.metadata.get("program_id")
    return (
        (current_project_id is not None and project == current_project_id)
        or (current_program_id is not None and program == current_program_id)
        or (project is None and program is None)
    )


def _relation_provenance(relation: GraphRelation) -> list[str]:
    return sorted({*relation.source_artifact_ids, *relation.source_record_ids})


def _mean_confidence(relations: list[GraphRelation]) -> float:
    if not relations:
        return 0.0
    return sum(relation.confidence for relation in relations) / len(relations)


__all__ = [
    "ADVISORY_WARNINGS",
    "RECOMMENDATION_TYPES",
    "generate_graph_recommendations",
    "recommend_from_graph",
]
