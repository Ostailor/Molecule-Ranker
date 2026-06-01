from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.hypotheses.engine import HypothesisGenerationEngine
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, MechanismHypothesis
from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore

__all__ = [
    "DeterministicHypothesisGenerator",
    "HypothesisGenerationEngine",
    "generate_hypothesis_candidates",
]

STRONG_SUPPORT_THRESHOLD = 0.75
GENERATED_READINESS_THRESHOLD = 0.75
UNDEREXPLORED_RESULT_LIMIT = 1


@dataclass(frozen=True)
class HypothesisGenerationInputs:
    mechanism_hypotheses: list[MechanismHypothesis | dict[str, Any]] = field(default_factory=list)
    contradiction_reports: list[Any] = field(default_factory=list)
    staleness_reports: list[Any] = field(default_factory=list)
    portfolio_selections: list[Any] = field(default_factory=list)
    assay_result_summaries: list[Any] = field(default_factory=list)
    model_predictions: list[Any] = field(default_factory=list)
    structure_assessments: list[Any] = field(default_factory=list)
    review_decisions: list[Any] = field(default_factory=list)


class DeterministicHypothesisGenerator:
    """Pattern-based V1.6 hypothesis generation over persisted graph records.

    The generator only references existing graph entities, graph relations, and
    imported artifact identifiers. It returns planning hypotheses and never
    creates EvidenceItem or AssayResult records.
    """

    def __init__(
        self,
        graph_store: KnowledgeGraphStore,
        *,
        mechanism_hypotheses: Iterable[MechanismHypothesis | dict[str, Any]] | None = None,
        contradiction_reports: Iterable[Any] | None = None,
        staleness_reports: Iterable[Any] | None = None,
        portfolio_selections: Iterable[Any] | None = None,
        assay_result_summaries: Iterable[Any] | None = None,
        model_predictions: Iterable[Any] | None = None,
        structure_assessments: Iterable[Any] | None = None,
        review_decisions: Iterable[Any] | None = None,
    ) -> None:
        self.graph_store = graph_store
        self.inputs = HypothesisGenerationInputs(
            mechanism_hypotheses=list(mechanism_hypotheses or []),
            contradiction_reports=list(contradiction_reports or []),
            staleness_reports=list(staleness_reports or []),
            portfolio_selections=list(portfolio_selections or []),
            assay_result_summaries=list(assay_result_summaries or []),
            model_predictions=list(model_predictions or []),
            structure_assessments=list(structure_assessments or []),
            review_decisions=list(review_decisions or []),
        )

    def generate(self) -> list[ResearchHypothesis]:
        graph = _GraphIndex.from_store(self.graph_store)
        hypotheses = [
            *self._supported_mechanism_expansion(graph),
            *self._generated_analog_follow_up(graph),
            *self._contradiction_resolution(graph),
            *self._scaffold_risk(graph),
            *self._cross_program_success_signal(graph),
            *self._stale_decision(graph),
            *self._underexplored_target(graph),
        ]
        return _dedupe_and_sort(hypotheses)

    def _supported_mechanism_expansion(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        for disease_target in graph.strong_disease_target_relations():
            disease = graph.entity(disease_target.subject_entity_id)
            target = graph.entity(disease_target.object_entity_id)
            if not disease or not target:
                continue
            molecule_target_relations = [
                relation
                for relation in graph.relations_for_object(target.entity_id)
                if graph.entity_type(relation.subject_entity_id)
                in {"molecule", "generated_molecule"}
                and relation.relation_type != "experimental"
                and _is_supportive(relation)
                and not graph.direct_result_relations(
                    relation.subject_entity_id,
                    target_entity_id=target.entity_id,
                )
            ]
            for molecule_target in sorted(molecule_target_relations, key=_relation_key):
                molecule = graph.entity(molecule_target.subject_entity_id)
                if not molecule:
                    continue
                mechanism_relations = graph.mechanism_relations_for_target(target.entity_id)
                supporting = [
                    disease_target,
                    molecule_target,
                    *mechanism_relations,
                ]
                hypotheses.append(
                    self._research_hypothesis(
                        "supported_mechanism_expansion",
                        [relation.relation_id for relation in supporting],
                        title=(
                            f"Hypothesis: {molecule.name} relationship to {target.name} "
                            f"in {disease.name} needs direct validation context"
                        ),
                        statement=(
                            "Hypothesis for review: graph-backed disease-target and "
                            "molecule-target context suggests a testable relationship, "
                            "but no direct imported experimental result is linked."
                        ),
                        hypothesis_type="mechanism" if mechanism_relations else "molecule_target",
                        disease_entity_ids=[disease.entity_id],
                        target_entity_ids=[target.entity_id],
                        molecule_entity_ids=[molecule.entity_id],
                        generated_molecule_entity_ids=(
                            [molecule.entity_id]
                            if molecule.entity_type == "generated_molecule"
                            else []
                        ),
                        mechanism_entity_ids=[
                            relation.object_entity_id for relation in mechanism_relations
                        ],
                        source_relations=supporting,
                        support_score=_mean_confidence(supporting),
                        novelty_score=0.45,
                        testability_score=0.8,
                        uncertainty_score=0.55,
                        priority_score=0.65,
                        confidence=min(_mean_confidence(supporting), 0.8),
                        metadata={
                            "evidence_gap_type": "missing_direct_experimental_result",
                            "graph_path": [
                                disease.entity_id,
                                target.entity_id,
                                molecule.entity_id,
                            ],
                        },
                    )
                )
        return hypotheses

    def _generated_analog_follow_up(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        for lineage in sorted(graph.find_relations(predicate="generated_from"), key=_relation_key):
            generated = graph.entity(lineage.subject_entity_id)
            seed = graph.entity(lineage.object_entity_id)
            if not generated or not seed or generated.entity_type != "generated_molecule":
                continue
            score = max(
                _float_metadata(generated.metadata, "readiness_score"),
                _float_metadata(generated.metadata, "design_score"),
                _float_metadata(lineage.metadata, "readiness_score"),
                _float_metadata(lineage.metadata, "design_score"),
            )
            if score < GENERATED_READINESS_THRESHOLD or graph.direct_result_relations(
                generated.entity_id
            ):
                continue
            hypotheses.append(
                self._research_hypothesis(
                    "generated_analog_follow_up",
                    [lineage.relation_id],
                    title=f"Hypothesis: generated analog {generated.name} merits evidence review",
                    statement=(
                        "Hypothesis for review: the generated analog has graph-backed "
                        "lineage and readiness context, but no direct imported evidence "
                        "is linked to the generated molecule."
                    ),
                    hypothesis_type="generated_molecule",
                    molecule_entity_ids=[seed.entity_id],
                    generated_molecule_entity_ids=[generated.entity_id],
                    source_relations=[lineage],
                    support_score=min(score, lineage.confidence),
                    novelty_score=0.7,
                    testability_score=0.75,
                    uncertainty_score=0.6,
                    priority_score=0.6,
                    confidence=min(score, lineage.confidence),
                    metadata={
                        "evidence_gap_type": "missing_direct_experimental_result",
                        "readiness_score": score,
                    },
                )
            )
        return hypotheses

    def _contradiction_resolution(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        report_pairs = self._contradiction_report_pairs(graph)
        pairs = [*report_pairs, *graph.support_contradiction_pairs()]
        seen: set[tuple[str, str]] = set()
        for support, contradiction in sorted(
            pairs,
            key=lambda item: (_relation_key(item[0]), _relation_key(item[1])),
        ):
            key = (support.relation_id, contradiction.relation_id)
            if key in seen:
                continue
            seen.add(key)
            molecule_ids = sorted(
                {
                    entity_id
                    for entity_id in [
                        support.subject_entity_id,
                        support.object_entity_id,
                        contradiction.subject_entity_id,
                        contradiction.object_entity_id,
                    ]
                    if graph.entity_type(entity_id) in {"molecule", "generated_molecule"}
                }
            )
            target_ids = sorted(
                {
                    entity_id
                    for entity_id in [
                        _metadata_str(support.metadata, "target_entity_id"),
                        _metadata_str(contradiction.metadata, "target_entity_id"),
                        support.subject_entity_id,
                        support.object_entity_id,
                    ]
                    if entity_id and graph.entity_type(entity_id) == "target"
                }
            )
            assay_ids = sorted(
                {
                    entity_id
                    for entity_id in [
                        support.subject_entity_id,
                        support.object_entity_id,
                        contradiction.subject_entity_id,
                        contradiction.object_entity_id,
                    ]
                    if graph.entity_type(entity_id) == "assay_result"
                }
            )
            hypotheses.append(
                self._research_hypothesis(
                    "contradiction_resolution",
                    [support.relation_id],
                    contradicting_relation_ids=[contradiction.relation_id],
                    title="Hypothesis: imported evidence conflict requires scoped review",
                    statement=(
                        "Hypothesis for review: graph-backed supportive context and a "
                        "contradictory imported result point to an assay-result "
                        "contradiction that should be resolved without assuming which "
                        "record is correct."
                    ),
                    hypothesis_type="assay_contradiction",
                    target_entity_ids=target_ids,
                    molecule_entity_ids=molecule_ids,
                    generated_molecule_entity_ids=[
                        entity_id
                        for entity_id in molecule_ids
                        if graph.entity_type(entity_id) == "generated_molecule"
                    ],
                    source_relations=[support, contradiction],
                    assay_result_ids=assay_ids,
                    model_prediction_ids=_model_prediction_ids([support, contradiction]),
                    support_score=support.confidence,
                    contradiction_score=contradiction.confidence,
                    novelty_score=0.3,
                    testability_score=0.8,
                    uncertainty_score=0.85,
                    priority_score=0.75,
                    confidence=min(max(support.confidence, contradiction.confidence), 0.85),
                    metadata={"evidence_gap_type": "contradictory_results"},
                )
            )
        return hypotheses

    def _scaffold_risk(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        for scaffold_id, scaffold_links in sorted(graph.scaffold_links().items()):
            molecule_ids = sorted({relation.subject_entity_id for relation in scaffold_links})
            risk_relations = [
                relation
                for molecule_id in molecule_ids
                for relation in graph.relations_for_subject(molecule_id)
                if _is_developability_or_safety_risk(relation)
            ]
            risk_subjects = sorted({relation.subject_entity_id for relation in risk_relations})
            if len(risk_subjects) < 2:
                continue
            scaffold = graph.entity(scaffold_id)
            source_relations = [*scaffold_links, *risk_relations]
            hypotheses.append(
                self._research_hypothesis(
                    "scaffold_risk",
                    [relation.relation_id for relation in source_relations],
                    title=(
                        "Hypothesis: repeated scaffold risk pattern for "
                        f"{scaffold.name if scaffold else scaffold_id}"
                    ),
                    statement=(
                        "Hypothesis for review: multiple candidates sharing a scaffold "
                        "also share graph-backed developability or safety blockers; this "
                        "is a risk hypothesis, not evidence of unsafe behavior."
                    ),
                    hypothesis_type=(
                        "safety_risk"
                        if any(_is_safety_risk(relation) for relation in risk_relations)
                        else "developability_risk"
                    ),
                    molecule_entity_ids=molecule_ids,
                    scaffold_entity_ids=[scaffold_id],
                    source_relations=source_relations,
                    support_score=_mean_confidence(source_relations),
                    contradiction_score=0.0,
                    novelty_score=0.35,
                    testability_score=0.65,
                    uncertainty_score=0.65,
                    priority_score=0.7,
                    confidence=min(_mean_confidence(source_relations), 0.8),
                    metadata={
                        "repeated_blocker_count": len(risk_subjects),
                        "evidence_gap_type": "missing_developability_data",
                    },
                )
            )
        return hypotheses

    def _cross_program_success_signal(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        grouped: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in graph.find_relations(relation_type="experimental"):
            if not _is_positive_qc_passed(relation):
                continue
            target_id = _metadata_str(relation.metadata, "target_entity_id")
            if not target_id:
                targets = graph.targets_for_molecule(relation.subject_entity_id)
                target_id = targets[0] if targets else ""
            if target_id:
                grouped[target_id].append(relation)
        for target_id, positive_results in sorted(grouped.items()):
            program_ids = sorted(
                {
                    program_id
                    for relation in positive_results
                    if (program_id := _metadata_str(relation.metadata, "program_id"))
                }
            )
            if len(program_ids) < 2:
                continue
            target = graph.entity(target_id)
            mechanism_relations = graph.mechanism_relations_for_target(target_id)
            molecule_ids = sorted({relation.subject_entity_id for relation in positive_results})
            source_relations = [
                *positive_results,
                *graph.target_links_for_molecules(molecule_ids, target_id),
                *mechanism_relations,
            ]
            hypotheses.append(
                self._research_hypothesis(
                    "cross_program_success_signal",
                    [relation.relation_id for relation in source_relations],
                    title=(
                        "Hypothesis: cross-program signal for "
                        f"{target.name if target else target_id}"
                    ),
                    statement=(
                        "Hypothesis for review: QC-passed positive results from another "
                        "program share graph context with this target or mechanism. The "
                        "signal supports prioritization review, not a claim of activity "
                        "outside the imported assay context."
                    ),
                    hypothesis_type="mechanism" if mechanism_relations else "scaffold_series",
                    target_entity_ids=[target_id],
                    molecule_entity_ids=molecule_ids,
                    mechanism_entity_ids=[
                        relation.object_entity_id for relation in mechanism_relations
                    ],
                    source_relations=source_relations,
                    assay_result_ids=[
                        relation.object_entity_id
                        for relation in positive_results
                        if graph.entity_type(relation.object_entity_id) == "assay_result"
                    ],
                    support_score=_mean_confidence(positive_results),
                    novelty_score=0.4,
                    testability_score=0.7,
                    uncertainty_score=0.45,
                    priority_score=0.72,
                    confidence=min(_mean_confidence(positive_results), 0.8),
                    metadata={
                        "cross_program_program_ids": program_ids,
                        "portfolio_selection_count": len(self.inputs.portfolio_selections),
                    },
                )
            )
        return hypotheses

    def _stale_decision(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        stale_pairs = self._staleness_report_pairs(graph) or graph.stale_decision_pairs()
        for decision, contradiction in sorted(
            stale_pairs,
            key=lambda item: (_relation_key(item[0]), _relation_key(item[1])),
        ):
            if decision.created_at >= contradiction.created_at:
                continue
            hypothesis_type = (
                "portfolio_decision"
                if decision.predicate == "selected_in_portfolio"
                or _metadata_str(decision.metadata, "decision_kind") in {"portfolio", "stage_gate"}
                or graph.entity_type(decision.subject_entity_id) == "portfolio"
                else "evidence_gap"
            )
            hypotheses.append(
                self._research_hypothesis(
                    "stale_decision",
                    [decision.relation_id],
                    contradicting_relation_ids=[contradiction.relation_id],
                    title="Hypothesis: prior decision may be stale after new contradiction",
                    statement=(
                        "Hypothesis for review: a review, portfolio, or stage-gate "
                        "decision predates newer contradictory graph-backed evidence. "
                        "The decision should be reassessed without treating the "
                        "hypothesis itself as evidence."
                    ),
                    hypothesis_type=hypothesis_type,
                    molecule_entity_ids=sorted(
                        {
                            entity_id
                            for entity_id in [
                                decision.subject_entity_id,
                                decision.object_entity_id,
                                contradiction.subject_entity_id,
                                contradiction.object_entity_id,
                            ]
                            if graph.entity_type(entity_id)
                            in {"molecule", "generated_molecule"}
                        }
                    ),
                    source_relations=[decision, contradiction],
                    assay_result_ids=[
                        entity_id
                        for entity_id in [
                            contradiction.subject_entity_id,
                            contradiction.object_entity_id,
                        ]
                        if graph.entity_type(entity_id) == "assay_result"
                    ],
                    review_decision_ids=[
                        review_id
                        for review_id in [_metadata_str(decision.metadata, "review_decision_id")]
                        if review_id
                    ],
                    support_score=decision.confidence,
                    contradiction_score=contradiction.confidence,
                    novelty_score=0.25,
                    testability_score=0.6,
                    uncertainty_score=0.75,
                    priority_score=0.7,
                    confidence=min(max(decision.confidence, contradiction.confidence), 0.85),
                    status="stale",
                    metadata={"evidence_gap_type": "contradictory_results"},
                )
            )
        return hypotheses

    def _underexplored_target(self, graph: _GraphIndex) -> list[ResearchHypothesis]:
        hypotheses: list[ResearchHypothesis] = []
        for disease_target in graph.strong_disease_target_relations():
            molecule_links = graph.molecule_links_for_target(disease_target.object_entity_id)
            result_relations = [
                relation
                for relation in graph.find_relations(relation_type="experimental")
                if _metadata_str(relation.metadata, "target_entity_id")
                == disease_target.object_entity_id
            ]
            if len(molecule_links) > UNDEREXPLORED_RESULT_LIMIT or result_relations:
                continue
            disease = graph.entity(disease_target.subject_entity_id)
            target = graph.entity(disease_target.object_entity_id)
            hypotheses.append(
                self._research_hypothesis(
                    "underexplored_target",
                    [disease_target.relation_id],
                    title=(
                        "Hypothesis: underexplored target "
                        f"{target.name if target else disease_target.object_entity_id}"
                    ),
                    statement=(
                        "Hypothesis for review: strong disease-target evidence exists "
                        "in the graph, but linked molecule and result coverage is sparse."
                    ),
                    hypothesis_type="disease_target",
                    disease_entity_ids=[disease_target.subject_entity_id],
                    target_entity_ids=[disease_target.object_entity_id],
                    source_relations=[disease_target],
                    support_score=disease_target.confidence,
                    novelty_score=0.65,
                    testability_score=0.55,
                    uncertainty_score=0.7,
                    priority_score=0.62,
                    confidence=min(disease_target.confidence, 0.8),
                    metadata={
                        "evidence_gap_type": "missing_molecule_target_evidence",
                        "disease_name": disease.name if disease else None,
                    },
                )
            )
        return hypotheses

    def _contradiction_report_pairs(
        self,
        graph: _GraphIndex,
    ) -> list[tuple[GraphRelation, GraphRelation]]:
        pairs: list[tuple[GraphRelation, GraphRelation]] = []
        for report in self.inputs.contradiction_reports:
            support_ids = _report_list(report, "supporting_relation_ids", "support_relation_ids")
            contradiction_ids = _report_list(
                report,
                "contradicting_relation_ids",
                "contradiction_relation_ids",
                "negative_relation_ids",
            )
            for support_id in support_ids:
                for contradiction_id in contradiction_ids:
                    support = graph.relation(support_id)
                    contradiction = graph.relation(contradiction_id)
                    if support and contradiction:
                        pairs.append((support, contradiction))
        return pairs

    def _staleness_report_pairs(
        self,
        graph: _GraphIndex,
    ) -> list[tuple[GraphRelation, GraphRelation]]:
        pairs: list[tuple[GraphRelation, GraphRelation]] = []
        for report in self.inputs.staleness_reports:
            stale_ids = _report_list(report, "stale_relation_ids", "decision_relation_ids")
            contradiction_ids = _report_list(
                report,
                "new_contradictory_relation_ids",
                "contradicting_relation_ids",
                "contradiction_relation_ids",
            )
            for stale_id in stale_ids:
                for contradiction_id in contradiction_ids:
                    stale = graph.relation(stale_id)
                    contradiction = graph.relation(contradiction_id)
                    if stale and contradiction:
                        pairs.append((stale, contradiction))
        return pairs

    def _research_hypothesis(
        self,
        pattern: str,
        supporting_relation_ids: list[str],
        *,
        title: str,
        statement: str,
        hypothesis_type: str,
        source_relations: list[GraphRelation],
        contradicting_relation_ids: list[str] | None = None,
        disease_entity_ids: list[str] | None = None,
        target_entity_ids: list[str] | None = None,
        molecule_entity_ids: list[str] | None = None,
        generated_molecule_entity_ids: list[str] | None = None,
        scaffold_entity_ids: list[str] | None = None,
        mechanism_entity_ids: list[str] | None = None,
        assay_result_ids: list[str] | None = None,
        model_prediction_ids: list[str] | None = None,
        review_decision_ids: list[str] | None = None,
        support_score: float = 0.0,
        contradiction_score: float = 0.0,
        novelty_score: float = 0.0,
        testability_score: float = 0.0,
        uncertainty_score: float = 0.0,
        priority_score: float = 0.0,
        confidence: float = 0.0,
        status: str = "proposed",
        metadata: dict[str, Any] | None = None,
    ) -> ResearchHypothesis:
        source_artifact_ids = _source_artifact_ids(source_relations)
        graph_path_material = "|".join(
            sorted(supporting_relation_ids + (contradicting_relation_ids or []))
        )
        graph_path_ids = [
            f"graph-path:{uuid5(NAMESPACE_URL, graph_path_material).hex[:16]}"
        ]
        base_metadata = {
            "pattern": pattern,
            "generator": "deterministic_v1_6",
            "inferred_hypothesis": True,
            "not_evidence": True,
            "creates_evidence": False,
            "creates_assay_result": False,
        }
        base_metadata.update(metadata or {})
        return ResearchHypothesis(
            hypothesis_id=_hypothesis_id(
                pattern,
                supporting_relation_ids,
                contradicting_relation_ids or [],
            ),
            hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
            title=title,
            statement=statement,
            disease_entity_ids=sorted(set(disease_entity_ids or [])),
            target_entity_ids=sorted(set(target_entity_ids or [])),
            molecule_entity_ids=sorted(set(molecule_entity_ids or [])),
            generated_molecule_entity_ids=sorted(set(generated_molecule_entity_ids or [])),
            scaffold_entity_ids=sorted(set(scaffold_entity_ids or [])),
            mechanism_entity_ids=sorted(set(mechanism_entity_ids or [])),
            supporting_relation_ids=sorted(set(supporting_relation_ids)),
            contradicting_relation_ids=sorted(set(contradicting_relation_ids or [])),
            source_artifact_ids=source_artifact_ids,
            assay_result_ids=sorted(set(assay_result_ids or [])),
            model_prediction_ids=sorted(set(model_prediction_ids or [])),
            review_decision_ids=sorted(set(review_decision_ids or [])),
            graph_path_ids=graph_path_ids,
            support_score=_bounded(support_score),
            contradiction_score=_bounded(contradiction_score),
            novelty_score=_bounded(novelty_score),
            testability_score=_bounded(testability_score),
            uncertainty_score=_bounded(uncertainty_score),
            priority_score=_bounded(priority_score),
            confidence=_bounded(confidence),
            status=status,  # type: ignore[arg-type]
            warnings=[
                "Generated deterministically from graph-backed records.",
                "Hypothesis only; do not treat as evidence or a procedural plan.",
            ],
            metadata=base_metadata,
        )


@dataclass
class _GraphIndex:
    entities: dict[str, GraphEntity]
    relations: dict[str, GraphRelation]
    relations_by_subject: dict[str, list[GraphRelation]]
    relations_by_object: dict[str, list[GraphRelation]]

    @classmethod
    def from_store(cls, store: KnowledgeGraphStore) -> _GraphIndex:
        entities = {entity.entity_id: entity for entity in store.find_entities()}
        relations = {relation.relation_id: relation for relation in store.find_relations()}
        relations_by_subject: dict[str, list[GraphRelation]] = defaultdict(list)
        relations_by_object: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in relations.values():
            relations_by_subject[relation.subject_entity_id].append(relation)
            relations_by_object[relation.object_entity_id].append(relation)
        return cls(
            entities=entities,
            relations=relations,
            relations_by_subject=relations_by_subject,
            relations_by_object=relations_by_object,
        )

    def entity(self, entity_id: str) -> GraphEntity | None:
        return self.entities.get(entity_id)

    def relation(self, relation_id: str) -> GraphRelation | None:
        return self.relations.get(relation_id)

    def entity_type(self, entity_id: str) -> str | None:
        entity = self.entities.get(entity_id)
        return str(entity.entity_type) if entity else None

    def find_relations(
        self,
        *,
        predicate: str | None = None,
        relation_type: str | None = None,
    ) -> list[GraphRelation]:
        return [
            relation
            for relation in self.relations.values()
            if (predicate is None or relation.predicate == predicate)
            and (relation_type is None or relation.relation_type == relation_type)
        ]

    def relations_for_subject(self, entity_id: str) -> list[GraphRelation]:
        return self.relations_by_subject.get(entity_id, [])

    def relations_for_object(self, entity_id: str) -> list[GraphRelation]:
        return self.relations_by_object.get(entity_id, [])

    def strong_disease_target_relations(self) -> list[GraphRelation]:
        return sorted(
            [
                relation
                for relation in self.relations.values()
                if self.entity_type(relation.subject_entity_id) == "disease"
                and self.entity_type(relation.object_entity_id) == "target"
                and relation.predicate in {"associated_with", "supports"}
                and relation.confidence >= STRONG_SUPPORT_THRESHOLD
                and _is_supportive(relation)
            ],
            key=_relation_key,
        )

    def mechanism_relations_for_target(self, target_id: str) -> list[GraphRelation]:
        return sorted(
            [
                relation
                for relation in self.relations_for_subject(target_id)
                if relation.predicate == "has_mechanism"
                and self.entity_type(relation.object_entity_id) == "mechanism"
                and _is_supportive(relation)
            ],
            key=_relation_key,
        )

    def direct_result_relations(
        self,
        entity_id: str,
        *,
        target_entity_id: str | None = None,
    ) -> list[GraphRelation]:
        direct = [
            relation
            for relation in self.relations.values()
            if relation.relation_type == "experimental"
            and (
                relation.subject_entity_id == entity_id
                or relation.object_entity_id == entity_id
                or _metadata_str(relation.metadata, "molecule_entity_id") == entity_id
                or _metadata_str(relation.metadata, "generated_molecule_entity_id") == entity_id
            )
            and (
                relation.predicate
                in {
                    "produced_result",
                    "tested_in",
                    "validated_by",
                    "contradicted_by",
                }
                or self.entity_type(relation.object_entity_id) == "assay_result"
                or self.entity_type(relation.subject_entity_id) == "assay_result"
            )
        ]
        if target_entity_id:
            direct = [
                relation
                for relation in direct
                if not _metadata_str(relation.metadata, "target_entity_id")
                or _metadata_str(relation.metadata, "target_entity_id") == target_entity_id
            ]
        return sorted(direct, key=_relation_key)

    def support_contradiction_pairs(self) -> list[tuple[GraphRelation, GraphRelation]]:
        pairs: list[tuple[GraphRelation, GraphRelation]] = []
        supports = [
            relation
            for relation in self.relations.values()
            if (
                _is_supportive_context(relation)
                and relation.relation_type != "experimental"
            )
            or _is_positive_result_context(relation)
        ]
        contradictions = [
            relation
            for relation in self.relations.values()
            if _is_contradictory_result_or_model(relation)
        ]
        for support in supports:
            support_molecules = self._related_molecules(support)
            support_targets = self._related_targets(support)
            for contradiction in contradictions:
                if support.relation_id == contradiction.relation_id:
                    continue
                if support_molecules and support_molecules.intersection(
                    self._related_molecules(contradiction)
                ):
                    contradiction_targets = self._related_targets(contradiction)
                    has_shared_target = support_targets.intersection(contradiction_targets)
                    if not support_targets or not contradiction_targets or has_shared_target:
                        pairs.append((support, contradiction))
        return pairs

    def scaffold_links(self) -> dict[str, list[GraphRelation]]:
        grouped: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in self.find_relations(predicate="has_scaffold"):
            if self.entity_type(relation.object_entity_id) == "scaffold":
                grouped[relation.object_entity_id].append(relation)
        return grouped

    def targets_for_molecule(self, molecule_id: str) -> list[str]:
        return sorted(
            {
                relation.object_entity_id
                for relation in self.relations_for_subject(molecule_id)
                if relation.predicate in {"targets", "modulates", "associated_with"}
                and self.entity_type(relation.object_entity_id) == "target"
            }
        )

    def target_links_for_molecules(
        self,
        molecule_ids: Iterable[str],
        target_id: str,
    ) -> list[GraphRelation]:
        molecule_set = set(molecule_ids)
        return sorted(
            [
                relation
                for relation in self.relations.values()
                if relation.subject_entity_id in molecule_set
                and relation.object_entity_id == target_id
                and relation.predicate in {"targets", "modulates", "associated_with"}
            ],
            key=_relation_key,
        )

    def molecule_links_for_target(self, target_id: str) -> list[GraphRelation]:
        return sorted(
            [
                relation
                for relation in self.relations_for_object(target_id)
                if self.entity_type(relation.subject_entity_id)
                in {"molecule", "generated_molecule"}
                and relation.predicate in {"targets", "modulates", "associated_with"}
            ],
            key=_relation_key,
        )

    def stale_decision_pairs(self) -> list[tuple[GraphRelation, GraphRelation]]:
        decision_relations = [
            relation
            for relation in self.relations.values()
            if relation.predicate in {"reviewed_as", "selected_in_portfolio"}
            or relation.relation_type == "review"
        ]
        contradictions = [
            relation
            for relation in self.relations.values()
            if _is_contradictory_result_or_model(relation)
        ]
        pairs: list[tuple[GraphRelation, GraphRelation]] = []
        for decision in decision_relations:
            decision_entities = {decision.subject_entity_id, decision.object_entity_id}
            for contradiction in contradictions:
                if decision.created_at >= contradiction.created_at:
                    continue
                contradiction_entities = {
                    contradiction.subject_entity_id,
                    contradiction.object_entity_id,
                }
                if decision_entities.intersection(contradiction_entities):
                    pairs.append((decision, contradiction))
        return pairs

    def _related_molecules(self, relation: GraphRelation) -> set[str]:
        return {
            entity_id
            for entity_id in [
                relation.subject_entity_id,
                relation.object_entity_id,
                _metadata_str(relation.metadata, "molecule_entity_id"),
                _metadata_str(relation.metadata, "generated_molecule_entity_id"),
            ]
            if entity_id and self.entity_type(entity_id) in {"molecule", "generated_molecule"}
        }

    def _related_targets(self, relation: GraphRelation) -> set[str]:
        return {
            entity_id
            for entity_id in [
                relation.subject_entity_id,
                relation.object_entity_id,
                _metadata_str(relation.metadata, "target_entity_id"),
            ]
            if entity_id and self.entity_type(entity_id) == "target"
        }


def generate_hypothesis_candidates(
    graph_store: KnowledgeGraphStore,
    *,
    mechanism_hypotheses: Iterable[MechanismHypothesis | dict[str, Any]] | None = None,
    contradiction_reports: Iterable[Any] | None = None,
    staleness_reports: Iterable[Any] | None = None,
    portfolio_selections: Iterable[Any] | None = None,
    assay_result_summaries: Iterable[Any] | None = None,
    model_predictions: Iterable[Any] | None = None,
    structure_assessments: Iterable[Any] | None = None,
    review_decisions: Iterable[Any] | None = None,
) -> list[ResearchHypothesis]:
    return DeterministicHypothesisGenerator(
        graph_store,
        mechanism_hypotheses=mechanism_hypotheses,
        contradiction_reports=contradiction_reports,
        staleness_reports=staleness_reports,
        portfolio_selections=portfolio_selections,
        assay_result_summaries=assay_result_summaries,
        model_predictions=model_predictions,
        structure_assessments=structure_assessments,
        review_decisions=review_decisions,
    ).generate()


def _source_artifact_ids(relations: Iterable[GraphRelation]) -> list[str]:
    return sorted(
        {
            artifact_id
            for relation in relations
            for artifact_id in relation.source_artifact_ids
        }
    )


def _hypothesis_id(
    pattern: str,
    supporting_relation_ids: list[str],
    contradicting_relation_ids: list[str],
) -> str:
    material = "|".join(
        [pattern, *sorted(supporting_relation_ids), *sorted(contradicting_relation_ids)]
    )
    return f"hypothesis:{uuid5(NAMESPACE_URL, material).hex[:16]}"


def _is_supportive(relation: GraphRelation) -> bool:
    return relation.direction in {None, "supportive", "neutral"} and relation.confidence >= 0.5


def _is_supportive_context(relation: GraphRelation) -> bool:
    if relation.direction == "contradictory":
        return False
    if relation.predicate in {
        "supports",
        "targets",
        "modulates",
        "associated_with",
        "has_structure_assessment",
        "predicted_by_model",
    }:
        return relation.confidence >= 0.5
    return relation.relation_type in {
        "literature",
        "computational",
        "model_prediction",
        "evidence_backed",
    } and relation.confidence >= 0.5


def _is_positive_result_context(relation: GraphRelation) -> bool:
    outcome = _metadata_str(relation.metadata, "outcome_label").lower()
    return (
        relation.relation_type == "experimental"
        and relation.direction == "supportive"
        and relation.predicate in {"produced_result", "validated_by", "tested_in"}
        and outcome in {"positive", "supported", "qc_positive"}
    )


def _is_contradictory_result_or_model(relation: GraphRelation) -> bool:
    outcome = _metadata_str(relation.metadata, "outcome_label").lower()
    return (
        relation.direction == "contradictory"
        or relation.predicate in {"contradicts", "contradicted_by"}
        or outcome in {"negative", "inactive", "failed", "no_support"}
    ) and relation.relation_type in {
        "experimental",
        "model_prediction",
        "computational",
        "inferred",
    }


def _is_developability_or_safety_risk(relation: GraphRelation) -> bool:
    return _is_developability_risk(relation) or _is_safety_risk(relation)


def _is_developability_risk(relation: GraphRelation) -> bool:
    return (
        relation.predicate in {"has_developability_risk", "blocked_by"}
        or _metadata_str(relation.metadata, "risk_type") == "developability"
        or "developability" in relation.object_entity_id
    )


def _is_safety_risk(relation: GraphRelation) -> bool:
    return (
        _metadata_str(relation.metadata, "risk_type") == "safety"
        or relation.predicate == "has_safety_risk"
        or "safety" in relation.object_entity_id
    )


def _is_positive_qc_passed(relation: GraphRelation) -> bool:
    outcome = _metadata_str(relation.metadata, "outcome_label").lower()
    qc_status = _metadata_str(relation.metadata, "qc_status").lower()
    return (
        relation.relation_type == "experimental"
        and relation.direction == "supportive"
        and outcome in {"positive", "qc_positive", "supported"}
        and qc_status in {"passed", "pass", "qc_passed"}
    )


def _model_prediction_ids(relations: Iterable[GraphRelation]) -> list[str]:
    return sorted(
        {
            model_id
            for relation in relations
            if (model_id := _metadata_str(relation.metadata, "model_prediction_id"))
        }
    )


def _report_list(report: Any, *keys: str) -> list[str]:
    for key in keys:
        value = _report_value(report, key)
        if value is None:
            continue
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]
    return []


def _report_value(report: Any, key: str) -> Any:
    if isinstance(report, dict):
        return report.get(key)
    return getattr(report, key, None)


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value) if value is not None else ""


def _float_metadata(metadata: dict[str, Any], key: str) -> float:
    value = metadata.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _mean_confidence(relations: Iterable[GraphRelation]) -> float:
    values = [relation.confidence for relation in relations]
    if not values:
        return 0.0
    return _bounded(sum(values) / len(values))


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _relation_key(relation: GraphRelation) -> tuple[str, str, str]:
    return (relation.subject_entity_id, relation.predicate, relation.relation_id)


def _dedupe_and_sort(hypotheses: list[ResearchHypothesis]) -> list[ResearchHypothesis]:
    deduped = {hypothesis.hypothesis_id: hypothesis for hypothesis in hypotheses}
    return sorted(
        deduped.values(),
        key=lambda hypothesis: (
            str(hypothesis.metadata.get("pattern", "")),
            hypothesis.hypothesis_id,
        ),
    )
