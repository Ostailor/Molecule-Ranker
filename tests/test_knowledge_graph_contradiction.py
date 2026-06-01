from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.knowledge_graph.contradiction import (
    build_contradiction_report,
    build_staleness_report,
    detect_contradiction_relations,
    detect_staleness_relations,
)
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph


def test_detects_assay_literature_and_model_experiment_contradictions() -> None:
    now = datetime.now(UTC)
    graph = _graph(
        [
            _entity("molecule:rasagiline", "molecule", "Rasagiline"),
            _entity("assay_result:pos", "assay_result", "Positive assay"),
            _entity("assay_result:neg", "assay_result", "Negative assay"),
            _entity("literature_claim:support", "literature_claim", "Supportive claim"),
            _entity("literature_claim:contra", "literature_claim", "Contradictory claim"),
            _entity("mechanism:maob", "mechanism", "MAOB inhibition"),
            _entity("model_prediction:high", "model_prediction", "High surrogate score"),
        ],
        [
            _rel(
                "assay-pos",
                "assay_result:pos",
                "supports",
                "molecule:rasagiline",
                metadata={
                    "candidate_name": "Rasagiline",
                    "target_symbol": "MAOB",
                    "endpoint_id": "potency",
                    "outcome_label": "positive",
                    "qc_status": "passed",
                },
                created_at=now - timedelta(days=10),
            ),
            _rel(
                "assay-neg",
                "assay_result:neg",
                "contradicts",
                "molecule:rasagiline",
                metadata={
                    "candidate_name": "Rasagiline",
                    "target_symbol": "MAOB",
                    "endpoint_id": "potency",
                    "outcome_label": "negative",
                    "qc_status": "passed",
                },
                created_at=now,
            ),
            _rel(
                "lit-support",
                "literature_claim:support",
                "supports",
                "mechanism:maob",
                relation_type="literature",
                created_at=now - timedelta(days=50),
            ),
            _rel(
                "lit-contra",
                "literature_claim:contra",
                "contradicts",
                "mechanism:maob",
                relation_type="literature",
                created_at=now - timedelta(days=5),
            ),
            _rel(
                "model-high",
                "model_prediction:high",
                "predicted_by_model",
                "molecule:rasagiline",
                relation_type="model_prediction",
                metadata={"score": 0.92, "target_symbol": "MAOB"},
                created_at=now - timedelta(days=20),
            ),
        ],
    )

    relations = detect_contradiction_relations(graph)
    report = build_contradiction_report(graph)

    reasons = {relation.metadata["reason"] for relation in relations}
    assert "assay_positive_vs_negative" in reasons
    assert "supportive_literature_vs_contradictory_literature" in reasons
    assert "model_prediction_high_vs_experimental_negative" in reasons
    assert all(relation.predicate == "contradicts" for relation in relations)
    assert all(relation.relation_type == "inferred" for relation in relations)
    assert report.advisory is True
    assert len(report.contradiction_relations) == len(relations)
    assert any("advisory" in warning for warning in report.warnings)


def test_detects_structure_review_and_generated_risk_contradictions() -> None:
    now = datetime.now(UTC)
    graph = _graph(
        [
            _entity("generated_molecule:gen", "generated_molecule", "Generated MAOB"),
            _entity("assay_result:neg", "assay_result", "Negative assay"),
            _entity("docking_pose:pose", "docking_pose", "High docking score"),
            _entity("review_decision:accept", "review_decision", "Accepted review"),
            _entity("developability_alert:critical", "developability_alert", "Critical hERG"),
        ],
        [
            _rel(
                "structure-high",
                "docking_pose:pose",
                "computational_pose_for",
                "generated_molecule:gen",
                relation_type="computational",
                metadata={"score": 0.9},
                created_at=now - timedelta(days=20),
            ),
            _rel(
                "assay-neg",
                "assay_result:neg",
                "contradicts",
                "generated_molecule:gen",
                relation_type="experimental",
                metadata={
                    "target_symbol": "MAOB",
                    "outcome_label": "negative",
                    "qc_status": "passed",
                },
                created_at=now,
            ),
            _rel(
                "review-accept",
                "review_decision:accept",
                "reviewed_as",
                "generated_molecule:gen",
                relation_type="review",
                metadata={"decision": "accept_for_followup"},
                created_at=now - timedelta(days=15),
            ),
            _rel(
                "critical-risk",
                "generated_molecule:gen",
                "has_developability_risk",
                "developability_alert:critical",
                relation_type="computational",
                metadata={"severity": "critical", "risk_level": "critical"},
                created_at=now - timedelta(days=1),
            ),
        ],
    )

    reasons = {relation.metadata["reason"] for relation in detect_contradiction_relations(graph)}

    assert "structure_score_high_vs_experimental_negative" in reasons
    assert "review_accepted_vs_later_safety_failure" in reasons
    assert "generated_promising_vs_critical_developability_risk" in reasons


def test_detects_temporal_staleness_without_deleting_old_records() -> None:
    now = datetime.now(UTC)
    graph = _graph(
        [
            _entity("molecule:candidate", "molecule", "Candidate"),
            _entity("literature_claim:old", "literature_claim", "Old supportive claim"),
            _entity("assay_result:new", "assay_result", "New negative assay"),
            _entity("model_prediction:old", "model_prediction", "Old model"),
            _entity("portfolio:p1", "portfolio", "Portfolio P1"),
            _entity("review_decision:old", "review_decision", "Old review"),
            _entity("developability_alert:new", "developability_alert", "New safety update"),
            _entity("target:a", "target", "Mapped target A"),
            _entity("target:b", "target", "Mapped target B"),
        ],
        [
            _rel(
                "old-lit",
                "literature_claim:old",
                "supports",
                "molecule:candidate",
                relation_type="literature",
                created_at=now - timedelta(days=300),
            ),
            _rel(
                "new-assay",
                "assay_result:new",
                "contradicts",
                "molecule:candidate",
                relation_type="experimental",
                metadata={"qc_status": "passed", "outcome_label": "negative"},
                created_at=now - timedelta(days=1),
            ),
            _rel(
                "old-model",
                "model_prediction:old",
                "predicted_by_model",
                "molecule:candidate",
                relation_type="model_prediction",
                metadata={"score": 0.91, "trained_at": (now - timedelta(days=120)).isoformat()},
                created_at=now - timedelta(days=100),
            ),
            _rel(
                "old-portfolio",
                "molecule:candidate",
                "selected_in_portfolio",
                "portfolio:p1",
                relation_type="computational",
                created_at=now - timedelta(days=30),
            ),
            _rel(
                "old-review",
                "review_decision:old",
                "reviewed_as",
                "molecule:candidate",
                relation_type="review",
                created_at=now - timedelta(days=25),
            ),
            _rel(
                "new-risk",
                "molecule:candidate",
                "has_developability_risk",
                "developability_alert:new",
                relation_type="computational",
                metadata={"severity": "critical"},
                created_at=now,
            ),
            _rel(
                "mapping-old",
                "target:a",
                "same_as",
                "target:b",
                relation_type="ontology_mapping",
                metadata={"mapping_version": "2024-01"},
                created_at=now - timedelta(days=200),
            ),
        ],
        metadata={"ontology_versions": {"ontology_mapping": "2026-05"}},
    )

    stale_relations = detect_staleness_relations(graph)
    report = build_staleness_report(graph)

    reasons = {relation.metadata["reason"] for relation in stale_relations}
    assert "old_literature_superseded_by_newer_contradiction" in reasons
    assert "model_trained_before_newer_assay_result" in reasons
    assert "portfolio_selected_before_safety_update" in reasons
    assert "review_decision_predates_new_experimental_result" in reasons
    assert "external_mapping_version_changed" in reasons
    assert all(relation.predicate == "stale_due_to" for relation in stale_relations)
    assert {relation.relation_id for relation in graph.relations} >= {
        "old-lit",
        "old-model",
        "old-portfolio",
        "old-review",
        "mapping-old",
    }
    assert report.advisory is True
    assert len(report.stale_relations) == len(stale_relations)


def _graph(
    entities: list[GraphEntity],
    relations: list[GraphRelation],
    *,
    metadata: dict[str, object] | None = None,
) -> KnowledgeGraph:
    return KnowledgeGraph(
        graph_id="kg-contradiction-test",
        entities=entities,
        relations=relations,
        metadata=metadata or {},
    )


def _entity(entity_id: str, entity_type: str, name: str) -> GraphEntity:
    return GraphEntity(entity_id=entity_id, entity_type=entity_type, name=name)


def _rel(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    *,
    relation_type: str = "experimental",
    metadata: dict[str, object] | None = None,
    created_at: datetime,
) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=0.8,
        direction="contradictory" if predicate == "contradicts" else "supportive",
        source_artifact_ids=[f"artifact:{relation_id}"],
        source_record_ids=[relation_id],
        created_at=created_at,
        updated_at=created_at,
        metadata=metadata or {},
    )
