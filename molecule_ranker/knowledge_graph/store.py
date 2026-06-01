from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)

from molecule_ranker.knowledge_graph.schemas import (
    GraphBuildRun,
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    KnowledgeGraph,
    MechanismHypothesis,
)


class Neo4jGraphAdapter:
    """Placeholder for future graph-native deployments."""

    def __init__(self, *, uri: str, username: str | None = None) -> None:
        self.uri = uri
        self.username = username

    def create_schema(self) -> None:
        raise NotImplementedError(
            "Neo4j graph storage is a planned adapter; use KnowledgeGraphStore relational "
            "storage for current runtime deployments."
        )


class KnowledgeGraphStore:
    def __init__(
        self,
        root_dir: Path,
        *,
        database_url: str | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.root_dir = root_dir.resolve()
        self.state_dir = self.root_dir / ".molecule-ranker"
        self.graph_dir = self.state_dir / "knowledge_graphs"
        self.audit_path = self.graph_dir / "audit.jsonl"
        self.db_path = db_path.resolve() if db_path else self.state_dir / "knowledge_graph.sqlite"
        self.database_url = database_url or f"sqlite:///{self.db_path}"
        self.engine = create_engine(self.database_url, future=True)
        self.metadata = MetaData()
        self.graph_entities = Table(
            "graph_entities",
            self.metadata,
            Column("entity_id", String(512), primary_key=True),
            Column("entity_type", String(128), nullable=False),
            Column("name", String(1024), nullable=False),
            Column("canonical_id", String(512), nullable=True),
            Column("identifiers_json", Text, nullable=False),
            Column("source_artifact_ids_json", Text, nullable=False),
            Column("provenance_refs_json", Text, nullable=False),
            Column("created_at", String(64), nullable=False),
            Column("updated_at", String(64), nullable=False),
            Column("metadata_json", Text, nullable=False),
        )
        self.graph_relations = Table(
            "graph_relations",
            self.metadata,
            Column("relation_id", String(512), primary_key=True),
            Column("subject_entity_id", String(512), nullable=False),
            Column("predicate", String(128), nullable=False),
            Column("object_entity_id", String(512), nullable=False),
            Column("relation_type", String(128), nullable=False),
            Column("confidence", String(32), nullable=False),
            Column("direction", String(128), nullable=True),
            Column("source_artifact_ids_json", Text, nullable=False),
            Column("source_record_ids_json", Text, nullable=False),
            Column("evidence_item_ids_json", Text, nullable=False),
            Column("created_at", String(64), nullable=False),
            Column("updated_at", String(64), nullable=False),
            Column("valid_from", String(64), nullable=True),
            Column("valid_until", String(64), nullable=True),
            Column("metadata_json", Text, nullable=False),
        )
        self.graph_provenance = Table(
            "graph_provenance",
            self.metadata,
            Column("provenance_id", String(512), primary_key=True),
            Column("source_type", String(128), nullable=False),
            Column("source_artifact_id", String(512), nullable=True),
            Column("source_record_id", String(512), nullable=True),
            Column("source_url", Text, nullable=True),
            Column("retrieved_at", String(64), nullable=True),
            Column("transformation", Text, nullable=False),
            Column("confidence", String(32), nullable=False),
            Column("metadata_json", Text, nullable=False),
        )
        self.mechanism_hypotheses = Table(
            "mechanism_hypotheses",
            self.metadata,
            Column("mechanism_id", String(512), primary_key=True),
            Column("payload_json", Text, nullable=False),
        )
        self.graph_build_runs = Table(
            "graph_build_runs",
            self.metadata,
            Column("graph_build_id", String(512), primary_key=True),
            Column("payload_json", Text, nullable=False),
        )
        self.graph_aliases = Table(
            "graph_aliases",
            self.metadata,
            Column("alias_id", String(512), primary_key=True),
            Column("entity_id", String(512), nullable=False),
            Column("alias", String(1024), nullable=False),
            Column("created_at", String(64), nullable=False),
            Column("metadata_json", Text, nullable=False),
        )
        self.graph_snapshots = Table(
            "graph_snapshots",
            self.metadata,
            Column("snapshot_id", String(512), primary_key=True),
            Column("project_id", String(512), nullable=True),
            Column("program_id", String(512), nullable=True),
            Column("created_at", String(64), nullable=False),
            Column("entity_count", String(32), nullable=False),
            Column("relation_count", String(32), nullable=False),
            Column("provenance_count", String(32), nullable=False),
            Column("mechanism_count", String(32), nullable=False),
            Column("graph_json", Text, nullable=False),
        )

    def create_schema(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        self.metadata.create_all(self.engine)

    def upsert_entity(self, entity: GraphEntity) -> GraphEntity:
        self.create_schema()
        existing = self._find_duplicate_entity(entity)
        active = self._merge_entities(existing, entity) if existing else entity
        with self.engine.begin() as connection:
            row = _entity_row(active)
            if self._entity_exists(connection, active.entity_id):
                connection.execute(
                    update(self.graph_entities)
                    .where(self.graph_entities.c.entity_id == active.entity_id)
                    .values(**row)
                )
            else:
                connection.execute(insert(self.graph_entities).values(**row))
            self._insert_alias(connection, active.entity_id, active.name)
            if existing and entity.name != active.name:
                self._insert_alias(connection, active.entity_id, entity.name)
        return active

    def upsert_relation(self, relation: GraphRelation) -> GraphRelation:
        self.create_schema()
        self._validate_relation_provenance(relation)
        with self.engine.begin() as connection:
            row = _relation_row(relation)
            exists = connection.execute(
                select(self.graph_relations.c.relation_id).where(
                    self.graph_relations.c.relation_id == relation.relation_id
                )
            ).first()
            if exists:
                connection.execute(
                    update(self.graph_relations)
                    .where(self.graph_relations.c.relation_id == relation.relation_id)
                    .values(**row)
                )
            else:
                connection.execute(insert(self.graph_relations).values(**row))
        return relation

    def add_provenance(self, provenance: GraphProvenance) -> GraphProvenance:
        self.create_schema()
        with self.engine.begin() as connection:
            row = _provenance_row(provenance)
            exists = connection.execute(
                select(self.graph_provenance.c.provenance_id).where(
                    self.graph_provenance.c.provenance_id == provenance.provenance_id
                )
            ).first()
            if exists:
                connection.execute(
                    update(self.graph_provenance)
                    .where(self.graph_provenance.c.provenance_id == provenance.provenance_id)
                    .values(**row)
                )
            else:
                connection.execute(insert(self.graph_provenance).values(**row))
        return provenance

    def get_entity(self, entity_id: str) -> GraphEntity | None:
        self.create_schema()
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(self.graph_entities).where(self.graph_entities.c.entity_id == entity_id)
                )
                .mappings()
                .first()
            )
        return _entity_from_row(row) if row else None

    def find_entities(
        self,
        *,
        entity_type: str | None = None,
        name: str | None = None,
        identifier: dict[str, str] | tuple[str, str] | str | None = None,
    ) -> list[GraphEntity]:
        self.create_schema()
        with self.engine.connect() as connection:
            query = select(self.graph_entities)
            if entity_type:
                query = query.where(self.graph_entities.c.entity_type == entity_type)
            rows = connection.execute(query).mappings().all()
        entities = [_entity_from_row(row) for row in rows]
        if name is not None:
            lowered = name.lower()
            entity_ids = set()
            with self.engine.connect() as connection:
                alias_rows = connection.execute(select(self.graph_aliases)).mappings().all()
            for row in alias_rows:
                if lowered in str(row["alias"]).lower():
                    entity_ids.add(str(row["entity_id"]))
            entities = [
                entity
                for entity in entities
                if lowered in entity.name.lower() or entity.entity_id in entity_ids
            ]
        if identifier is not None:
            system, value = _identifier_pair(identifier)
            entities = [
                entity
                for entity in entities
                if entity.identifiers.get(system) == value
                or entity.identifiers.get(system.upper()) == value
                or entity.identifiers.get(system.lower()) == value
            ]
        return sorted(entities, key=lambda entity: entity.entity_id)

    def find_relations(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        relation_type: str | None = None,
    ) -> list[GraphRelation]:
        self.create_schema()
        with self.engine.connect() as connection:
            query = select(self.graph_relations)
            if subject:
                query = query.where(self.graph_relations.c.subject_entity_id == subject)
            if predicate:
                query = query.where(self.graph_relations.c.predicate == predicate)
            if object:
                query = query.where(self.graph_relations.c.object_entity_id == object)
            if relation_type:
                query = query.where(self.graph_relations.c.relation_type == relation_type)
            rows = connection.execute(query).mappings().all()
        return [_relation_from_row(row) for row in rows]

    def neighborhood(self, entity_id: str, depth: int = 1) -> KnowledgeGraph:
        self.create_schema()
        if depth < 0:
            raise ValueError("depth must be non-negative")
        visited = {entity_id}
        frontier = {entity_id}
        included_relations: dict[str, GraphRelation] = {}
        relations = self.find_relations()
        for _ in range(depth):
            next_frontier: set[str] = set()
            for relation in relations:
                if relation.subject_entity_id in frontier or relation.object_entity_id in frontier:
                    included_relations[relation.relation_id] = relation
                    for node in [relation.subject_entity_id, relation.object_entity_id]:
                        if node not in visited:
                            visited.add(node)
                            next_frontier.add(node)
            frontier = next_frontier
            if not frontier:
                break
        entities = [
            entity for entity_id in sorted(visited) if (entity := self.get_entity(entity_id))
        ]
        return KnowledgeGraph(
            graph_id=f"neighborhood:{entity_id}",
            entities=entities,
            relations=sorted(included_relations.values(), key=lambda item: item.relation_id),
            provenance=self._all_provenance(),
            mechanisms=self._all_mechanisms(),
            build_runs=self._all_build_runs(),
        )

    def shortest_paths(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 4,
    ) -> list[list[str]]:
        self.create_schema()
        if max_depth < 1:
            return []
        adjacency: dict[str, set[str]] = {}
        for relation in self.find_relations():
            adjacency.setdefault(relation.subject_entity_id, set()).add(relation.object_entity_id)
            adjacency.setdefault(relation.object_entity_id, set()).add(relation.subject_entity_id)
        queue: deque[list[str]] = deque([[source_id]])
        shortest: list[list[str]] = []
        shortest_length: int | None = None
        while queue:
            path = queue.popleft()
            if shortest_length is not None and len(path) > shortest_length:
                continue
            node = path[-1]
            if node == target_id:
                shortest_length = len(path)
                shortest.append(path)
                continue
            if len(path) > max_depth:
                continue
            for neighbor in sorted(adjacency.get(node, set())):
                if neighbor not in path:
                    queue.append([*path, neighbor])
        return shortest

    def save_mechanism_hypothesis(
        self,
        hypothesis: MechanismHypothesis,
    ) -> MechanismHypothesis:
        self.create_schema()
        with self.engine.begin() as connection:
            payload = json.dumps(hypothesis.model_dump(mode="json"), sort_keys=True)
            exists = connection.execute(
                select(self.mechanism_hypotheses.c.mechanism_id).where(
                    self.mechanism_hypotheses.c.mechanism_id == hypothesis.mechanism_id
                )
            ).first()
            if exists:
                connection.execute(
                    update(self.mechanism_hypotheses)
                    .where(self.mechanism_hypotheses.c.mechanism_id == hypothesis.mechanism_id)
                    .values(payload_json=payload)
                )
            else:
                connection.execute(
                    insert(self.mechanism_hypotheses).values(
                        mechanism_id=hypothesis.mechanism_id,
                        payload_json=payload,
                    )
                )
        return hypothesis

    def create_snapshot(
        self,
        *,
        project_id: str | None = None,
        program_id: str | None = None,
    ) -> dict[str, Any]:
        self.create_schema()
        graph = self._graph_from_store(graph_id=f"snapshot:{uuid4().hex[:12]}")
        snapshot = {
            "snapshot_id": f"snapshot:{uuid4().hex[:16]}",
            "project_id": project_id,
            "program_id": program_id,
            "created_at": datetime.now(UTC).isoformat(),
            "entity_count": len(graph.entities),
            "relation_count": len(graph.relations),
            "provenance_count": len(graph.provenance),
            "mechanism_count": len(graph.mechanisms),
            "graph": graph.model_dump(mode="json"),
        }
        with self.engine.begin() as connection:
            connection.execute(
                insert(self.graph_snapshots).values(
                    snapshot_id=snapshot["snapshot_id"],
                    project_id=project_id,
                    program_id=program_id,
                    created_at=snapshot["created_at"],
                    entity_count=str(snapshot["entity_count"]),
                    relation_count=str(snapshot["relation_count"]),
                    provenance_count=str(snapshot["provenance_count"]),
                    mechanism_count=str(snapshot["mechanism_count"]),
                    graph_json=json.dumps(snapshot["graph"], sort_keys=True),
                )
            )
        return snapshot

    def export_graph_json(self, output_path: Path) -> Path:
        self.create_schema()
        graph = self._graph_from_store(graph_id="knowledge-graph-export")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        return output_path

    def import_graph_json(self, input_path: Path) -> KnowledgeGraph:
        self.create_schema()
        graph = KnowledgeGraph.model_validate(json.loads(input_path.read_text()))
        for entity in graph.entities:
            self.upsert_entity(entity)
        for provenance in graph.provenance:
            self.add_provenance(provenance)
        for relation in graph.relations:
            self.upsert_relation(relation)
        for mechanism in graph.mechanisms:
            self.save_mechanism_hypothesis(mechanism)
        for build in graph.build_runs:
            self._save_build_run(build)
        return graph

    def export_rdf_turtle(self, output_path: Path) -> Path:
        self.create_schema()
        lines = ["@prefix mr: <https://molecule-ranker.local/kg/> .", ""]
        for relation in self.find_relations():
            lines.append(
                f"mr:{_rdf_id(relation.subject_entity_id)} "
                f"mr:{_rdf_id(relation.predicate)} "
                f"mr:{_rdf_id(relation.object_entity_id)} ."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n")
        return output_path

    def save(
        self,
        graph: KnowledgeGraph,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Path:
        self.create_schema()
        for entity in graph.entities:
            self.upsert_entity(entity)
        for provenance in graph.provenance:
            self.add_provenance(provenance)
        for relation in graph.relations:
            self.upsert_relation(relation)
        for mechanism in graph.mechanisms:
            self.save_mechanism_hypothesis(mechanism)
        for build in graph.build_runs:
            self._save_build_run(build)
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        path = self.graph_dir / f"{graph.graph_id}.json"
        path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        self._audit(
            {
                "event_type": "knowledge_graph_saved",
                "graph_id": graph.graph_id,
                "actor": actor,
                "reason": reason,
                "entity_count": len(graph.entities),
                "relation_count": len(graph.relations),
            }
        )
        return path

    def load(self, graph_id: str) -> KnowledgeGraph:
        path = self.graph_dir / f"{graph_id}.json"
        if path.exists():
            return KnowledgeGraph.model_validate(json.loads(path.read_text()))
        self.create_schema()
        return self._graph_from_store(graph_id=graph_id)

    def list_graphs(self) -> list[str]:
        self.create_schema()
        file_graphs = sorted(path.stem for path in self.graph_dir.glob("*.json"))
        return file_graphs or ["knowledge-graph-export"]

    def aliases(self, entity_id: str) -> list[str]:
        self.create_schema()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.graph_aliases.c.alias).where(
                    self.graph_aliases.c.entity_id == entity_id
                )
            ).all()
        return sorted(str(row.alias) for row in rows)

    def audit_events(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        return [
            json.loads(line) for line in self.audit_path.read_text().splitlines() if line.strip()
        ]

    def _find_duplicate_entity(self, entity: GraphEntity) -> GraphEntity | None:
        if entity.canonical_id:
            matches = self.find_entities()
            for candidate in matches:
                if candidate.canonical_id == entity.canonical_id:
                    return candidate
        for system, value in entity.identifiers.items():
            matches = self.find_entities(identifier={system: value})
            if matches:
                return matches[0]
        return self.get_entity(entity.entity_id)

    def _merge_entities(self, existing: GraphEntity, incoming: GraphEntity) -> GraphEntity:
        identifiers = {**existing.identifiers, **incoming.identifiers}
        source_artifact_ids = sorted({*existing.source_artifact_ids, *incoming.source_artifact_ids})
        provenance_refs = sorted({*existing.provenance_refs, *incoming.provenance_refs})
        metadata = {**incoming.metadata, **existing.metadata}
        return existing.model_copy(
            update={
                "canonical_id": existing.canonical_id or incoming.canonical_id,
                "identifiers": identifiers,
                "source_artifact_ids": source_artifact_ids,
                "provenance_refs": provenance_refs,
                "updated_at": datetime.now(UTC),
                "metadata": metadata,
            }
        )

    def _entity_exists(self, connection: Any, entity_id: str) -> bool:
        return bool(
            connection.execute(
                select(self.graph_entities.c.entity_id).where(
                    self.graph_entities.c.entity_id == entity_id
                )
            ).first()
        )

    def _insert_alias(self, connection: Any, entity_id: str, alias: str) -> None:
        alias_id = f"alias:{uuid5(NAMESPACE_URL, entity_id + ':' + alias).hex[:16]}"
        exists = connection.execute(
            select(self.graph_aliases.c.alias_id).where(self.graph_aliases.c.alias_id == alias_id)
        ).first()
        if exists:
            return
        connection.execute(
            insert(self.graph_aliases).values(
                alias_id=alias_id,
                entity_id=entity_id,
                alias=alias,
                created_at=datetime.now(UTC).isoformat(),
                metadata_json="{}",
            )
        )

    def _validate_relation_provenance(self, relation: GraphRelation) -> None:
        has_source = bool(
            relation.source_artifact_ids or relation.source_record_ids or relation.evidence_item_ids
        )
        if relation.relation_type != "inferred" and not has_source:
            raise ValueError(
                "Graph relation insertion requires source provenance or relation_type='inferred'."
            )

    def _all_provenance(self) -> list[GraphProvenance]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.graph_provenance)).mappings().all()
        return [_provenance_from_row(row) for row in rows]

    def _all_mechanisms(self) -> list[MechanismHypothesis]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.mechanism_hypotheses)).mappings().all()
        return [
            MechanismHypothesis.model_validate(json.loads(str(row["payload_json"]))) for row in rows
        ]

    def _all_build_runs(self) -> list[GraphBuildRun]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.graph_build_runs)).mappings().all()
        return [GraphBuildRun.model_validate(json.loads(str(row["payload_json"]))) for row in rows]

    def _save_build_run(self, build: GraphBuildRun) -> GraphBuildRun:
        self.create_schema()
        payload = json.dumps(build.model_dump(mode="json"), sort_keys=True)
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(self.graph_build_runs.c.graph_build_id).where(
                    self.graph_build_runs.c.graph_build_id == build.graph_build_id
                )
            ).first()
            if exists:
                connection.execute(
                    update(self.graph_build_runs)
                    .where(self.graph_build_runs.c.graph_build_id == build.graph_build_id)
                    .values(payload_json=payload)
                )
            else:
                connection.execute(
                    insert(self.graph_build_runs).values(
                        graph_build_id=build.graph_build_id,
                        payload_json=payload,
                    )
                )
        return build

    def _graph_from_store(self, *, graph_id: str) -> KnowledgeGraph:
        entities = self.find_entities()
        relations = self.find_relations()
        return KnowledgeGraph(
            graph_id=graph_id,
            entities=entities,
            relations=relations,
            provenance=self._all_provenance(),
            mechanisms=self._all_mechanisms(),
            build_runs=self._all_build_runs(),
        )

    def _audit(self, payload: dict[str, Any]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": datetime.now(UTC).isoformat(), **payload}
        with self.audit_path.open("a") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _entity_row(entity: GraphEntity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "entity_type": entity.entity_type,
        "name": entity.name,
        "canonical_id": entity.canonical_id,
        "identifiers_json": json.dumps(entity.identifiers, sort_keys=True),
        "source_artifact_ids_json": json.dumps(entity.source_artifact_ids, sort_keys=True),
        "provenance_refs_json": json.dumps(entity.provenance_refs, sort_keys=True),
        "created_at": entity.created_at.isoformat(),
        "updated_at": entity.updated_at.isoformat(),
        "metadata_json": json.dumps(entity.metadata, sort_keys=True),
    }


def _entity_from_row(row: Any) -> GraphEntity:
    return GraphEntity(
        entity_id=str(row["entity_id"]),
        entity_type=str(row["entity_type"]),
        name=str(row["name"]),
        canonical_id=row["canonical_id"],
        identifiers=json.loads(str(row["identifiers_json"])),
        source_artifact_ids=json.loads(str(row["source_artifact_ids_json"])),
        provenance_refs=json.loads(str(row["provenance_refs_json"])),
        created_at=_datetime(str(row["created_at"])),
        updated_at=_datetime(str(row["updated_at"])),
        metadata=json.loads(str(row["metadata_json"])),
    )


def _relation_row(relation: GraphRelation) -> dict[str, Any]:
    return {
        "relation_id": relation.relation_id,
        "subject_entity_id": relation.subject_entity_id,
        "predicate": relation.predicate,
        "object_entity_id": relation.object_entity_id,
        "relation_type": relation.relation_type,
        "confidence": str(relation.confidence),
        "direction": relation.direction,
        "source_artifact_ids_json": json.dumps(relation.source_artifact_ids, sort_keys=True),
        "source_record_ids_json": json.dumps(relation.source_record_ids, sort_keys=True),
        "evidence_item_ids_json": json.dumps(relation.evidence_item_ids, sort_keys=True),
        "created_at": relation.created_at.isoformat(),
        "updated_at": relation.updated_at.isoformat(),
        "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
        "valid_until": relation.valid_until.isoformat() if relation.valid_until else None,
        "metadata_json": json.dumps(relation.metadata, sort_keys=True),
    }


def _relation_from_row(row: Any) -> GraphRelation:
    return GraphRelation(
        relation_id=str(row["relation_id"]),
        subject_entity_id=str(row["subject_entity_id"]),
        predicate=str(row["predicate"]),
        object_entity_id=str(row["object_entity_id"]),
        relation_type=str(row["relation_type"]),
        confidence=float(row["confidence"]),
        direction=row["direction"],
        source_artifact_ids=json.loads(str(row["source_artifact_ids_json"])),
        source_record_ids=json.loads(str(row["source_record_ids_json"])),
        evidence_item_ids=json.loads(str(row["evidence_item_ids_json"])),
        created_at=_datetime(str(row["created_at"])),
        updated_at=_datetime(str(row["updated_at"])),
        valid_from=_datetime(row["valid_from"]) if row["valid_from"] else None,
        valid_until=_datetime(row["valid_until"]) if row["valid_until"] else None,
        metadata=json.loads(str(row["metadata_json"])),
    )


def _provenance_row(provenance: GraphProvenance) -> dict[str, Any]:
    return {
        "provenance_id": provenance.provenance_id,
        "source_type": provenance.source_type,
        "source_artifact_id": provenance.source_artifact_id,
        "source_record_id": provenance.source_record_id,
        "source_url": provenance.source_url,
        "retrieved_at": provenance.retrieved_at.isoformat() if provenance.retrieved_at else None,
        "transformation": provenance.transformation,
        "confidence": str(provenance.confidence),
        "metadata_json": json.dumps(provenance.metadata, sort_keys=True),
    }


def _provenance_from_row(row: Any) -> GraphProvenance:
    return GraphProvenance(
        provenance_id=str(row["provenance_id"]),
        source_type=str(row["source_type"]),
        source_artifact_id=row["source_artifact_id"],
        source_record_id=row["source_record_id"],
        source_url=row["source_url"],
        retrieved_at=_datetime(row["retrieved_at"]) if row["retrieved_at"] else None,
        transformation=str(row["transformation"]),
        confidence=float(row["confidence"]),
        metadata=json.loads(str(row["metadata_json"])),
    )


def _identifier_pair(identifier: dict[str, str] | tuple[str, str] | str) -> tuple[str, str]:
    if isinstance(identifier, dict):
        return next(iter(identifier.items()))
    if isinstance(identifier, tuple):
        return identifier
    if ":" in identifier:
        system, value = identifier.split(":", 1)
        return system, value
    raise ValueError("identifier string must use 'system:value' format")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _rdf_id(value: str) -> str:
    return value.replace(":", "_").replace(" ", "_").replace("/", "_")
