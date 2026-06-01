from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.knowledge_graph.schemas import (
    GraphBuildRun,
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    MechanismHypothesis,
)


def test_graph_entity_schema_matches_v15_contract() -> None:
    now = datetime.now(UTC)

    entity = GraphEntity(
        entity_id="target:MAOB",
        entity_type="target",
        name="MAOB",
        canonical_id="HGNC:6834",
        identifiers={"HGNC": "6834"},
        source_artifact_ids=["ranking-run-1"],
        provenance_refs=["prov-opentargets-1"],
        created_at=now,
        updated_at=now,
        metadata={"organism": "human"},
    )

    assert entity.entity_type == "target"
    assert entity.canonical_id == "HGNC:6834"
    assert entity.source_artifact_ids == ["ranking-run-1"]
    assert entity.provenance_refs == ["prov-opentargets-1"]


def test_graph_relation_schema_bounds_confidence_and_labels_inferred_relations() -> None:
    now = datetime.now(UTC)

    relation = GraphRelation(
        relation_id="rel-1",
        subject_entity_id="molecule:rasagiline",
        predicate="has_mechanism",
        object_entity_id="mechanism:maob-inhibition",
        relation_type="inferred",
        confidence=0.44,
        direction="supportive",
        source_artifact_ids=["graph-build-1"],
        source_record_ids=["cooccurrence:1"],
        evidence_item_ids=[],
        created_at=now,
        updated_at=now,
        valid_from=now,
        metadata={},
    )

    assert relation.is_inferred is True
    assert relation.metadata["inferred_relation"] is True
    assert relation.metadata["not_evidence"] is True

    with pytest.raises(ValueError, match="confidence"):
        GraphRelation(
            relation_id="rel-bad",
            subject_entity_id="a",
            predicate="associated_with",
            object_entity_id="b",
            relation_type="computational",
            confidence=1.5,
            created_at=now,
            updated_at=now,
        )


def test_inferred_graph_relations_cannot_be_promoted_to_evidence_items() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="Inferred graph relations must not become EvidenceItem"):
        GraphRelation(
            relation_id="rel-evidence",
            subject_entity_id="target:MAOB",
            predicate="supports",
            object_entity_id="evidence_item:fake",
            relation_type="inferred",
            confidence=0.5,
            evidence_item_ids=["fake-evidence"],
            created_at=now,
            updated_at=now,
            metadata={"creates": "EvidenceItem"},
        )


def test_graph_provenance_mechanism_and_build_run_validate_scores_and_timezones() -> None:
    now = datetime.now(UTC)

    provenance = GraphProvenance(
        provenance_id="prov-1",
        source_type="pubmed",
        source_artifact_id="lit-artifact-1",
        source_record_id="123456",
        source_url="https://pubmed.ncbi.nlm.nih.gov/123456/",
        retrieved_at=now,
        transformation="literature claim extraction",
        confidence=0.82,
    )
    mechanism = MechanismHypothesis(
        mechanism_id="mech-1",
        disease_entity_id="disease:parkinson",
        target_entity_ids=["target:MAOB"],
        pathway_entity_ids=[],
        molecule_entity_ids=["molecule:rasagiline"],
        generated_molecule_entity_ids=[],
        claim_entity_ids=["literature_claim:1"],
        evidence_relation_ids=["rel-support"],
        contradiction_relation_ids=[],
        summary="MAOB inhibition hypothesis for review.",
        support_score=0.7,
        contradiction_score=0.1,
        novelty_score=0.3,
        confidence=0.62,
        status="weakly_supported",
        warnings=[],
    )
    build = GraphBuildRun(
        graph_build_id="build-1",
        project_id="project-1",
        program_id=None,
        input_artifact_ids=["ranking-run-1"],
        entity_count=3,
        relation_count=2,
        provenance_count=1,
        mechanism_count=1,
        warnings=[],
        started_at=now,
        completed_at=now,
    )

    assert provenance.confidence == 0.82
    assert mechanism.status == "weakly_supported"
    assert build.mechanism_count == 1

    with pytest.raises(ValueError, match="timezone-aware"):
        GraphBuildRun(
            graph_build_id="build-naive",
            input_artifact_ids=[],
            entity_count=0,
            relation_count=0,
            provenance_count=0,
            mechanism_count=0,
            warnings=[],
            started_at=datetime.utcnow(),
        )
