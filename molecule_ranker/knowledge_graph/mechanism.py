from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
    MechanismHypothesis,
)


def classify_mechanism_status(
    *,
    support_score: float,
    contradiction_score: float,
    generated: bool = False,
    stale: bool = False,
) -> str:
    if stale:
        return "stale"
    if contradiction_score >= 0.6:
        return "contradicted"
    if generated:
        return "generated_hypothesis"
    if support_score >= 0.7:
        return "supported"
    if support_score >= 0.3:
        return "weakly_supported"
    return "unresolved"


def extract_mechanism_hypotheses(
    graph: KnowledgeGraph,
    *,
    stale_after_days: int = 180,
) -> list[MechanismHypothesis]:
    entities = graph.entity_map()
    outgoing = _outgoing(graph.relations)
    incoming = _incoming(graph.relations)
    disease_targets = _disease_targets(graph.relations, entities)
    target_contexts = _target_contexts(graph.relations, entities)
    molecule_contexts = _molecule_contexts(graph.relations, entities)

    hypotheses: dict[str, MechanismAccumulator] = {}
    for relation in graph.relations:
        if not _is_molecule_target_relation(relation, entities):
            continue
        molecule = entities[relation.subject_entity_id]
        target = entities[relation.object_entity_id]
        diseases = disease_targets.get(target.entity_id) or [None]
        pathway_ids = sorted(
            set(target_contexts[target.entity_id].pathway_ids)
            | set(molecule_contexts[molecule.entity_id].pathway_ids)
        )
        mechanism_ids = sorted(
            set(target_contexts[target.entity_id].mechanism_ids)
            | set(molecule_contexts[molecule.entity_id].mechanism_ids)
        )
        if not mechanism_ids and pathway_ids:
            mechanism_ids = pathway_ids
        if not mechanism_ids:
            mechanism_ids = [f"implicit:{target.entity_id}:{molecule.entity_id}"]
        for disease_id in diseases:
            for mechanism_id in mechanism_ids:
                accumulator = _accumulator(
                    hypotheses,
                    disease_id=disease_id,
                    target_id=target.entity_id,
                    molecule=molecule,
                    mechanism_id=mechanism_id,
                    pathway_ids=pathway_ids,
                )
                accumulator.add_path_relation(relation)

    _attach_generated_lineage(hypotheses, graph.relations, entities, outgoing, incoming)
    for relation in graph.relations:
        _attach_relation_evidence(hypotheses, relation, entities)

    extracted = [
        accumulator.to_hypothesis(entities, graph.generated_at, stale_after_days=stale_after_days)
        for accumulator in hypotheses.values()
    ]
    return sorted(
        extracted,
        key=lambda item: (
            item.status != "supported",
            item.status != "contradicted",
            -item.support_score,
            -item.contradiction_score,
            item.mechanism_id,
        ),
    )


@dataclass
class MechanismContext:
    mechanism_ids: set[str] = field(default_factory=set)
    pathway_ids: set[str] = field(default_factory=set)
    relation_ids: set[str] = field(default_factory=set)


@dataclass
class MechanismAccumulator:
    disease_entity_id: str | None
    target_entity_ids: set[str]
    pathway_entity_ids: set[str]
    molecule_entity_ids: set[str]
    generated_molecule_entity_ids: set[str]
    mechanism_entity_ids: set[str]
    claim_entity_ids: set[str] = field(default_factory=set)
    evidence_relation_ids: set[str] = field(default_factory=set)
    contradiction_relation_ids: set[str] = field(default_factory=set)
    review_relation_ids: set[str] = field(default_factory=set)
    support_values: list[float] = field(default_factory=list)
    contradiction_values: list[float] = field(default_factory=list)
    novelty_values: list[float] = field(default_factory=list)
    warning_set: set[str] = field(default_factory=set)
    stale_relation_seen: bool = False

    @property
    def entity_ids(self) -> set[str]:
        ids = set(self.target_entity_ids)
        ids.update(self.pathway_entity_ids)
        ids.update(self.molecule_entity_ids)
        ids.update(self.generated_molecule_entity_ids)
        ids.update(self.mechanism_entity_ids)
        ids.update(self.claim_entity_ids)
        if self.disease_entity_id:
            ids.add(self.disease_entity_id)
        return ids

    def add_path_relation(self, relation: GraphRelation) -> None:
        if relation.relation_type != "inferred":
            self.evidence_relation_ids.add(relation.relation_id)
            self.support_values.append(relation.confidence)
        elif relation.is_stale:
            self.stale_relation_seen = True

    def add_support_relation(self, relation: GraphRelation) -> None:
        if relation.metadata.get("qc_status") == "failed":
            self.add_contradiction_relation(relation, weight=0.4)
            self.warning_set.add("Failed-QC assay results do not support mechanism hypotheses.")
            return
        if relation.relation_type == "inferred":
            if relation.is_stale:
                self.stale_relation_seen = True
            return
        self.evidence_relation_ids.add(relation.relation_id)
        self.support_values.append(relation.confidence)

    def add_contradiction_relation(self, relation: GraphRelation, *, weight: float = 1.0) -> None:
        self.contradiction_relation_ids.add(relation.relation_id)
        self.contradiction_values.append(_bounded(relation.confidence * weight))
        self.warning_set.add("Contradictions are surfaced separately and are not averaged away.")
        if relation.is_stale:
            self.stale_relation_seen = True

    def add_review_relation(self, relation: GraphRelation) -> None:
        self.review_relation_ids.add(relation.relation_id)
        self.warning_set.add("Review decisions modify priority but are not evidence.")

    def add_generated(self) -> None:
        self.novelty_values.append(0.65)
        self.warning_set.add("Generated mechanisms remain hypotheses until source-backed.")

    def to_hypothesis(
        self,
        entities: dict[str, GraphEntity],
        graph_generated_at: datetime,
        *,
        stale_after_days: int,
    ) -> MechanismHypothesis:
        support_score = _combine_scores(self.support_values)
        contradiction_score = _combine_scores(self.contradiction_values)
        novelty_score = max(self.novelty_values, default=0.0)
        generated = bool(self.generated_molecule_entity_ids)
        stale = self.stale_relation_seen or _relations_stale(
            self.evidence_relation_ids | self.contradiction_relation_ids,
            entities,
            graph_generated_at,
            stale_after_days=stale_after_days,
        )
        confidence = _confidence_from_consistency(
            support_score=support_score,
            contradiction_score=contradiction_score,
            evidence_count=len(self.evidence_relation_ids),
        )
        status = classify_mechanism_status(
            support_score=support_score,
            contradiction_score=contradiction_score,
            generated=generated,
            stale=stale,
        )
        self.warning_set.add("A mechanism hypothesis is not proof of causality.")
        summary = _summary(
            entities,
            disease_entity_id=self.disease_entity_id,
            target_entity_ids=self.target_entity_ids,
            molecule_entity_ids=self.molecule_entity_ids,
            generated_molecule_entity_ids=self.generated_molecule_entity_ids,
            mechanism_entity_ids=self.mechanism_entity_ids,
            status=status,
        )
        mechanism_key = "|".join(
            [
                self.disease_entity_id or "no-disease",
                ",".join(sorted(self.target_entity_ids)),
                ",".join(sorted(self.molecule_entity_ids | self.generated_molecule_entity_ids)),
                ",".join(sorted(self.mechanism_entity_ids | self.pathway_entity_ids)),
            ]
        )
        return MechanismHypothesis(
            mechanism_id="mechanism-hypothesis:" + uuid5(NAMESPACE_URL, mechanism_key).hex[:16],
            disease_entity_id=self.disease_entity_id,
            target_entity_ids=sorted(self.target_entity_ids),
            pathway_entity_ids=sorted(self.pathway_entity_ids),
            molecule_entity_ids=sorted(self.molecule_entity_ids),
            generated_molecule_entity_ids=sorted(self.generated_molecule_entity_ids),
            claim_entity_ids=sorted(self.claim_entity_ids),
            evidence_relation_ids=sorted(self.evidence_relation_ids),
            contradiction_relation_ids=sorted(self.contradiction_relation_ids),
            summary=summary,
            support_score=support_score,
            contradiction_score=contradiction_score,
            novelty_score=novelty_score,
            confidence=confidence,
            status=status,  # type: ignore[arg-type]
            warnings=sorted(self.warning_set),
            metadata={
                "mechanism_entity_ids": sorted(self.mechanism_entity_ids),
                "review_relation_ids": sorted(self.review_relation_ids),
                "support_relation_count": len(self.evidence_relation_ids),
                "contradiction_relation_count": len(self.contradiction_relation_ids),
            },
        )


_RELATION_INDEX: dict[str, GraphRelation] = {}


def _outgoing(relations: list[GraphRelation]) -> dict[str, list[GraphRelation]]:
    global _RELATION_INDEX
    _RELATION_INDEX = {relation.relation_id: relation for relation in relations}
    grouped: dict[str, list[GraphRelation]] = defaultdict(list)
    for relation in relations:
        grouped[relation.subject_entity_id].append(relation)
    return grouped


def _incoming(relations: list[GraphRelation]) -> dict[str, list[GraphRelation]]:
    grouped: dict[str, list[GraphRelation]] = defaultdict(list)
    for relation in relations:
        grouped[relation.object_entity_id].append(relation)
    return grouped


def _disease_targets(
    relations: list[GraphRelation], entities: dict[str, GraphEntity]
) -> dict[str, list[str]]:
    disease_targets: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        subject = entities.get(relation.subject_entity_id)
        target = entities.get(relation.object_entity_id)
        if (
            relation.predicate == "associated_with"
            and subject is not None
            and target is not None
            and subject.entity_type == "disease"
            and target.entity_type == "target"
        ):
            disease_targets[target.entity_id].append(subject.entity_id)
    return disease_targets


def _target_contexts(
    relations: list[GraphRelation], entities: dict[str, GraphEntity]
) -> dict[str, MechanismContext]:
    contexts: dict[str, MechanismContext] = defaultdict(MechanismContext)
    for relation in relations:
        source = entities.get(relation.subject_entity_id)
        target = entities.get(relation.object_entity_id)
        if source is None or target is None or source.entity_type != "target":
            continue
        _add_context_relation(contexts[source.entity_id], relation, target)
    return contexts


def _molecule_contexts(
    relations: list[GraphRelation], entities: dict[str, GraphEntity]
) -> dict[str, MechanismContext]:
    contexts: dict[str, MechanismContext] = defaultdict(MechanismContext)
    for relation in relations:
        source = entities.get(relation.subject_entity_id)
        target = entities.get(relation.object_entity_id)
        if (
            source is None
            or target is None
            or source.entity_type
            not in {
                "molecule",
                "generated_molecule",
            }
        ):
            continue
        _add_context_relation(contexts[source.entity_id], relation, target)
    return contexts


def _add_context_relation(
    context: MechanismContext, relation: GraphRelation, target: GraphEntity
) -> None:
    if relation.predicate not in {"has_mechanism", "associated_with", "modulates"}:
        return
    if target.entity_type == "mechanism":
        context.mechanism_ids.add(target.entity_id)
        context.relation_ids.add(relation.relation_id)
    if target.entity_type == "pathway":
        context.pathway_ids.add(target.entity_id)
        context.relation_ids.add(relation.relation_id)


def _is_molecule_target_relation(relation: GraphRelation, entities: dict[str, GraphEntity]) -> bool:
    subject = entities.get(relation.subject_entity_id)
    target = entities.get(relation.object_entity_id)
    return (
        subject is not None
        and target is not None
        and subject.entity_type in {"molecule", "generated_molecule"}
        and target.entity_type == "target"
        and relation.predicate in {"targets", "modulates", "hypothesizes"}
    )


def _accumulator(
    hypotheses: dict[str, MechanismAccumulator],
    *,
    disease_id: str | None,
    target_id: str,
    molecule: GraphEntity,
    mechanism_id: str,
    pathway_ids: list[str],
) -> MechanismAccumulator:
    key = "|".join([disease_id or "", target_id, molecule.entity_id, mechanism_id])
    if key not in hypotheses:
        generated = molecule.entity_type == "generated_molecule"
        hypotheses[key] = MechanismAccumulator(
            disease_entity_id=disease_id,
            target_entity_ids={target_id},
            pathway_entity_ids=set(pathway_ids),
            molecule_entity_ids=set() if generated else {molecule.entity_id},
            generated_molecule_entity_ids={molecule.entity_id} if generated else set(),
            mechanism_entity_ids=set() if mechanism_id.startswith("implicit:") else {mechanism_id},
        )
        if generated:
            hypotheses[key].add_generated()
    return hypotheses[key]


def _attach_generated_lineage(
    hypotheses: dict[str, MechanismAccumulator],
    relations: list[GraphRelation],
    entities: dict[str, GraphEntity],
    outgoing: dict[str, list[GraphRelation]],
    incoming: dict[str, list[GraphRelation]],
) -> None:
    for relation in relations:
        if relation.predicate != "generated_from":
            continue
        generated = entities.get(relation.subject_entity_id)
        seed = entities.get(relation.object_entity_id)
        if generated is None or seed is None or generated.entity_type != "generated_molecule":
            continue
        for accumulator in hypotheses.values():
            if generated.entity_id not in accumulator.generated_molecule_entity_ids:
                continue
            accumulator.evidence_relation_ids.add(relation.relation_id)
            accumulator.novelty_values.append(0.65)
            for seed_relation in outgoing.get(seed.entity_id, []):
                if seed_relation.predicate in {"targets", "modulates"}:
                    accumulator.add_support_relation(seed_relation)
            for generated_relation in outgoing.get(generated.entity_id, []):
                if generated_relation.predicate == "has_no_direct_evidence":
                    accumulator.novelty_values.append(0.75)
            for generated_relation in incoming.get(generated.entity_id, []):
                if generated_relation.predicate == "has_no_direct_evidence":
                    accumulator.novelty_values.append(0.75)


def _attach_relation_evidence(
    hypotheses: dict[str, MechanismAccumulator],
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> None:
    if relation.predicate == "reviewed_as":
        for accumulator in hypotheses.values():
            if _touches_accumulator(relation, accumulator):
                accumulator.add_review_relation(relation)
        return
    if relation.predicate in {"has_developability_risk", "blocked_by"}:
        for accumulator in hypotheses.values():
            if relation.subject_entity_id in (
                accumulator.molecule_entity_ids | accumulator.generated_molecule_entity_ids
            ):
                accumulator.add_contradiction_relation(relation, weight=0.75)
                accumulator.warning_set.add(
                    "Safety or developability risk blocks mechanism follow-up."
                )
        return
    if relation.predicate in {"contradicts", "contradicted_by", "failed_qc"}:
        for accumulator in hypotheses.values():
            if _touches_accumulator(relation, accumulator):
                accumulator.add_contradiction_relation(relation)
                _add_claim_if_present(accumulator, relation, entities)
        return
    if relation.predicate in {"supports", "supported_by", "validated_by"}:
        for accumulator in hypotheses.values():
            if _touches_accumulator(relation, accumulator):
                accumulator.add_support_relation(relation)
                _add_claim_if_present(accumulator, relation, entities)
        return
    if relation.predicate in {"associated_with", "has_mechanism"}:
        if _is_disease_target_association(relation, entities):
            return
        for accumulator in hypotheses.values():
            if _touches_accumulator(relation, accumulator):
                accumulator.add_support_relation(relation)
                _add_claim_if_present(accumulator, relation, entities)
        return
    if relation.predicate == "novel_vs_known":
        for accumulator in hypotheses.values():
            if relation.subject_entity_id in accumulator.generated_molecule_entity_ids:
                accumulator.novelty_values.append(0.1)
                accumulator.warning_set.add(
                    "Generated molecule may rediscover known chemistry or prior series."
                )


def _touches_accumulator(relation: GraphRelation, accumulator: MechanismAccumulator) -> bool:
    entity_ids = accumulator.entity_ids
    return relation.subject_entity_id in entity_ids or relation.object_entity_id in entity_ids


def _is_disease_target_association(
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> bool:
    subject = entities.get(relation.subject_entity_id)
    target = entities.get(relation.object_entity_id)
    return (
        relation.predicate == "associated_with"
        and subject is not None
        and target is not None
        and subject.entity_type == "disease"
        and target.entity_type == "target"
    )


def _add_claim_if_present(
    accumulator: MechanismAccumulator,
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> None:
    for entity_id in (relation.subject_entity_id, relation.object_entity_id):
        entity = entities.get(entity_id)
        if entity is not None and entity.entity_type == "literature_claim":
            accumulator.claim_entity_ids.add(entity_id)


def _combine_scores(values: list[float]) -> float:
    if not values:
        return 0.0
    remaining = 1.0
    for value in values:
        remaining *= 1.0 - _bounded(value)
    return round(_bounded(1.0 - remaining), 4)


def _confidence_from_consistency(
    *, support_score: float, contradiction_score: float, evidence_count: int
) -> float:
    provenance_quality = min(1.0, evidence_count / 3)
    consistency = max(0.0, 1.0 - contradiction_score)
    return round(
        _bounded(0.2 + 0.5 * support_score + 0.2 * provenance_quality + 0.1 * consistency), 4
    )


def _relations_stale(
    relation_ids: set[str],
    entities: dict[str, GraphEntity],
    graph_generated_at: datetime,
    *,
    stale_after_days: int,
) -> bool:
    del entities
    for relation_id in relation_ids:
        relation = _RELATION_INDEX.get(relation_id)
        if relation is None:
            continue
        if relation.is_stale:
            return True
        if relation.created_at.tzinfo is not None:
            age_days = (graph_generated_at - relation.created_at).days
            if age_days > stale_after_days:
                return True
    return False


def _summary(
    entities: dict[str, GraphEntity],
    *,
    disease_entity_id: str | None,
    target_entity_ids: set[str],
    molecule_entity_ids: set[str],
    generated_molecule_entity_ids: set[str],
    mechanism_entity_ids: set[str],
    status: str,
) -> str:
    disease = entities[disease_entity_id].name if disease_entity_id else "unspecified disease"
    targets = ", ".join(_names(entities, target_entity_ids)) or "unspecified target"
    molecules = ", ".join(_names(entities, molecule_entity_ids | generated_molecule_entity_ids))
    mechanisms = ", ".join(_names(entities, mechanism_entity_ids)) or "target mechanism"
    return (
        f"{status.replace('_', ' ').title()} mechanism hypothesis for {disease}: "
        f"{targets} linked to {molecules or 'candidate molecule'} via {mechanisms}."
    )


def _names(entities: dict[str, GraphEntity], entity_ids: set[str]) -> list[str]:
    return [entities[entity_id].name for entity_id in sorted(entity_ids) if entity_id in entities]


def _bounded(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "MechanismHypothesis",
    "classify_mechanism_status",
    "extract_mechanism_hypotheses",
]
