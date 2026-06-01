from __future__ import annotations

from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph

from .schemas import (
    EvidenceGap,
    FalsificationCriterion,
    Hypothesis,
    HypothesisSet,
    HypothesisType,
    ResearchQuestionType,
    ValidationPlan,
)
from .validation import validate_hypothesis_set


class HypothesisGenerationEngine:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self.entities = graph.entity_map()

    def generate(self) -> HypothesisSet:
        hypotheses: list[Hypothesis] = []
        hypotheses.extend(self._mechanistic_hypotheses())
        hypotheses.extend(self._molecule_target_hypotheses())
        hypotheses.extend(self._generated_follow_up_hypotheses())
        hypotheses.extend(self._developability_risk_hypotheses())
        hypotheses.extend(self._contradiction_hypotheses())
        hypotheses.extend(self._scaffold_series_hypotheses())
        hypotheses.extend(self._evidence_gap_hypotheses())
        hypotheses.extend(self._active_learning_hypotheses())
        hypotheses.extend(self._portfolio_hypotheses())
        hypotheses.extend(self._high_level_validation_questions())
        hypotheses = _dedupe_hypotheses(hypotheses)
        hypotheses = sorted(
            hypotheses,
            key=lambda hypothesis: (-hypothesis.rank_score, hypothesis.hypothesis_id),
        )
        validate_hypothesis_set(hypotheses, self.graph)
        return HypothesisSet(graph_id=self.graph.graph_id, hypotheses=hypotheses)

    def _mechanistic_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "mechanistic",
                "Review graph-backed mechanism hypothesis",
                f"Graph records link {_name(relation.subject_entity_id, self.entities)} with "
                f"{_name(relation.object_entity_id, self.entities)} as a reviewable mechanism "
                "hypothesis.",
                [relation],
                _question_type("mechanistic"),
            )
            for relation in self.graph.relations
            if relation.predicate == "has_mechanism"
        ]

    def _molecule_target_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "molecule_target",
                "Review molecule-target hypothesis",
                f"Graph records connect {_name(relation.subject_entity_id, self.entities)} and "
                f"{_name(relation.object_entity_id, self.entities)} for source-scoped review.",
                [relation],
                _question_type("molecule_target"),
            )
            for relation in self.graph.relations
            if relation.predicate in {"targets", "modulates"}
        ]

    def _generated_follow_up_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "generated_molecule_follow_up",
                "Review generated-molecule follow-up hypothesis",
                f"{_name(relation.subject_entity_id, self.entities)} has graph lineage to "
                f"{_name(relation.object_entity_id, self.entities)} and needs source-scoped "
                "follow-up review.",
                [relation],
                _question_type("generated_molecule_follow_up"),
            )
            for relation in self.graph.relations
            if relation.predicate == "generated_from"
        ]

    def _developability_risk_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "developability_risk",
                "Review developability-risk hypothesis",
                f"Graph records associate {_name(relation.subject_entity_id, self.entities)} "
                f"with risk signal {_name(relation.object_entity_id, self.entities)}.",
                [relation],
                _question_type("developability_risk"),
            )
            for relation in self.graph.relations
            if relation.predicate in {"has_developability_risk", "blocked_by"}
        ]

    def _contradiction_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "assay_result_contradiction",
                "Review assay-result contradiction hypothesis",
                f"Graph records flag a contradiction involving "
                f"{_name(relation.subject_entity_id, self.entities)} and "
                f"{_name(relation.object_entity_id, self.entities)}.",
                [relation],
                _question_type("assay_result_contradiction"),
                contradiction_ids=[relation.relation_id],
            )
            for relation in self.graph.relations
            if relation.predicate in {"contradicts", "contradicted_by"}
        ]

    def _scaffold_series_hypotheses(self) -> list[Hypothesis]:
        grouped: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in self.graph.relations:
            if relation.predicate in {"has_scaffold", "has_series"}:
                grouped[relation.object_entity_id].append(relation)
        hypotheses = []
        for scaffold_id, relations in grouped.items():
            if len({relation.subject_entity_id for relation in relations}) < 2:
                continue
            hypotheses.append(
                self._hypothesis(
                    "cross_program_scaffold_series",
                    "Review cross-program scaffold or series hypothesis",
                    f"{_name(scaffold_id, self.entities)} recurs across graph-linked molecules "
                    "or generated molecules.",
                    relations,
                    _question_type("cross_program_scaffold_series"),
                )
            )
        return hypotheses

    def _evidence_gap_hypotheses(self) -> list[Hypothesis]:
        hypotheses = []
        for entity in self.entities.values():
            if entity.entity_type not in {"molecule", "generated_molecule"}:
                continue
            relations = self._relations_for(entity.entity_id)
            if relations and any(_has_gap(relation) for relation in relations):
                hypotheses.append(
                    self._hypothesis(
                        "evidence_gap",
                        "Review evidence-gap hypothesis",
                        f"{entity.name} has graph-linked uncertainty or missing direct support "
                        "that should be resolved before stronger claims are made.",
                        relations,
                        _question_type("evidence_gap"),
                    )
                )
        return hypotheses

    def _active_learning_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "active_learning",
                "Review active-learning hypothesis",
                f"{_name(relation.subject_entity_id, self.entities)} has graph-linked model "
                "uncertainty or learning value for prioritization review.",
                [relation],
                _question_type("active_learning"),
            )
            for relation in self.graph.relations
            if relation.predicate == "predicted_by_model" or "uncertainty" in relation.metadata
        ]

    def _portfolio_hypotheses(self) -> list[Hypothesis]:
        return [
            self._hypothesis(
                "portfolio_decision",
                "Review portfolio decision hypothesis",
                f"{_name(relation.subject_entity_id, self.entities)} is linked to portfolio "
                f"{_name(relation.object_entity_id, self.entities)} for decision review.",
                [relation],
                _question_type("portfolio_decision"),
            )
            for relation in self.graph.relations
            if relation.predicate == "selected_in_portfolio"
        ]

    def _high_level_validation_questions(self) -> list[Hypothesis]:
        if not self.graph.relations:
            return []
        relations = self.graph.relations[:5]
        return [
            self._hypothesis(
                "high_level_validation_question",
                "Review high-level validation question",
                "Graph-backed hypotheses should be reviewed for uncertainty, contradictions, "
                "evidence gaps, and decision relevance before downstream use.",
                relations,
                _question_type("high_level_validation_question"),
            )
        ]

    def _hypothesis(
        self,
        hypothesis_type: HypothesisType,
        title: str,
        summary: str,
        relations: list[GraphRelation],
        question_type: ResearchQuestionType,
        *,
        contradiction_ids: list[str] | None = None,
    ) -> Hypothesis:
        entity_ids = _entity_ids(relations)
        relation_ids = [relation.relation_id for relation in relations]
        artifact_ids = sorted(
            {artifact for relation in relations for artifact in relation.source_artifact_ids}
        )
        provenance_ids = [provenance.provenance_id for provenance in self.graph.provenance]
        hypothesis_id = _stable_id(self.graph.graph_id, hypothesis_type, *relation_ids)
        rank_score = _rank_score(relations, hypothesis_type)
        criteria = [
            FalsificationCriterion(
                statement=(
                    "Treat the hypothesis as unsupported if source-backed graph records or "
                    "expert review no longer support the referenced relationship."
                ),
                graph_record_ids=[*entity_ids, *relation_ids],
            )
        ]
        gaps = _evidence_gaps(relations, self.entities)
        plan = ValidationPlan(
            research_question_id=f"rq-{hypothesis_id}",
            objective="Define a reviewable research question using only cited graph records.",
            question_type=question_type,
            recommended_evidence=[
                "Compare referenced graph entities, relations, provenance, contradictions, "
                "and imported evidence artifacts at the claim level."
            ],
        )
        return Hypothesis(
            hypothesis_id=hypothesis_id,
            hypothesis_type=hypothesis_type,
            title=title,
            summary=summary,
            uncertainty=(
                "This is a graph-backed hypothesis for expert review, not evidence or a "
                "biomedical claim."
            ),
            entity_ids=entity_ids,
            relation_ids=relation_ids,
            provenance_ids=provenance_ids,
            artifact_ids=artifact_ids,
            evidence_gaps=gaps,
            contradiction_relation_ids=contradiction_ids or [
                relation.relation_id
                for relation in relations
                if relation.predicate in {"contradicts", "contradicted_by"}
            ],
            falsification_criteria=criteria,
            validation_plan=plan,
            rank_score=rank_score,
            metadata={"graph_id": self.graph.graph_id},
        )

    def _relations_for(self, entity_id: str) -> list[GraphRelation]:
        return [
            relation
            for relation in self.graph.relations
            if entity_id in {relation.subject_entity_id, relation.object_entity_id}
        ]


def analyze_evidence_gaps(graph: KnowledgeGraph) -> list[EvidenceGap]:
    entities = graph.entity_map()
    return [
        gap
        for relation in graph.relations
        if _has_gap(relation)
        for gap in _evidence_gaps([relation], entities)
    ]


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(hypotheses, key=lambda item: (-item.rank_score, item.hypothesis_id))


def _entity_ids(relations: list[GraphRelation]) -> list[str]:
    entity_ids = {
        entity_id
        for relation in relations
        for entity_id in (relation.subject_entity_id, relation.object_entity_id)
    }
    return sorted(entity_ids)


def _evidence_gaps(
    relations: list[GraphRelation],
    entities: dict[str, GraphEntity],
) -> list[EvidenceGap]:
    gaps = []
    entity_ids = _entity_ids(relations)
    relation_ids = [relation.relation_id for relation in relations]
    if any(relation.relation_type == "inferred" for relation in relations):
        gaps.append(
            EvidenceGap(
                description="Inferred graph relationship requires source-backed confirmation.",
                severity="high",
                related_entity_ids=entity_ids,
                related_relation_ids=relation_ids,
            )
        )
    if not any(relation.relation_type == "experimental" for relation in relations):
        gaps.append(
            EvidenceGap(
                description="No direct imported experimental result is cited for this hypothesis.",
                severity="medium",
                related_entity_ids=entity_ids,
                related_relation_ids=relation_ids,
            )
        )
    if any(relation.predicate in {"contradicts", "contradicted_by"} for relation in relations):
        gaps.append(
            EvidenceGap(
                description="Contradictory graph-linked records require expert adjudication.",
                severity="high",
                related_entity_ids=entity_ids,
                related_relation_ids=relation_ids,
            )
        )
    has_generated_molecule = any(
        entities.get(entity_id) and entities[entity_id].entity_type == "generated_molecule"
        for entity_id in entity_ids
    )
    if has_generated_molecule:
        gaps.append(
            EvidenceGap(
                description=(
                    "Generated molecules remain computational hypotheses without exact "
                    "linked evidence."
                ),
                severity="high",
                related_entity_ids=entity_ids,
                related_relation_ids=relation_ids,
            )
        )
    if not gaps:
        gaps.append(
            EvidenceGap(
                description=(
                    "Scope remains limited to cited graph records and requires review "
                    "before use."
                ),
                severity="low",
                related_entity_ids=entity_ids,
                related_relation_ids=relation_ids,
            )
        )
    return gaps


def _has_gap(relation: GraphRelation) -> bool:
    return (
        relation.relation_type == "inferred"
        or relation.predicate in {"contradicts", "contradicted_by", "generated_from"}
        or relation.predicate in {"predicted_by_model", "selected_in_portfolio"}
    )


def _rank_score(relations: list[GraphRelation], hypothesis_type: HypothesisType) -> float:
    base = sum(relation.confidence for relation in relations) / len(relations)
    type_bonus = {
        "assay_result_contradiction": 0.16,
        "developability_risk": 0.12,
        "evidence_gap": 0.1,
        "portfolio_decision": 0.08,
        "active_learning": 0.06,
    }.get(hypothesis_type, 0.04)
    contradiction_bonus = 0.08 if any(
        relation.predicate in {"contradicts", "contradicted_by"} for relation in relations
    ) else 0.0
    return max(0.0, min(base + type_bonus + contradiction_bonus, 1.0))


def _question_type(hypothesis_type: HypothesisType) -> ResearchQuestionType:
    return {
        "assay_result_contradiction": "contradiction_review",
        "developability_risk": "developability_review",
        "portfolio_decision": "portfolio_review",
        "active_learning": "active_learning_review",
        "high_level_validation_question": "validation_question",
    }.get(hypothesis_type, "evidence_review")  # type: ignore[return-value]


def _stable_id(graph_id: str, hypothesis_type: str, *parts: str) -> str:
    return "hyp:" + uuid5(NAMESPACE_URL, "|".join([graph_id, hypothesis_type, *parts])).hex[:16]


def _name(entity_id: str, entities: dict[str, GraphEntity]) -> str:
    entity = entities.get(entity_id)
    return entity.name if entity else entity_id


def _dedupe_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    seen: set[str] = set()
    deduped = []
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id in seen:
            continue
        seen.add(hypothesis.hypothesis_id)
        deduped.append(hypothesis)
    return deduped
