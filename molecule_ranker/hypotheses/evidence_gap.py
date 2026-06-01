from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.hypotheses.schemas import EvidenceGap, ResearchHypothesis
from molecule_ranker.hypotheses.validation import detect_hypothesis_guardrail_violations
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph

__all__ = [
    "EvidenceGap",
    "EvidenceGapAnalyzer",
    "analyze_evidence_gaps",
    "analyze_evidence_gaps_for_hypotheses",
    "analyze_hypothesis_evidence_gaps",
]

BOUNDARY_NOTE = (
    "Evidence gaps are not failures. Absence of evidence is not evidence of absence."
)
SEVERITY_RATIONALE = {
    "critical": "blocks any follow-up planning",
    "high": "blocks assay triage, requires expert review",
    "medium": "useful follow-up but not blocking",
    "low": "nice-to-have context",
}


class EvidenceGapAnalyzer:
    """Detect V1.6 evidence gaps for graph-backed research hypotheses."""

    def __init__(
        self,
        *,
        entities: Iterable[GraphEntity] | Mapping[str, GraphEntity] | None = None,
        relations: Iterable[GraphRelation] | None = None,
    ) -> None:
        self.entities: dict[str, GraphEntity]
        if entities is None:
            self.entities = {}
        elif isinstance(entities, Mapping):
            self.entities = dict(cast(Mapping[str, GraphEntity], entities))
        else:
            self.entities = {entity.entity_id: entity for entity in entities}
        self.relations = sorted(list(relations or []), key=lambda relation: relation.relation_id)
        self.relations_by_id = {relation.relation_id: relation for relation in self.relations}

    def analyze(self, hypothesis: ResearchHypothesis) -> list[EvidenceGap]:
        related_relations = self._related_relations(hypothesis)
        gaps: list[EvidenceGap] = []

        if self._missing_direct_result(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_direct_experimental_result",
                    "No direct imported experimental result is linked to the exact "
                    "hypothesis entities.",
                    self._direct_result_severity(hypothesis),
                    related_relations,
                    "High-level review of imported result coverage for the exact graph entities.",
                    sub_type=(
                        "generated_molecule_no_exact_assay_result"
                        if hypothesis.generated_molecule_entity_ids
                        else "missing_direct_experimental_result"
                    ),
                )
            )

        if self._missing_target_engagement(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_target_evidence",
                    "Target engagement evidence is not linked for the exact "
                    "molecule-target context.",
                    "high",
                    related_relations,
                    "High-level review of target engagement evidence linked to the exact entities.",
                    sub_type="missing_target_engagement_evidence",
                )
            )

        if self._missing_selectivity(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_selectivity_data",
                    "Selectivity context is not linked for the molecule or series.",
                    "medium",
                    related_relations,
                    "High-level review of selectivity context across linked targets or series.",
                )
            )

        if self._missing_safety_context(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_safety_data",
                    "Safety context is not linked for the molecule or generated molecule.",
                    "medium",
                    related_relations,
                    "High-level review of safety and liability context from imported sources.",
                    sub_type="missing_safety_context",
                )
            )

        if self._missing_developability_context(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_developability_data",
                    "Developability context is not linked for the molecule or generated molecule.",
                    "medium",
                    related_relations,
                    "High-level review of developability assessments from imported sources.",
                    sub_type="missing_developability_context",
                )
            )

        if self._missing_literature_support(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_literature",
                    "Literature support is not linked to this hypothesis.",
                    "low",
                    related_relations,
                    "High-level review of source-backed literature claims for the hypothesis.",
                )
            )

        contradiction_relations = self._contradictory_relations(hypothesis, related_relations)
        if contradiction_relations:
            gaps.append(
                self._gap(
                    hypothesis,
                    "contradictory_results",
                    "Contradictory evidence is present and remains unresolved.",
                    self._contradiction_severity(hypothesis),
                    contradiction_relations,
                    "Expert review of contradiction scope, provenance, and affected decisions.",
                )
            )

        stale_model_relations = self._stale_model_relations(hypothesis, related_relations)
        if stale_model_relations:
            gaps.append(
                self._gap(
                    hypothesis,
                    "stale_model_prediction",
                    "A linked model prediction is stale relative to its metadata or age.",
                    "medium",
                    stale_model_relations,
                    "High-level review of whether model context should be refreshed.",
                )
            )

        if self._generated_unreviewed(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "unreviewed_generated_molecule",
                    "Generated molecule has not been linked to review support.",
                    "high",
                    related_relations,
                    "Expert review of generated-molecule readiness and graph provenance.",
                )
            )

        if self._missing_structure_context(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_structure_context",
                    "Structure context is not linked for the target or molecule context.",
                    "medium",
                    related_relations,
                    "High-level review of available structure or pose assessment context.",
                )
            )

        weak_mapping_relations = self._weak_external_mapping_relations(related_relations)
        if weak_mapping_relations:
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_target_evidence",
                    "External mapping support is weak for at least one linked graph relation.",
                    "medium",
                    weak_mapping_relations,
                    "High-level review of external identifier mapping confidence.",
                    sub_type="weak_external_mapping",
                )
            )

        if self._weak_review_support(hypothesis, related_relations):
            gaps.append(
                self._gap(
                    hypothesis,
                    "missing_literature",
                    "Review support is weak or absent for this hypothesis.",
                    "low",
                    related_relations,
                    "Expert review of hypothesis status and decision rationale.",
                    sub_type="weak_review_support",
                )
            )

        return _dedupe_gaps(gaps)

    def _related_relations(self, hypothesis: ResearchHypothesis) -> list[GraphRelation]:
        relation_ids = {
            *hypothesis.supporting_relation_ids,
            *hypothesis.contradicting_relation_ids,
        }
        entity_ids = _hypothesis_entity_ids(hypothesis)
        related = [
            relation
            for relation in self.relations
            if relation.relation_id in relation_ids
            or relation.subject_entity_id in entity_ids
            or relation.object_entity_id in entity_ids
            or _metadata_str(relation.metadata, "target_entity_id") in entity_ids
            or _metadata_str(relation.metadata, "molecule_entity_id") in entity_ids
            or _metadata_str(relation.metadata, "generated_molecule_entity_id") in entity_ids
        ]
        return sorted(related, key=lambda relation: relation.relation_id)

    def _missing_direct_result(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if hypothesis.assay_result_ids and not hypothesis.generated_molecule_entity_ids:
            return False
        exact_entities = {
            *hypothesis.molecule_entity_ids,
            *hypothesis.generated_molecule_entity_ids,
        }
        if not exact_entities:
            return not hypothesis.assay_result_ids
        return not any(
            _is_exact_result_relation(relation, exact_entities) for relation in relations
        )

    def _direct_result_severity(self, hypothesis: ResearchHypothesis) -> str:
        if hypothesis.generated_molecule_entity_ids:
            return "high"
        if hypothesis.status == "accepted_for_planning":
            return "high"
        return "medium"

    def _missing_target_engagement(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        molecule_ids = {*hypothesis.molecule_entity_ids, *hypothesis.generated_molecule_entity_ids}
        if not molecule_ids or not hypothesis.target_entity_ids:
            return False
        return not any(
            relation.subject_entity_id in molecule_ids
            and relation.object_entity_id in set(hypothesis.target_entity_ids)
            and relation.predicate in {"targets", "modulates", "tested_in", "produced_result"}
            for relation in relations
        )

    def _missing_selectivity(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        molecule_ids = {*hypothesis.molecule_entity_ids, *hypothesis.generated_molecule_entity_ids}
        if not molecule_ids:
            return False
        return not any(_has_selectivity_context(relation) for relation in relations)

    def _missing_safety_context(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if not (hypothesis.molecule_entity_ids or hypothesis.generated_molecule_entity_ids):
            return False
        return not any(_has_safety_context(relation) for relation in relations)

    def _missing_developability_context(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if not (hypothesis.molecule_entity_ids or hypothesis.generated_molecule_entity_ids):
            return False
        return not any(_has_developability_context(relation) for relation in relations)

    def _missing_literature_support(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        return not hypothesis.literature_claim_ids and not any(
            relation.relation_type == "literature" for relation in relations
        )

    def _contradictory_relations(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> list[GraphRelation]:
        contradiction_ids = set(hypothesis.contradicting_relation_ids)
        return [
            relation
            for relation in relations
            if relation.relation_id in contradiction_ids or _is_contradictory(relation)
        ]

    def _contradiction_severity(self, hypothesis: ResearchHypothesis) -> str:
        if hypothesis.generated_molecule_entity_ids and not hypothesis.review_decision_ids:
            return "critical"
        if hypothesis.status == "accepted_for_planning" and not hypothesis.review_decision_ids:
            return "critical"
        return "high"

    def _stale_model_relations(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> list[GraphRelation]:
        model_ids = set(hypothesis.model_prediction_ids)
        return [
            relation
            for relation in relations
            if (
                relation.relation_type == "model_prediction"
                or relation.predicate == "predicted_by_model"
                or relation.subject_entity_id in model_ids
                or relation.object_entity_id in model_ids
                or _metadata_str(relation.metadata, "model_prediction_id") in model_ids
            )
            and _is_stale_model_relation(relation)
        ]

    def _generated_unreviewed(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if not hypothesis.generated_molecule_entity_ids or hypothesis.review_decision_ids:
            return False
        return not any(relation.relation_type == "review" for relation in relations)

    def _missing_structure_context(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if not (hypothesis.target_entity_ids or hypothesis.molecule_entity_ids):
            return False
        return not any(_has_structure_context(relation, self.entities) for relation in relations)

    def _weak_external_mapping_relations(
        self,
        relations: list[GraphRelation],
    ) -> list[GraphRelation]:
        return [
            relation
            for relation in relations
            if _weak_mapping_candidate(relation) and _mapping_confidence(relation) < 0.5
        ]

    def _weak_review_support(
        self,
        hypothesis: ResearchHypothesis,
        relations: list[GraphRelation],
    ) -> bool:
        if hypothesis.review_decision_ids:
            return False
        review_relations = [
            relation
            for relation in relations
            if relation.relation_type == "review" or relation.predicate == "reviewed_as"
        ]
        return not review_relations or any(
            relation.confidence < 0.5 for relation in review_relations
        )

    def _gap(
        self,
        hypothesis: ResearchHypothesis,
        gap_type: str,
        description: str,
        severity: str,
        relations: list[GraphRelation],
        suggested_high_level_resolution: str,
        *,
        sub_type: str | None = None,
    ) -> EvidenceGap:
        if detect_hypothesis_guardrail_violations(suggested_high_level_resolution):
            raise ValueError("evidence-gap suggested resolution must remain high-level")
        entity_ids = sorted(_hypothesis_entity_ids(hypothesis))
        relation_ids = sorted({relation.relation_id for relation in relations})
        metadata: dict[str, Any] = {
            "boundary": BOUNDARY_NOTE,
            "severity_rationale": SEVERITY_RATIONALE[severity],
            "not_failure": True,
            "absence_is_not_absence": True,
        }
        if sub_type:
            metadata["sub_type"] = sub_type
        return EvidenceGap(
            gap_id=_gap_id(hypothesis.hypothesis_id, gap_type, sub_type, relation_ids),
            hypothesis_id=hypothesis.hypothesis_id,
            gap_type=gap_type,  # type: ignore[arg-type]
            description=description,
            severity=severity,  # type: ignore[arg-type]
            suggested_high_level_resolution=suggested_high_level_resolution,
            linked_entity_ids=entity_ids,
            related_entity_ids=entity_ids,
            related_relation_ids=relation_ids,
            metadata=metadata,
        )


def analyze_hypothesis_evidence_gaps(
    hypothesis: ResearchHypothesis,
    *,
    entities: Iterable[GraphEntity] | Mapping[str, GraphEntity] | None = None,
    relations: Iterable[GraphRelation] | None = None,
) -> list[EvidenceGap]:
    return EvidenceGapAnalyzer(entities=entities, relations=relations).analyze(hypothesis)


def analyze_evidence_gaps_for_hypotheses(
    hypotheses: Iterable[ResearchHypothesis],
    *,
    graph: KnowledgeGraph | None = None,
    entities: Iterable[GraphEntity] | Mapping[str, GraphEntity] | None = None,
    relations: Iterable[GraphRelation] | None = None,
) -> dict[str, list[EvidenceGap]]:
    analyzer = EvidenceGapAnalyzer(
        entities=graph.entity_map() if graph else entities,
        relations=graph.relations if graph else relations,
    )
    return {hypothesis.hypothesis_id: analyzer.analyze(hypothesis) for hypothesis in hypotheses}


def analyze_evidence_gaps(graph: KnowledgeGraph) -> list[EvidenceGap]:
    entities = graph.entity_map()
    hypotheses = [
        _research_hypothesis_for_relation(graph.graph_id, relation)
        for relation in graph.relations
    ]
    analyzer = EvidenceGapAnalyzer(entities=entities, relations=graph.relations)
    return [
        gap
        for hypothesis in hypotheses
        for gap in analyzer.analyze(hypothesis)
        if gap.severity in {"critical", "high", "medium"}
    ]


def _research_hypothesis_for_relation(graph_id: str, relation: GraphRelation) -> ResearchHypothesis:
    entity_ids = [relation.subject_entity_id, relation.object_entity_id]
    return ResearchHypothesis(
        hypothesis_id=(
            f"hypothesis:{uuid5(NAMESPACE_URL, graph_id + ':' + relation.relation_id).hex[:16]}"
        ),
        hypothesis_type="evidence_gap",
        title="Hypothesis: graph relation evidence-gap review",
        statement="Hypothesis for review: graph-backed relation needs evidence-gap analysis.",
        supporting_relation_ids=[relation.relation_id],
        contradicting_relation_ids=(
            [relation.relation_id] if _is_contradictory(relation) else []
        ),
        source_artifact_ids=relation.source_artifact_ids,
        assay_result_ids=(
            [relation.object_entity_id]
            if relation.object_entity_id.startswith("assay_result:")
            else []
        ),
        model_prediction_ids=[
            value
            for value in [
                (
                    relation.subject_entity_id
                    if relation.subject_entity_id.startswith("model_prediction:")
                    else ""
                ),
                (
                    relation.object_entity_id
                    if relation.object_entity_id.startswith("model_prediction:")
                    else ""
                ),
                _metadata_str(relation.metadata, "model_prediction_id"),
            ]
            if value
        ],
        disease_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("disease:")
        ],
        target_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("target:")
        ],
        molecule_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("molecule:")
        ],
        generated_molecule_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("generated_molecule:")
        ],
        scaffold_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("scaffold:")
        ],
        mechanism_entity_ids=[
            entity_id for entity_id in entity_ids if entity_id.startswith("mechanism:")
        ],
    )


def _hypothesis_entity_ids(hypothesis: ResearchHypothesis) -> set[str]:
    return {
        *hypothesis.disease_entity_ids,
        *hypothesis.target_entity_ids,
        *hypothesis.molecule_entity_ids,
        *hypothesis.generated_molecule_entity_ids,
        *hypothesis.scaffold_entity_ids,
        *hypothesis.mechanism_entity_ids,
    }


def _is_exact_result_relation(relation: GraphRelation, exact_entity_ids: set[str]) -> bool:
    relation_entity_ids = {
        relation.subject_entity_id,
        relation.object_entity_id,
        _metadata_str(relation.metadata, "molecule_entity_id"),
        _metadata_str(relation.metadata, "generated_molecule_entity_id"),
    }
    touches_exact_entity = bool(exact_entity_ids.intersection(relation_entity_ids))
    touches_result = (
        relation.subject_entity_id.startswith("assay_result:")
        or relation.object_entity_id.startswith("assay_result:")
        or relation.predicate in {"produced_result", "tested_in", "validated_by", "contradicted_by"}
    )
    return relation.relation_type == "experimental" and touches_exact_entity and touches_result


def _has_selectivity_context(relation: GraphRelation) -> bool:
    return (
        relation.predicate in {"has_selectivity_context", "selectivity_profile"}
        or _metadata_bool(relation.metadata, "has_selectivity_context")
        or _metadata_str(relation.metadata, "context_type") == "selectivity"
        or bool(_metadata_str(relation.metadata, "selectivity_context"))
    )


def _has_safety_context(relation: GraphRelation) -> bool:
    return (
        "safety" in relation.predicate
        or "safety" in relation.object_entity_id
        or _metadata_str(relation.metadata, "risk_type") == "safety"
        or _metadata_bool(relation.metadata, "has_safety_context")
    )


def _has_developability_context(relation: GraphRelation) -> bool:
    return (
        relation.predicate in {"has_developability_risk", "blocked_by"}
        or "developability" in relation.object_entity_id
        or _metadata_str(relation.metadata, "risk_type") == "developability"
        or _metadata_bool(relation.metadata, "has_developability_context")
    )


def _has_structure_context(
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> bool:
    return (
        relation.predicate == "has_structure_assessment"
        or relation.relation_type == "structure_assessment"
        or entities.get(relation.subject_entity_id, None) is not None
        and entities[relation.subject_entity_id].entity_type in {"structure", "docking_pose"}
        or entities.get(relation.object_entity_id, None) is not None
        and entities[relation.object_entity_id].entity_type in {"structure", "docking_pose"}
    )


def _is_contradictory(relation: GraphRelation) -> bool:
    return (
        relation.direction == "contradictory"
        or relation.predicate in {"contradicts", "contradicted_by"}
        or _metadata_str(relation.metadata, "outcome_label").lower()
        in {"negative", "inactive", "failed", "no_support"}
    )


def _is_stale_model_relation(relation: GraphRelation) -> bool:
    if _metadata_bool(relation.metadata, "is_stale") or _metadata_bool(relation.metadata, "stale"):
        return True
    if relation.valid_until and relation.valid_until < datetime.now(UTC):
        return True
    stale_after_days = _metadata_int(relation.metadata, "stale_after_days")
    return stale_after_days is not None and (
        datetime.now(UTC) - relation.updated_at
    ).days > stale_after_days


def _weak_mapping_candidate(relation: GraphRelation) -> bool:
    return (
        relation.relation_type == "ontology_mapping"
        or relation.predicate in {"same_as", "similar_to"}
        or _metadata_bool(relation.metadata, "weak_external_mapping")
    )


def _mapping_confidence(relation: GraphRelation) -> float:
    for key in ["mapping_confidence", "external_mapping_confidence"]:
        value = _metadata_float(relation.metadata, key)
        if value is not None:
            return value
    return relation.confidence


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value) if value is not None else ""


def _metadata_bool(metadata: dict[str, Any], key: str) -> bool:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return False


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _metadata_float(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _gap_id(
    hypothesis_id: str,
    gap_type: str,
    sub_type: str | None,
    relation_ids: list[str],
) -> str:
    material = "|".join([hypothesis_id, gap_type, sub_type or "", *relation_ids])
    return f"gap:{uuid5(NAMESPACE_URL, material).hex[:16]}"


def _dedupe_gaps(gaps: list[EvidenceGap]) -> list[EvidenceGap]:
    deduped = {gap.gap_id: gap for gap in gaps}
    return sorted(
        deduped.values(),
        key=lambda gap: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}[gap.severity],
            gap.gap_type,
            gap.gap_id,
        ),
    )
