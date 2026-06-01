from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.identifiers import (
    IdentifierMergeResult,
    merge_identifier_sets,
    normalize_identifier,
)
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, ProvenanceSource


@dataclass
class IdentifierResolution:
    entity_id: str
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OntologyMapping:
    entity_type: str
    label: str
    identifiers: dict[str, str]
    relation: GraphRelation
    warnings: list[str] = field(default_factory=list)
    review_required: bool = False


class IdentifierMapper:
    """Deterministic alias and identifier resolver for graph nodes."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], IdentifierResolution] = {}

    def resolve(
        self,
        entity_type: str,
        name: str,
        *,
        identifiers: dict[str, str] | None = None,
    ) -> IdentifierResolution:
        identifiers = identifiers or {}
        normalized = [normalize_identifier(system, value) for system, value in identifiers.items()]
        if normalized:
            system, value = sorted(normalized)[0]
            key = (entity_type, system.lower(), value)
            entity_id = f"{entity_type}:{system.lower()}:{value}"
        else:
            key = (entity_type, "name", name.strip().lower())
            entity_id = f"{entity_type}:name:{_slug(name)}"
        resolution = self._by_key.setdefault(key, IdentifierResolution(entity_id=entity_id))
        if name and name not in resolution.aliases:
            resolution.aliases.append(name)
        return resolution


class LocalOntologyMapper:
    """Small deterministic local ontology mapping table."""

    def __init__(self, mappings: dict[tuple[str, str], dict[str, str]] | None = None) -> None:
        self.mappings: dict[tuple[str, str], dict[str, str]] = {}
        for key, identifiers in (mappings or {}).items():
            entity_type, label = key
            self.mappings[(entity_type, _norm_label(label))] = dict(
                normalize_identifier(prefix, value) for prefix, value in identifiers.items()
            )

    def lookup(self, entity_type: str, label: str) -> dict[str, str] | None:
        return self.mappings.get((entity_type, _norm_label(label)))

    def add_mapping(
        self,
        entity_type: str,
        label: str,
        identifiers: dict[str, str],
        *,
        mapping_method: str = "deterministic",
        user_confirmed: bool = False,
    ) -> IdentifierMergeResult:
        if mapping_method == "codex_suggested" and not user_confirmed:
            return IdentifierMergeResult(
                identifiers={},
                warnings=["Codex-suggested ontology mappings cannot be activated directly."],
                review_required=True,
            )
        key = (entity_type, _norm_label(label))
        existing = self.mappings.get(key, {})
        merged = merge_identifier_sets(existing, identifiers)
        if not merged.review_required:
            self.mappings[key] = merged.identifiers
        return merged

    def import_obo_owl_rdf(self, path: str) -> None:
        raise NotImplementedError(
            "Optional OBO/OWL/RDF import is a future extension and is not required "
            f"for default tests: {path}"
        )

    def export_rdf_triples(self) -> list[str]:
        triples: list[str] = []
        for (entity_type, label), identifiers in sorted(self.mappings.items()):
            subject = f"mr:ontology/{entity_type}/{_slug(label)}"
            for prefix, value in sorted(identifiers.items()):
                triples.append(f"{subject} mr:mapsTo {_rdf_identifier(prefix, value)} .")
        return triples


def map_to_ontology_terms(
    entity_type: str,
    label: str,
    *,
    identifiers: dict[str, str] | None = None,
    mapper: LocalOntologyMapper | None = None,
    source_artifact_id: str | None = None,
    mapping_method: str = "deterministic",
    user_confirmed: bool = False,
) -> list[OntologyMapping]:
    mapper = mapper or LocalOntologyMapper()
    local_identifiers = mapper.lookup(entity_type, label) or {}
    merged = merge_identifier_sets(local_identifiers, identifiers or {})
    if mapping_method == "codex_suggested" and not user_confirmed:
        return [
            _mapping(
                entity_type,
                label,
                {},
                source_artifact_id,
                warnings=["Codex-suggested ontology mapping requires deterministic validation."],
                review_required=True,
                mapping_method="codex_suggested_pending_validation",
            )
        ]
    if merged.review_required:
        return [
            _mapping(
                entity_type,
                label,
                merged.identifiers,
                source_artifact_id,
                warnings=merged.warnings,
                review_required=True,
                mapping_method=mapping_method,
            )
        ]
    if not merged.identifiers:
        return []
    active_method = "user_confirmed" if user_confirmed else mapping_method
    return [
        _mapping(
            entity_type,
            label,
            merged.identifiers,
            source_artifact_id,
            mapping_method=active_method,
        )
    ]


def entity_from_identifier(
    entity_type: str,
    name: str,
    *,
    identifiers: dict[str, str] | None = None,
    provenance: ProvenanceSource,
) -> GraphEntity:
    mapper = IdentifierMapper()
    resolution = mapper.resolve(entity_type, name, identifiers=identifiers)
    normalized = dict(normalize_identifier(k, v) for k, v in (identifiers or {}).items())
    return GraphEntity(
        entity_id=resolution.entity_id,
        entity_type=entity_type,
        name=name,
        identifiers=normalized,
        canonical_id=next(iter(normalized.values()), None),
        provenance_refs=[
            provenance.artifact_ref or f"{provenance.source_type}:{provenance.source_id}"
        ],
        source_artifact_ids=[provenance.artifact_ref] if provenance.artifact_ref else [],
    )


def export_ontology_mappings_rdf(mappings: list[OntologyMapping]) -> list[str]:
    triples: list[str] = []
    for mapping in mappings:
        subject = f"mr:ontology/{mapping.entity_type}/{_slug(mapping.label)}"
        for prefix, value in sorted(mapping.identifiers.items()):
            triples.append(f"{subject} mr:mapsTo {_rdf_identifier(prefix, value)} .")
    return triples


def _mapping(
    entity_type: str,
    label: str,
    identifiers: dict[str, str],
    source_artifact_id: str | None,
    *,
    warnings: list[str] | None = None,
    review_required: bool = False,
    mapping_method: str = "deterministic",
) -> OntologyMapping:
    subject = f"ontology_term:{entity_type}:{_slug(label)}"
    object_id = (
        _primary_ontology_object(identifiers) if identifiers else f"ontology_review:{_slug(label)}"
    )
    relation = GraphRelation(
        relation_id="ontology-map:" + uuid5(NAMESPACE_URL, subject + object_id).hex[:16],
        subject_entity_id=subject,
        predicate="same_as",
        object_entity_id=object_id,
        relation_type="ontology_mapping",
        confidence=1.0 if not review_required else 0.5,
        direction="neutral",
        source_artifact_ids=[source_artifact_id] if source_artifact_id else [],
        source_record_ids=[f"{prefix}:{value}" for prefix, value in sorted(identifiers.items())],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={
            "mapping_method": mapping_method,
            "review_required": review_required,
            "codex_suggestion_activated": False,
        },
    )
    return OntologyMapping(
        entity_type=entity_type,
        label=label,
        identifiers=identifiers,
        relation=relation,
        warnings=warnings or [],
        review_required=review_required,
    )


def _primary_ontology_object(identifiers: dict[str, str]) -> str:
    prefix, value = sorted(identifiers.items())[0]
    return f"ontology:{prefix}:{value}"


def _rdf_identifier(prefix: str, value: str) -> str:
    if prefix == "MONDO":
        return f"mondo:{value.replace(':', '_')}"
    if prefix == "EFO":
        return f"efo:{value}"
    if prefix == "MeSH":
        return f"mesh:{value}"
    return f"{prefix.lower()}:{value.replace(':', '_')}"


def _norm_label(label: str) -> str:
    return " ".join(label.lower().split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


__all__ = [
    "IdentifierMapper",
    "IdentifierResolution",
    "LocalOntologyMapper",
    "OntologyMapping",
    "entity_from_identifier",
    "export_ontology_mappings_rdf",
    "map_to_ontology_terms",
    "normalize_identifier",
]
