from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from molecule_ranker.hypotheses.generator import generate_hypothesis_candidates
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation
from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore


def test_supported_mechanism_expansion_generates_gap_backed_hypothesis(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("disease:alz", "disease", "Alzheimer disease"),
        _entity("target:MAOB", "target", "MAOB"),
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity("mechanism:oxidative-stress", "mechanism", "Oxidative stress pathway"),
    )
    disease_target = _relation(
        "rel:disease-target",
        "disease:alz",
        "associated_with",
        "target:MAOB",
        relation_type="evidence_backed",
        confidence=0.92,
    )
    molecule_target = _relation(
        "rel:molecule-target-lit",
        "molecule:seed",
        "targets",
        "target:MAOB",
        relation_type="literature",
        confidence=0.84,
    )
    mechanism = _relation(
        "rel:target-mechanism",
        "target:MAOB",
        "has_mechanism",
        "mechanism:oxidative-stress",
        relation_type="evidence_backed",
        confidence=0.78,
    )
    _add_relations(store, disease_target, molecule_target, mechanism)

    hypotheses = generate_hypothesis_candidates(store)

    expansion = _only_pattern(hypotheses, "supported_mechanism_expansion")
    assert expansion.hypothesis_type in {"molecule_target", "mechanism"}
    assert expansion.disease_entity_ids == ["disease:alz"]
    assert expansion.target_entity_ids == ["target:MAOB"]
    assert expansion.molecule_entity_ids == ["molecule:seed"]
    assert expansion.mechanism_entity_ids == ["mechanism:oxidative-stress"]
    assert set(expansion.supporting_relation_ids) == {
        disease_target.relation_id,
        molecule_target.relation_id,
        mechanism.relation_id,
    }
    assert expansion.metadata["evidence_gap_type"] == "missing_direct_experimental_result"
    assert expansion.metadata["not_evidence"] is True
    assert "hypothesis" in expansion.statement.lower()


def test_generated_analog_follow_up_requires_strong_readiness_and_no_direct_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity(
            "generated_molecule:analog-1",
            "generated_molecule",
            "Generated analog 1",
            metadata={"design_score": 0.91, "readiness_score": 0.86},
        ),
    )
    lineage = _relation(
        "rel:analog-lineage",
        "generated_molecule:analog-1",
        "generated_from",
        "molecule:seed",
        relation_type="generated_lineage",
        confidence=0.88,
    )
    _add_relations(store, lineage)

    hypotheses = generate_hypothesis_candidates(store)

    follow_up = _only_pattern(hypotheses, "generated_analog_follow_up")
    assert follow_up.hypothesis_type == "generated_molecule"
    assert follow_up.generated_molecule_entity_ids == ["generated_molecule:analog-1"]
    assert follow_up.molecule_entity_ids == ["molecule:seed"]
    assert follow_up.supporting_relation_ids == [lineage.relation_id]
    assert follow_up.metadata["evidence_gap_type"] == "missing_direct_experimental_result"
    assert follow_up.evidence_item_ids == []
    assert follow_up.assay_result_ids == []


def test_contradiction_resolution_detects_supportive_model_and_negative_result(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("target:MAOB", "target", "MAOB"),
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity("assay_result:negative", "assay_result", "Negative imported assay result"),
    )
    model_support = _relation(
        "rel:model-support",
        "molecule:seed",
        "predicted_by_model",
        "target:MAOB",
        relation_type="model_prediction",
        confidence=0.81,
        direction="supportive",
        metadata={"model_prediction_id": "model-prediction:1"},
    )
    negative_result = _relation(
        "rel:negative-result",
        "molecule:seed",
        "produced_result",
        "assay_result:negative",
        relation_type="experimental",
        confidence=0.9,
        direction="contradictory",
        metadata={"target_entity_id": "target:MAOB", "outcome_label": "negative"},
    )
    _add_relations(store, model_support, negative_result)

    hypotheses = generate_hypothesis_candidates(store)

    contradiction = _only_pattern(hypotheses, "contradiction_resolution")
    assert contradiction.hypothesis_type == "assay_contradiction"
    assert contradiction.supporting_relation_ids == [model_support.relation_id]
    assert contradiction.contradicting_relation_ids == [negative_result.relation_id]
    assert contradiction.model_prediction_ids == ["model-prediction:1"]
    assert contradiction.assay_result_ids == ["assay_result:negative"]
    assert contradiction.contradiction_score > 0.7


def test_contradiction_resolution_detects_positive_result_and_negative_model(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("target:MAOB", "target", "MAOB"),
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity("assay_result:positive", "assay_result", "Positive imported assay result"),
    )
    positive_result = _relation(
        "rel:positive-result",
        "molecule:seed",
        "produced_result",
        "assay_result:positive",
        relation_type="experimental",
        confidence=0.88,
        direction="supportive",
        metadata={"target_entity_id": "target:MAOB", "outcome_label": "positive"},
    )
    negative_model = _relation(
        "rel:model-negative",
        "molecule:seed",
        "predicted_by_model",
        "target:MAOB",
        relation_type="model_prediction",
        confidence=0.79,
        direction="contradictory",
        metadata={"model_prediction_id": "model-prediction:negative"},
    )
    _add_relations(store, positive_result, negative_model)

    hypotheses = generate_hypothesis_candidates(store)

    contradiction = _only_pattern(hypotheses, "contradiction_resolution")
    assert contradiction.hypothesis_type == "assay_contradiction"
    assert contradiction.supporting_relation_ids == [positive_result.relation_id]
    assert contradiction.contradicting_relation_ids == [negative_model.relation_id]
    assert contradiction.assay_result_ids == ["assay_result:positive"]
    assert contradiction.model_prediction_ids == ["model-prediction:negative"]


def test_scaffold_risk_detects_repeated_blockers_across_candidates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("scaffold:core-a", "scaffold", "Core A"),
        _entity("molecule:a", "molecule", "Candidate A"),
        _entity("molecule:b", "molecule", "Candidate B"),
        _entity("developability_alert:solubility", "developability_alert", "Solubility alert"),
    )
    relations = [
        _relation("rel:a-scaffold", "molecule:a", "has_scaffold", "scaffold:core-a"),
        _relation("rel:b-scaffold", "molecule:b", "has_scaffold", "scaffold:core-a"),
        _relation(
            "rel:a-risk",
            "molecule:a",
            "has_developability_risk",
            "developability_alert:solubility",
            direction="risk",
        ),
        _relation(
            "rel:b-risk",
            "molecule:b",
            "has_developability_risk",
            "developability_alert:solubility",
            direction="risk",
        ),
    ]
    _add_relations(store, *relations)

    hypotheses = generate_hypothesis_candidates(store)

    scaffold_risk = _only_pattern(hypotheses, "scaffold_risk")
    assert scaffold_risk.hypothesis_type == "developability_risk"
    assert scaffold_risk.scaffold_entity_ids == ["scaffold:core-a"]
    assert set(scaffold_risk.molecule_entity_ids) == {"molecule:a", "molecule:b"}
    assert {"rel:a-risk", "rel:b-risk"}.issubset(set(scaffold_risk.supporting_relation_ids))
    assert scaffold_risk.metadata["repeated_blocker_count"] == 2


def test_cross_program_success_signal_uses_qc_passed_external_program_results(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("target:MAOB", "target", "MAOB"),
        _entity("mechanism:dopamine", "mechanism", "Dopamine pathway modulation"),
        _entity("molecule:a", "molecule", "Program A molecule"),
        _entity("molecule:b", "molecule", "Program B molecule"),
        _entity("assay_result:positive-a", "assay_result", "Positive result A"),
        _entity("assay_result:positive-b", "assay_result", "Positive result B"),
    )
    relations = [
        _relation("rel:a-target", "molecule:a", "targets", "target:MAOB"),
        _relation("rel:b-target", "molecule:b", "targets", "target:MAOB"),
        _relation(
            "rel:target-mechanism",
            "target:MAOB",
            "has_mechanism",
            "mechanism:dopamine",
        ),
        _relation(
            "rel:positive-a",
            "molecule:a",
            "produced_result",
            "assay_result:positive-a",
            relation_type="experimental",
            direction="supportive",
            metadata={
                "target_entity_id": "target:MAOB",
                "outcome_label": "positive",
                "qc_status": "passed",
                "program_id": "program-a",
            },
        ),
        _relation(
            "rel:positive-b",
            "molecule:b",
            "produced_result",
            "assay_result:positive-b",
            relation_type="experimental",
            direction="supportive",
            metadata={
                "target_entity_id": "target:MAOB",
                "outcome_label": "positive",
                "qc_status": "passed",
                "program_id": "program-b",
            },
        ),
    ]
    _add_relations(store, *relations)

    hypotheses = generate_hypothesis_candidates(
        store,
        portfolio_selections=[{"program_id": "program-local"}],
    )

    cross_program = _only_pattern(hypotheses, "cross_program_success_signal")
    assert cross_program.hypothesis_type == "mechanism"
    assert cross_program.target_entity_ids == ["target:MAOB"]
    assert cross_program.mechanism_entity_ids == ["mechanism:dopamine"]
    assert set(cross_program.assay_result_ids) == {
        "assay_result:positive-a",
        "assay_result:positive-b",
    }
    assert cross_program.metadata["cross_program_program_ids"] == ["program-a", "program-b"]


def test_stale_decision_flags_decision_predating_new_contradiction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_time = datetime.now(UTC) - timedelta(days=30)
    new_time = datetime.now(UTC)
    _add_entities(
        store,
        _entity("portfolio:local", "portfolio", "Local portfolio"),
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity("assay_result:new-negative", "assay_result", "New negative result"),
    )
    old_decision = _relation(
        "rel:old-portfolio-selection",
        "portfolio:local",
        "selected_in_portfolio",
        "molecule:seed",
        relation_type="review",
        direction="supportive",
        created_at=old_time,
        metadata={"decision_kind": "stage_gate", "review_decision_id": "review:old"},
    )
    new_contradiction = _relation(
        "rel:new-contradiction",
        "molecule:seed",
        "produced_result",
        "assay_result:new-negative",
        relation_type="experimental",
        direction="contradictory",
        created_at=new_time,
        metadata={"outcome_label": "negative"},
    )
    _add_relations(store, old_decision, new_contradiction)

    hypotheses = generate_hypothesis_candidates(
        store,
        staleness_reports=[
            {
                "stale_relation_ids": ["rel:old-portfolio-selection"],
                "new_contradictory_relation_ids": ["rel:new-contradiction"],
            }
        ],
    )

    stale = _only_pattern(hypotheses, "stale_decision")
    assert stale.hypothesis_type == "portfolio_decision"
    assert stale.supporting_relation_ids == [old_decision.relation_id]
    assert stale.contradicting_relation_ids == [new_contradiction.relation_id]
    assert stale.review_decision_ids == ["review:old"]
    assert stale.status == "stale"


def test_underexplored_target_detects_strong_disease_target_with_few_results(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("disease:rare", "disease", "Rare disease"),
        _entity("target:KIN1", "target", "KIN1"),
    )
    disease_target = _relation(
        "rel:rare-target",
        "disease:rare",
        "associated_with",
        "target:KIN1",
        relation_type="evidence_backed",
        confidence=0.94,
    )
    _add_relations(store, disease_target)

    hypotheses = generate_hypothesis_candidates(store)

    underexplored = _only_pattern(hypotheses, "underexplored_target")
    assert underexplored.hypothesis_type == "disease_target"
    assert underexplored.disease_entity_ids == ["disease:rare"]
    assert underexplored.target_entity_ids == ["target:KIN1"]
    assert underexplored.supporting_relation_ids == [disease_target.relation_id]
    assert underexplored.metadata["evidence_gap_type"] == "missing_molecule_target_evidence"


def test_generator_outputs_are_grounded_hypotheses_not_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add_entities(
        store,
        _entity("disease:rare", "disease", "Rare disease"),
        _entity("target:KIN1", "target", "KIN1"),
    )
    _add_relations(
        store,
        _relation(
            "rel:rare-target",
            "disease:rare",
            "associated_with",
            "target:KIN1",
            relation_type="evidence_backed",
            confidence=0.94,
        ),
    )

    hypotheses = generate_hypothesis_candidates(store)

    assert hypotheses
    for hypothesis in hypotheses:
        assert hypothesis.metadata["inferred_hypothesis"] is True
        assert hypothesis.metadata["not_evidence"] is True
        assert hypothesis.source_artifact_ids
        assert hypothesis.evidence_item_ids == []
        assert "cures" not in hypothesis.statement.lower()
        assert "treats" not in hypothesis.statement.lower()


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path)


def _entity(
    entity_id: str,
    entity_type: str,
    name: str,
    *,
    metadata: dict[str, object] | None = None,
) -> GraphEntity:
    return GraphEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        source_artifact_ids=[f"artifact:{entity_id}"],
        provenance_refs=[f"provenance:{entity_id}"],
        metadata=metadata or {},
    )


def _relation(
    relation_id: str,
    subject: str,
    predicate: str,
    object: str,
    *,
    relation_type: str = "evidence_backed",
    confidence: float = 0.8,
    direction: str | None = "supportive",
    created_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> GraphRelation:
    timestamp = created_at or datetime.now(UTC)
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object,
        relation_type=relation_type,
        confidence=confidence,
        direction=direction,
        source_artifact_ids=[f"artifact:{relation_id}"],
        source_record_ids=[f"record:{relation_id}"],
        created_at=timestamp,
        updated_at=timestamp,
        metadata=metadata or {},
    )


def _add_entities(store: KnowledgeGraphStore, *entities: GraphEntity) -> None:
    for entity in entities:
        store.upsert_entity(entity)


def _add_relations(store: KnowledgeGraphStore, *relations: GraphRelation) -> None:
    for relation in relations:
        store.upsert_relation(relation)


def _only_pattern(
    hypotheses: Sequence[ResearchHypothesis],
    pattern_name: str,
) -> ResearchHypothesis:
    matches = [
        hypothesis
        for hypothesis in hypotheses
        if getattr(hypothesis, "metadata", {}).get("pattern") == pattern_name
    ]
    assert len(matches) == 1
    return matches[0]
