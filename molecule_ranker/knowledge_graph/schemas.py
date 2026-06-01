from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator, model_validator

GraphEntityType = Literal[
    "disease",
    "target",
    "pathway",
    "mechanism",
    "molecule",
    "generated_molecule",
    "scaffold",
    "chemical_series",
    "assay",
    "assay_result",
    "literature_paper",
    "literature_claim",
    "evidence_item",
    "developability_alert",
    "structure",
    "docking_pose",
    "model_prediction",
    "review_decision",
    "project",
    "program",
    "portfolio",
]
GraphPredicate = Literal[
    "associated_with",
    "targets",
    "modulates",
    "has_mechanism",
    "has_scaffold",
    "tested_in",
    "produced_result",
    "supports",
    "contradicts",
    "generated_from",
    "reviewed_as",
    "selected_in_portfolio",
    "has_developability_risk",
    "has_structure_assessment",
    "predicted_by_model",
    "same_as",
    "similar_to",
]
GraphRelationKind = Literal[
    "evidence_backed",
    "experimental",
    "literature",
    "computational",
    "review",
    "model_prediction",
    "generated_lineage",
    "ontology_mapping",
    "inferred",
]
GraphDirection = Literal["supportive", "contradictory", "neutral", "risk", "unknown"]
GraphProvenanceType = Literal[
    "opentargets",
    "chembl",
    "pubchem",
    "pubmed",
    "openalex",
    "imported_assay_result",
    "review_decision",
    "generated_artifact",
    "model_prediction",
    "codex_summary",
    "integration_sync",
]
MechanismStatus = Literal[
    "supported",
    "weakly_supported",
    "contradicted",
    "unresolved",
    "stale",
    "generated_hypothesis",
]

GRAPH_BOUNDARIES = [
    "Knowledge graph records are a memory and reasoning layer, not new biomedical truth.",
    "Graph-inferred relationships are hypotheses unless backed by source provenance.",
    "Graph inference must not create source evidence records or assay results.",
    "Graph paths do not prove causality, efficacy, safety, binding, or activity.",
    "No medical advice, synthesis instructions, lab protocols, dosing, or patient guidance.",
]


class TimezoneAwareGraphModel(BaseModel):
    @field_validator(
        "created_at",
        "updated_at",
        "retrieved_at",
        "started_at",
        "completed_at",
        "valid_from",
        "valid_until",
        "generated_at",
        check_fields=False,
    )
    @classmethod
    def require_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class GraphEntity(TimezoneAwareGraphModel):
    entity_id: str
    entity_type: GraphEntityType | str
    name: str
    canonical_id: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    source_artifact_ids: list[str] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_id", "entity_type", "name")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_entity_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "created_from" in migrated and "provenance_refs" not in migrated:
            migrated["provenance_refs"] = [
                _legacy_provenance_ref(item) for item in migrated.get("created_from") or []
            ]
        return migrated

    @model_validator(mode="after")
    def reject_record_impersonation(self) -> Self:
        dumped = str(self.metadata)
        if "EvidenceItem" in dumped or "AssayResult" in dumped:
            raise ValueError("graph entities must not impersonate EvidenceItem or AssayResult")
        return self

    @property
    def created_from(self) -> list[ProvenanceSource]:
        return [
            ProvenanceSource(source_type="graph_inference", source_id=ref)
            for ref in self.provenance_refs
        ]


class GraphRelation(TimezoneAwareGraphModel):
    relation_id: str = ""
    subject_entity_id: str
    predicate: GraphPredicate | str
    object_entity_id: str
    relation_type: GraphRelationKind | str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: GraphDirection | str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_relation_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "subject_entity_id" not in migrated and "source_entity_id" in migrated:
            migrated["subject_entity_id"] = migrated["source_entity_id"]
        if "object_entity_id" not in migrated and "target_entity_id" in migrated:
            migrated["object_entity_id"] = migrated["target_entity_id"]
        legacy_relation = migrated.get("relation_type")
        if "predicate" not in migrated and legacy_relation in _LEGACY_PREDICATES:
            migrated["predicate"] = legacy_relation
        if migrated.get("assertion_type") == "graph_inferred":
            migrated["relation_type"] = "inferred"
        elif legacy_relation in _LEGACY_PREDICATES:
            migrated["relation_type"] = _predicate_to_relation_kind(str(legacy_relation))
        if "direction" not in migrated and "polarity" in migrated:
            migrated["direction"] = _polarity_to_direction(str(migrated["polarity"]))
        if "source_record_ids" not in migrated and "evidence_source_ids" in migrated:
            migrated["source_record_ids"] = migrated.get("evidence_source_ids") or []
        if "source_artifact_ids" not in migrated and "provenance" in migrated:
            migrated["source_artifact_ids"] = [
                _legacy_provenance_ref(item) for item in migrated.get("provenance") or []
            ]
            migrated["source_record_ids"] = [
                _legacy_source_id(item) for item in migrated.get("provenance") or []
            ]
        if "stale_after_days" in migrated:
            metadata = dict(migrated.get("metadata") or {})
            metadata.setdefault("stale_after_days", migrated["stale_after_days"])
            migrated["metadata"] = metadata
        return migrated

    @model_validator(mode="after")
    def validate_graph_boundaries(self) -> Self:
        if not self.relation_id:
            self.relation_id = _relation_id(
                self.subject_entity_id,
                self.object_entity_id,
                self.predicate,
                self.relation_type,
            )
        metadata_text = str(self.metadata)
        if self.relation_type == "inferred":
            self.metadata.setdefault("inferred_relation", True)
            self.metadata.setdefault("not_evidence", True)
            if self.evidence_item_ids or "EvidenceItem" in metadata_text:
                raise ValueError(
                    "graph inference cannot create evidence. "
                    "Inferred graph relations must not become EvidenceItem records"
                )
            if "AssayResult" in metadata_text:
                raise ValueError("Inferred graph relations must not create assay results")
            if self.object_entity_id.startswith("assay_result:"):
                raise ValueError("assay-result relationships require source provenance")
            if self.predicate in {"supports", "produced_result"} and (
                self.object_entity_id.startswith("evidence_item:")
                or self.object_entity_id.startswith("assay_result:")
            ):
                raise ValueError("Inferred graph relations must not become EvidenceItem records")
        return self

    @property
    def subject(self) -> str:
        return self.subject_entity_id

    @property
    def object(self) -> str:
        return self.object_entity_id

    @property
    def source_entity_id(self) -> str:
        return self.subject_entity_id

    @property
    def target_entity_id(self) -> str:
        return self.object_entity_id

    @property
    def assertion_type(self) -> str:
        return "graph_inferred" if self.relation_type == "inferred" else "source_backed"

    @property
    def polarity(self) -> str:
        if self.direction == "supportive":
            return "supports"
        if self.direction == "contradictory":
            return "contradicts"
        return "neutral"

    @property
    def evidence_source_ids(self) -> list[str]:
        return self.source_record_ids

    @property
    def provenance(self) -> list[ProvenanceSource]:
        sources: list[ProvenanceSource] = []
        source_refs = self.source_record_ids or self.source_artifact_ids
        for index, source_id in enumerate(source_refs):
            artifact_ref = (
                self.source_artifact_ids[index] if index < len(self.source_artifact_ids) else None
            )
            sources.append(
                ProvenanceSource(
                    source_type=str(self.relation_type),
                    source_id=source_id,
                    artifact_ref=artifact_ref,
                )
            )
        return sources

    @property
    def is_inferred(self) -> bool:
        return self.relation_type == "inferred"

    @property
    def is_hypothesis(self) -> bool:
        return self.is_inferred

    @property
    def is_stale(self) -> bool:
        if not isinstance(self.stale_after_days, int):
            return False
        return (datetime.now(UTC) - self.created_at).days > self.stale_after_days

    @property
    def stale_after_days(self) -> int | None:
        value = self.metadata.get("stale_after_days")
        return value if isinstance(value, int) else None


class GraphProvenance(TimezoneAwareGraphModel):
    provenance_id: str
    source_type: GraphProvenanceType | str
    source_artifact_id: str | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    transformation: str
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MechanismHypothesis(BaseModel):
    mechanism_id: str
    disease_entity_id: str | None = None
    target_entity_ids: list[str] = Field(default_factory=list)
    pathway_entity_ids: list[str] = Field(default_factory=list)
    molecule_entity_ids: list[str] = Field(default_factory=list)
    generated_molecule_entity_ids: list[str] = Field(default_factory=list)
    claim_entity_ids: list[str] = Field(default_factory=list)
    evidence_relation_ids: list[str] = Field(default_factory=list)
    contradiction_relation_ids: list[str] = Field(default_factory=list)
    summary: str
    support_score: float = Field(ge=0.0, le=1.0)
    contradiction_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    status: MechanismStatus
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphBuildRun(TimezoneAwareGraphModel):
    graph_build_id: str
    project_id: str | None = None
    program_id: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    entity_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    provenance_count: int = Field(ge=0)
    mechanism_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraph(TimezoneAwareGraphModel):
    graph_id: str
    schema_version: str = "1.6"
    entities: list[GraphEntity] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    provenance: list[GraphProvenance] = Field(default_factory=list)
    mechanisms: list[MechanismHypothesis] = Field(default_factory=list)
    build_runs: list[GraphBuildRun] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    limitations: list[str] = Field(default_factory=lambda: list(GRAPH_BOUNDARIES))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        entity_ids = [entity.entity_id for entity in self.entities]
        duplicates = [entity_id for entity_id, count in Counter(entity_ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate graph entity ids: {', '.join(sorted(duplicates))}")
        known = set(entity_ids)
        missing = [
            relation.relation_id
            for relation in self.relations
            if relation.subject_entity_id not in known or relation.object_entity_id not in known
        ]
        if missing:
            raise ValueError(f"relations reference unknown entities: {', '.join(missing)}")
        return self

    def entity_map(self) -> dict[str, GraphEntity]:
        return {entity.entity_id: entity for entity in self.entities}


class GraphValidationReport(BaseModel):
    status: Literal["pass", "fail"]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GraphPattern(BaseModel):
    name: str
    entity_id: str
    count: int = Field(ge=0)
    program_ids: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    rationale: str


class TargetPattern(BaseModel):
    target_entity_id: str
    name: str
    strong_candidate_count: int = Field(ge=0)
    weak_candidate_count: int = Field(ge=0)
    contradiction_count: int = Field(ge=0)
    rationale: str


class GraphFinding(BaseModel):
    finding_id: str
    name: str
    status: str
    reason: str
    entity_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    severity: Literal["info", "low", "medium", "high"] = "info"


class GraphRecommendation(BaseModel):
    recommendation_id: str
    rationale: str
    reuse_entity_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendation_type: str | None = None
    graph_paths: list[dict[str, Any]] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    creates_evidence: bool = False
    claims_activity_or_safety: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrossProgramAnalysis(BaseModel):
    recurring_mechanisms: list[GraphPattern] = Field(default_factory=list)
    target_patterns: list[TargetPattern] = Field(default_factory=list)
    scaffold_patterns: list[GraphPattern] = Field(default_factory=list)
    contradictions: list[GraphFinding] = Field(default_factory=list)
    repeated_developability_risks: list[GraphPattern] = Field(default_factory=list)
    novelty_assessments: list[GraphFinding] = Field(default_factory=list)
    hypothesis_status: list[GraphFinding] = Field(default_factory=list)
    review_outcome_patterns: list[GraphFinding] = Field(default_factory=list)
    recommendations: list[GraphRecommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(GRAPH_BOUNDARIES))


class ProvenanceSource(BaseModel):
    """Compatibility provenance record used by V1.5 graph builder internals."""

    source_type: str
    source_id: str
    artifact_ref: str | None = None
    citation_ref: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def require_source_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id must not be empty")
        return value


_LEGACY_PREDICATES = {
    "associated_with",
    "targets",
    "has_mechanism",
    "shares_mechanism",
    "member_of",
    "has_scaffold",
    "has_series",
    "supported_by",
    "contradicted_by",
    "validated_by",
    "blocked_by",
    "reviewed_as",
    "correlates_with",
    "hypothesizes",
    "reuses_knowledge",
    "similar_to",
    "novel_vs_known",
}


def make_entity_id(entity_type: str, namespace: str, value: str) -> str:
    raw = value.strip() if namespace == "symbol" else value.strip().lower()
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-")
    if not safe:
        safe = uuid5(NAMESPACE_URL, f"{entity_type}:{namespace}:{value}").hex[:12]
    if namespace == entity_type:
        return f"{entity_type}:{safe}"
    return f"{entity_type}:{namespace}:{safe}"


def _relation_id(*parts: str) -> str:
    return "rel:" + uuid5(NAMESPACE_URL, "|".join(parts)).hex[:16]


def _predicate_to_relation_kind(predicate: str) -> str:
    if predicate in {"validated_by", "contradicted_by", "produced_result", "tested_in"}:
        return "experimental"
    if predicate in {"supported_by", "supports"}:
        return "evidence_backed"
    if predicate == "reviewed_as":
        return "review"
    if predicate == "generated_from":
        return "generated_lineage"
    if predicate in {"same_as", "similar_to"}:
        return "ontology_mapping"
    return "computational"


def _polarity_to_direction(polarity: str) -> str:
    if polarity == "supports":
        return "supportive"
    if polarity == "contradicts":
        return "contradictory"
    return "neutral"


def _legacy_provenance_ref(value: Any) -> str:
    if isinstance(value, ProvenanceSource):
        return value.artifact_ref or f"{value.source_type}:{value.source_id}"
    if isinstance(value, dict):
        return str(
            value.get("artifact_ref") or f"{value.get('source_type')}:{value.get('source_id')}"
        )
    return str(value)


def _legacy_source_id(value: Any) -> str:
    if isinstance(value, ProvenanceSource):
        return value.source_id
    if isinstance(value, dict):
        return str(value.get("source_id") or "")
    return str(value)
