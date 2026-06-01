from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.hypotheses.evidence_gap import analyze_hypothesis_evidence_gaps
from molecule_ranker.hypotheses.guardrails import detect_hypothesis_guardrail_violations
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation


def test_generated_molecule_without_direct_evidence_gets_high_gap() -> None:
    hypothesis = _hypothesis(
        generated_molecule_entity_ids=["generated_molecule:gen-1"],
        molecule_entity_ids=["molecule:seed"],
        supporting_relation_ids=["rel:lineage"],
        source_artifact_ids=["artifact:lineage"],
    )
    relations = [
        _relation(
            "rel:lineage",
            "generated_molecule:gen-1",
            "generated_from",
            "molecule:seed",
            relation_type="generated_lineage",
        )
    ]

    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("generated_molecule:gen-1", "molecule:seed"),
        relations=relations,
    )

    direct = _gap(gaps, "missing_direct_experimental_result")
    generated = _gap(gaps, "unreviewed_generated_molecule")
    assert direct.severity == "high"
    assert generated.severity == "high"
    assert "Absence of evidence is not evidence of absence" in direct.metadata["boundary"]
    assert not detect_hypothesis_guardrail_violations(direct.suggested_high_level_resolution)


def test_stale_model_prediction_gap_is_detected() -> None:
    old_time = datetime.now(UTC) - timedelta(days=120)
    hypothesis = _hypothesis(
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
        supporting_relation_ids=["rel:model"],
        model_prediction_ids=["model_prediction:old"],
    )
    relations = [
        _relation(
            "rel:model",
            "model_prediction:old",
            "predicted_by_model",
            "target:MAOB",
            relation_type="model_prediction",
            created_at=old_time,
            metadata={"is_stale": True, "model_prediction_id": "model_prediction:old"},
        )
    ]

    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("target:MAOB", "molecule:seed", "model_prediction:old"),
        relations=relations,
    )

    stale = _gap(gaps, "stale_model_prediction")
    assert stale.severity == "medium"
    assert stale.related_relation_ids == ["rel:model"]


def test_contradictory_evidence_unresolved_gap_is_high_severity() -> None:
    hypothesis = _hypothesis(
        molecule_entity_ids=["molecule:seed"],
        supporting_relation_ids=["rel:support"],
        contradicting_relation_ids=["rel:negative"],
        assay_result_ids=["assay_result:negative"],
        source_artifact_ids=["artifact:support", "artifact:negative"],
    )
    relations = [
        _relation("rel:support", "molecule:seed", "supports", "target:MAOB"),
        _relation(
            "rel:negative",
            "molecule:seed",
            "produced_result",
            "assay_result:negative",
            relation_type="experimental",
            direction="contradictory",
            metadata={"outcome_label": "negative"},
        ),
    ]

    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("molecule:seed", "target:MAOB", "assay_result:negative"),
        relations=relations,
    )

    contradiction = _gap(gaps, "contradictory_results")
    assert contradiction.severity == "high"
    assert contradiction.related_relation_ids == ["rel:negative"]


def test_missing_selectivity_context_gap_is_medium() -> None:
    hypothesis = _hypothesis(
        hypothesis_type="molecule_target",
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
        supporting_relation_ids=["rel:molecule-target"],
    )
    relations = [_relation("rel:molecule-target", "molecule:seed", "targets", "target:MAOB")]

    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("molecule:seed", "target:MAOB"),
        relations=relations,
    )

    selectivity = _gap(gaps, "missing_selectivity_data")
    assert selectivity.severity == "medium"
    assert "high-level" in selectivity.suggested_high_level_resolution.lower()


def test_severity_assignment_marks_blocking_generated_contradiction_critical() -> None:
    hypothesis = _hypothesis(
        generated_molecule_entity_ids=["generated_molecule:gen-1"],
        supporting_relation_ids=["rel:lineage"],
        contradicting_relation_ids=["rel:negative"],
        source_artifact_ids=["artifact:lineage", "artifact:negative"],
    )
    relations = [
        _relation(
            "rel:lineage",
            "generated_molecule:gen-1",
            "generated_from",
            "molecule:seed",
            relation_type="generated_lineage",
        ),
        _relation(
            "rel:negative",
            "generated_molecule:gen-1",
            "produced_result",
            "assay_result:negative",
            relation_type="experimental",
            direction="contradictory",
        ),
    ]

    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("generated_molecule:gen-1", "molecule:seed", "assay_result:negative"),
        relations=relations,
    )

    contradiction = _gap(gaps, "contradictory_results")
    assert contradiction.severity == "critical"
    assert contradiction.metadata["severity_rationale"] == "blocks any follow-up planning"


def _hypothesis(
    *,
    hypothesis_type: str = "generated_molecule",
    target_entity_ids: list[str] | None = None,
    molecule_entity_ids: list[str] | None = None,
    generated_molecule_entity_ids: list[str] | None = None,
    supporting_relation_ids: list[str] | None = None,
    contradicting_relation_ids: list[str] | None = None,
    source_artifact_ids: list[str] | None = None,
    assay_result_ids: list[str] | None = None,
    model_prediction_ids: list[str] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id="hypothesis:test",
        hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
        title="Hypothesis: evidence gap review",
        statement="Hypothesis for review: graph-backed context needs evidence-gap analysis.",
        target_entity_ids=target_entity_ids or [],
        molecule_entity_ids=molecule_entity_ids or [],
        generated_molecule_entity_ids=generated_molecule_entity_ids or [],
        supporting_relation_ids=supporting_relation_ids or ["rel:support"],
        contradicting_relation_ids=contradicting_relation_ids or [],
        source_artifact_ids=source_artifact_ids or ["artifact:support"],
        assay_result_ids=assay_result_ids or [],
        model_prediction_ids=model_prediction_ids or [],
    )


def _entity(entity_id: str) -> GraphEntity:
    entity_type = entity_id.split(":", 1)[0]
    return GraphEntity(entity_id=entity_id, entity_type=entity_type, name=entity_id)


def _entities(*entity_ids: str) -> list[GraphEntity]:
    return [_entity(entity_id) for entity_id in entity_ids]


def _relation(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    *,
    relation_type: str = "evidence_backed",
    direction: str | None = "supportive",
    created_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> GraphRelation:
    timestamp = created_at or datetime.now(UTC)
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=0.8,
        direction=direction,
        source_artifact_ids=[f"artifact:{relation_id}"],
        source_record_ids=[f"record:{relation_id}"],
        created_at=timestamp,
        updated_at=timestamp,
        metadata=metadata or {},
    )


def _gap(gaps, gap_type: str):
    matches = [gap for gap in gaps if gap.gap_type == gap_type]
    assert len(matches) == 1
    return matches[0]
