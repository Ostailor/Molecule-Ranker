from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    MechanismHypothesis,
)
from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore, Neo4jGraphAdapter


def test_store_upserts_entities_deduplicates_identifiers_and_preserves_aliases(
    tmp_path: Path,
) -> None:
    store = KnowledgeGraphStore(tmp_path)
    store.create_schema()

    first = store.upsert_entity(
        GraphEntity(
            entity_id="target:MAOB",
            entity_type="target",
            name="MAOB",
            canonical_id="HGNC:6834",
            identifiers={"HGNC": "6834", "UniProt": "P27338"},
            source_artifact_ids=["run-a"],
            provenance_refs=["prov-a"],
        )
    )
    second = store.upsert_entity(
        GraphEntity(
            entity_id="target:monoamine-oxidase-b",
            entity_type="target",
            name="Monoamine oxidase B",
            canonical_id="HGNC:6834",
            identifiers={"HGNC": "6834"},
            source_artifact_ids=["run-b"],
            provenance_refs=["prov-b"],
        )
    )

    assert first.entity_id == "target:MAOB"
    assert second.entity_id == "target:MAOB"
    assert second.name == "MAOB"
    assert set(second.source_artifact_ids) == {"run-a", "run-b"}
    assert [entity.entity_id for entity in store.find_entities(identifier={"HGNC": "6834"})] == [
        "target:MAOB"
    ]

    aliases = store.aliases("target:MAOB")
    assert {"MAOB", "Monoamine oxidase B"} <= set(aliases)


def test_store_requires_relation_provenance_unless_inferred(tmp_path: Path) -> None:
    store = KnowledgeGraphStore(tmp_path)
    store.create_schema()
    store.upsert_entity(_entity("molecule:rasagiline", "molecule", "Rasagiline"))
    store.upsert_entity(_entity("target:MAOB", "target", "MAOB"))

    with pytest.raises(ValueError, match="source provenance"):
        store.upsert_relation(
            GraphRelation(
                relation_id="rel-no-source",
                subject_entity_id="molecule:rasagiline",
                predicate="targets",
                object_entity_id="target:MAOB",
                relation_type="evidence_backed",
                confidence=0.8,
            )
        )

    inferred = store.upsert_relation(
        GraphRelation(
            relation_id="rel-inferred",
            subject_entity_id="molecule:rasagiline",
            predicate="associated_with",
            object_entity_id="target:MAOB",
            relation_type="inferred",
            confidence=0.4,
        )
    )

    assert inferred.metadata["inferred_relation"] is True
    assert store.find_relations(relation_type="inferred")[0].relation_id == "rel-inferred"


def test_store_queries_relations_neighborhood_and_shortest_paths(tmp_path: Path) -> None:
    store = KnowledgeGraphStore(tmp_path)
    store.create_schema()
    for entity in [
        _entity("molecule:rasagiline", "molecule", "Rasagiline"),
        _entity("target:MAOB", "target", "MAOB"),
        _entity("mechanism:maob-inhibition", "mechanism", "MAOB inhibition"),
        _entity("assay_result:a1", "assay_result", "Assay result A1"),
    ]:
        store.upsert_entity(entity)
    store.upsert_relation(_relation("rel-targets", "molecule:rasagiline", "targets", "target:MAOB"))
    store.upsert_relation(
        _relation("rel-mech", "target:MAOB", "has_mechanism", "mechanism:maob-inhibition")
    )
    store.upsert_relation(
        _relation("rel-assay", "molecule:rasagiline", "produced_result", "assay_result:a1")
    )

    assert len(store.find_relations(subject="molecule:rasagiline")) == 2
    assert len(store.find_relations(predicate="targets")) == 1

    neighborhood = store.neighborhood("molecule:rasagiline", depth=2)
    assert {entity.entity_id for entity in neighborhood.entities} == {
        "molecule:rasagiline",
        "target:MAOB",
        "mechanism:maob-inhibition",
        "assay_result:a1",
    }

    paths = store.shortest_paths(
        "molecule:rasagiline",
        "mechanism:maob-inhibition",
        max_depth=3,
    )
    assert paths == [["molecule:rasagiline", "target:MAOB", "mechanism:maob-inhibition"]]


def test_store_persists_provenance_mechanisms_snapshots_and_json_roundtrip(
    tmp_path: Path,
) -> None:
    store = KnowledgeGraphStore(tmp_path)
    store.create_schema()
    store.upsert_entity(_entity("molecule:rasagiline", "molecule", "Rasagiline"))
    store.upsert_entity(_entity("target:MAOB", "target", "MAOB"))
    store.add_provenance(
        GraphProvenance(
            provenance_id="prov-chembl-1",
            source_type="chembl",
            source_artifact_id="artifact-chembl",
            source_record_id="CHEMBL123",
            transformation="target relation import",
            confidence=0.9,
        )
    )
    store.upsert_relation(
        GraphRelation(
            relation_id="rel-targets",
            subject_entity_id="molecule:rasagiline",
            predicate="targets",
            object_entity_id="target:MAOB",
            relation_type="evidence_backed",
            confidence=0.8,
            source_artifact_ids=["artifact-chembl"],
            source_record_ids=["CHEMBL123"],
        )
    )
    store.save_mechanism_hypothesis(
        MechanismHypothesis(
            mechanism_id="mech-1",
            target_entity_ids=["target:MAOB"],
            pathway_entity_ids=[],
            molecule_entity_ids=["molecule:rasagiline"],
            generated_molecule_entity_ids=[],
            claim_entity_ids=[],
            evidence_relation_ids=["rel-targets"],
            contradiction_relation_ids=[],
            summary="MAOB inhibition mechanism for review.",
            support_score=0.7,
            contradiction_score=0.0,
            novelty_score=0.2,
            confidence=0.6,
            status="weakly_supported",
            warnings=[],
        )
    )
    snapshot = store.create_snapshot(project_id="project-1")

    assert snapshot["project_id"] == "project-1"
    assert snapshot["entity_count"] == 2
    assert snapshot["relation_count"] == 1

    output = store.export_graph_json(tmp_path / "graph.json")
    payload = json.loads(output.read_text())
    assert payload["entities"][0]["entity_id"] == "molecule:rasagiline"
    assert payload["provenance"][0]["provenance_id"] == "prov-chembl-1"

    imported = KnowledgeGraphStore(tmp_path / "imported")
    imported.create_schema()
    imported.import_graph_json(output)
    assert imported.get_entity("target:MAOB") is not None
    assert imported.find_relations(predicate="targets")[0].source_record_ids == ["CHEMBL123"]


def test_neo4j_adapter_placeholder_is_explicit() -> None:
    with pytest.raises(NotImplementedError, match="Neo4j"):
        Neo4jGraphAdapter(uri="bolt://localhost:7687").create_schema()


def _entity(entity_id: str, entity_type: str, name: str) -> GraphEntity:
    return GraphEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _relation(relation_id: str, subject: str, predicate: str, object_id: str) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type="evidence_backed",
        confidence=0.8,
        source_artifact_ids=["artifact-1"],
        source_record_ids=[relation_id],
    )
