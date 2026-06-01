from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from molecule_ranker.knowledge_graph.recommendations import (
    generate_graph_recommendations,
    recommend_from_graph,
)
from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphRecommendation,
    GraphRelation,
    KnowledgeGraph,
)


def test_recommends_stale_decision_review_with_provenance_paths() -> None:
    graph = _recommendation_graph()

    recommendations = generate_graph_recommendations(graph, current_project_id="project:pd")
    stale = _by_type(recommendations, "stale_decision_to_review")

    assert stale
    assert stale[0].recommendation_id.startswith("stale_decision_to_review:")
    assert "review_decision:accept" in stale[0].reuse_entity_ids
    assert _path_has_provenance(stale[0])
    assert _advisory_warnings(stale[0])


def test_recommends_contradiction_resolution() -> None:
    graph = _recommendation_graph()

    recommendations = recommend_from_graph(graph)
    contradiction = _by_type(recommendations, "contradiction_to_resolve")

    assert contradiction
    assert any("contradiction" in item.rationale.lower() for item in contradiction)
    assert _path_has_provenance(contradiction[0])
    assert _advisory_warnings(contradiction[0])


def test_recommends_scaffold_to_avoid_after_repeated_risks() -> None:
    graph = _recommendation_graph()

    recommendations = generate_graph_recommendations(graph)
    avoid = _by_type(recommendations, "scaffold_to_avoid")

    assert avoid
    assert "scaffold:propargylamine" in avoid[0].reuse_entity_ids
    assert "developability_alert:herg" in avoid[0].reuse_entity_ids
    assert _path_has_provenance(avoid[0])
    assert _advisory_warnings(avoid[0])


def test_recommendations_include_provenance_paths() -> None:
    graph = _recommendation_graph()

    recommendations = generate_graph_recommendations(graph)

    assert recommendations
    assert all(_path_has_provenance(item) for item in recommendations)
    assert all(_advisory_warnings(item) for item in recommendations)


def _recommendation_graph() -> KnowledgeGraph:
    now = datetime.now(UTC)
    entities = [
        _entity("project:pd", "project", "PD project"),
        _entity("molecule:risky-a", "molecule", "Risky A"),
        _entity("molecule:risky-b", "molecule", "Risky B"),
        _entity("generated_molecule:gen-stop", "generated_molecule", "Generated stop family"),
        _entity("target:MAOB", "target", "MAOB"),
        _entity("mechanism:maob", "mechanism", "MAOB inhibition"),
        _entity("scaffold:propargylamine", "scaffold", "Propargylamine"),
        _entity("developability_alert:herg", "developability_alert", "hERG alert"),
        _entity("review_decision:accept", "review_decision", "Accepted review"),
        _entity("assay_result:negative", "assay_result", "Negative assay"),
        _entity("model_prediction:old", "model_prediction", "Old prediction"),
    ]
    relations = [
        _rel("a-target", "molecule:risky-a", "targets", "target:MAOB", ["program:a"]),
        _rel("b-target", "molecule:risky-b", "targets", "target:MAOB", ["program:b"]),
        _rel("target-mech-a", "target:MAOB", "has_mechanism", "mechanism:maob", ["program:a"]),
        _rel("target-mech-b", "molecule:risky-b", "has_mechanism", "mechanism:maob", ["program:b"]),
        _rel(
            "a-scaffold",
            "molecule:risky-a",
            "has_scaffold",
            "scaffold:propargylamine",
            ["program:a"],
        ),
        _rel(
            "b-scaffold",
            "molecule:risky-b",
            "has_scaffold",
            "scaffold:propargylamine",
            ["program:b"],
        ),
        _rel(
            "a-risk",
            "molecule:risky-a",
            "has_developability_risk",
            "developability_alert:herg",
            ["program:a"],
        ),
        _rel(
            "b-risk",
            "molecule:risky-b",
            "has_developability_risk",
            "developability_alert:herg",
            ["program:b"],
        ),
        _rel(
            "review-accept",
            "review_decision:accept",
            "reviewed_as",
            "molecule:risky-a",
            ["review:accept"],
            relation_type="review",
            metadata={"decision": "accept_for_followup", "project_id": "project:pd"},
        ),
        _rel(
            "assay-neg",
            "assay_result:negative",
            "contradicts",
            "molecule:risky-a",
            ["assay:negative"],
            relation_type="experimental",
            metadata={"qc_status": "passed", "outcome_label": "negative"},
        ),
        _rel(
            "contradiction-review",
            "review_decision:accept",
            "contradicts",
            "molecule:risky-a",
            ["graph:contradiction", "review-accept", "assay-neg"],
            relation_type="inferred",
            metadata={
                "reason": "review_accepted_vs_later_safety_failure",
                "project_id": "project:pd",
            },
        ),
        _rel(
            "stale-review",
            "review_decision:accept",
            "stale_due_to",
            "molecule:risky-a",
            ["graph:stale", "review-accept", "assay-neg"],
            relation_type="inferred",
            metadata={
                "reason": "review_decision_predates_new_experimental_result",
                "project_id": "project:pd",
            },
        ),
        _rel(
            "gen-target",
            "generated_molecule:gen-stop",
            "hypothesizes",
            "target:MAOB",
            ["graph:gen"],
            relation_type="inferred",
        ),
        _rel(
            "gen-risk",
            "generated_molecule:gen-stop",
            "has_developability_risk",
            "developability_alert:herg",
            ["program:a"],
        ),
        _rel(
            "old-model",
            "model_prediction:old",
            "predicted_by_model",
            "generated_molecule:gen-stop",
            ["project:pd"],
            relation_type="model_prediction",
            metadata={"project_id": "project:pd", "score": 0.8},
        ),
    ]
    return KnowledgeGraph(
        graph_id="kg-recommendation-test",
        entities=entities,
        relations=[
            relation.model_copy(update={"created_at": now, "updated_at": now})
            for relation in relations
        ],
    )


def _entity(entity_id: str, entity_type: str, name: str) -> GraphEntity:
    return GraphEntity(entity_id=entity_id, entity_type=entity_type, name=name)


def _rel(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    provenance: list[str],
    *,
    relation_type: str = "computational",
    metadata: dict[str, object] | None = None,
) -> GraphRelation:
    now = datetime.now(UTC)
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=0.8,
        direction="contradictory" if predicate == "contradicts" else "supportive",
        source_artifact_ids=provenance,
        source_record_ids=[relation_id],
        created_at=now,
        updated_at=now,
        metadata=metadata or {},
    )


def _by_type(
    recommendations: Sequence[GraphRecommendation], recommendation_type: str
) -> list[GraphRecommendation]:
    return [
        item
        for item in recommendations
        if item.recommendation_id.startswith(f"{recommendation_type}:")
    ]


def _path_has_provenance(recommendation: GraphRecommendation) -> bool:
    paths = recommendation.graph_paths
    return bool(
        paths
        and all(path.get("relation_ids") for path in paths)
        and all(path.get("provenance") for path in paths)
    )


def _advisory_warnings(recommendation: GraphRecommendation) -> bool:
    warning_text = " ".join(recommendation.warnings)
    return (
        "advisory" in warning_text.lower()
        and "not evidence" in warning_text.lower()
        and "activity or safety" in warning_text.lower()
    )
