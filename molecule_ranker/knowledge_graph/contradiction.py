from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.knowledge_graph.reasoning import analyze_cross_program_knowledge
from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphFinding,
    GraphRelation,
    KnowledgeGraph,
)

HIGH_SCORE_THRESHOLD = 0.75
CRITICAL_RISK_TERMS = {"critical", "severe", "high"}


@dataclass(frozen=True)
class ContradictionReport:
    graph_id: str
    contradiction_relations: list[GraphRelation]
    findings: list[GraphFinding] = field(default_factory=list)
    advisory: bool = True
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    warnings: list[str] = field(
        default_factory=lambda: [
            "Contradiction detection is advisory and does not delete older evidence.",
            "Graph paths do not prove causality, efficacy, safety, binding, or activity.",
        ]
    )


@dataclass(frozen=True)
class StalenessReport:
    graph_id: str
    stale_relations: list[GraphRelation]
    findings: list[GraphFinding] = field(default_factory=list)
    advisory: bool = True
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    warnings: list[str] = field(
        default_factory=lambda: [
            "Staleness detection is advisory and preserves temporal provenance.",
            "Older evidence is retained for audit and review.",
        ]
    )


def detect_contradictions(graph: KnowledgeGraph) -> list[GraphFinding]:
    """Backward-compatible finding view used by existing graph reasoning code."""

    findings = list(analyze_cross_program_knowledge(graph).contradictions)
    report = build_contradiction_report(graph)
    findings.extend(report.findings)
    return findings


def build_contradiction_report(graph: KnowledgeGraph) -> ContradictionReport:
    relations = detect_contradiction_relations(graph)
    return ContradictionReport(
        graph_id=graph.graph_id,
        contradiction_relations=relations,
        findings=[_finding("contradiction", relation) for relation in relations],
    )


def build_staleness_report(graph: KnowledgeGraph) -> StalenessReport:
    relations = detect_staleness_relations(graph)
    return StalenessReport(
        graph_id=graph.graph_id,
        stale_relations=relations,
        findings=[_finding("staleness", relation) for relation in relations],
    )


def detect_contradiction_relations(graph: KnowledgeGraph) -> list[GraphRelation]:
    entities = graph.entity_map()
    contexts = _relation_contexts(graph.relations, entities)
    emitted: dict[str, GraphRelation] = {}

    _detect_assay_disagreements(contexts, emitted)
    _detect_literature_disagreements(graph.relations, entities, emitted)
    _detect_prediction_or_structure_vs_negative_experiment(contexts, emitted)
    _detect_review_acceptance_vs_safety_failure(graph.relations, contexts, emitted)
    _detect_generated_promising_vs_critical_risk(entities, contexts, emitted)

    return sorted(emitted.values(), key=lambda relation: relation.relation_id)


def detect_staleness_relations(graph: KnowledgeGraph) -> list[GraphRelation]:
    entities = graph.entity_map()
    contexts = _relation_contexts(graph.relations, entities)
    emitted: dict[str, GraphRelation] = {}

    _detect_old_literature_superseded_by_newer_contradiction(graph.relations, emitted)
    _detect_models_before_new_assays(contexts, emitted)
    _detect_portfolio_before_safety_update(graph.relations, contexts, emitted)
    _detect_review_before_new_experiment(graph.relations, contexts, emitted)
    _detect_mapping_version_staleness(graph, emitted)

    return sorted(emitted.values(), key=lambda relation: relation.relation_id)


@dataclass
class RelationContext:
    relation: GraphRelation
    candidate_id: str | None
    target_symbol: str | None
    endpoint_id: str | None
    outcome: str | None
    qc_status: str | None
    score: float | None
    severity: str | None

    @property
    def candidate_target_endpoint_key(self) -> tuple[str, str, str] | None:
        if not self.candidate_id:
            return None
        return (
            self.candidate_id,
            self.target_symbol or "unknown_target",
            self.endpoint_id or "unknown_endpoint",
        )


def _relation_contexts(
    relations: list[GraphRelation],
    entities: dict[str, GraphEntity],
) -> list[RelationContext]:
    contexts: list[RelationContext] = []
    for relation in relations:
        contexts.append(
            RelationContext(
                relation=relation,
                candidate_id=_candidate_id(relation, entities),
                target_symbol=_target_symbol(relation, entities),
                endpoint_id=_endpoint_id(relation),
                outcome=_outcome(relation),
                qc_status=_qc_status(relation),
                score=_score(relation),
                severity=_severity(relation),
            )
        )
    return contexts


def _detect_assay_disagreements(
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    positives: dict[tuple[str, str, str], list[RelationContext]] = {}
    negatives: dict[tuple[str, str, str], list[RelationContext]] = {}
    for context in contexts:
        if context.relation.relation_type != "experimental" or context.qc_status == "failed":
            continue
        key = context.candidate_target_endpoint_key
        if key is None:
            continue
        if _is_positive_assay(context):
            positives.setdefault(key, []).append(context)
        if _is_negative_assay(context):
            negatives.setdefault(key, []).append(context)
    for key in sorted(set(positives) & set(negatives)):
        for positive in positives[key]:
            for negative in negatives[key]:
                _emit_advisory_relation(
                    emitted,
                    predicate="contradicts",
                    subject=positive.relation.subject_entity_id,
                    object_id=negative.relation.object_entity_id,
                    source_relations=[positive.relation, negative.relation],
                    reason="assay_positive_vs_negative",
                    confidence=max(positive.relation.confidence, negative.relation.confidence),
                    direction="contradictory",
                    metadata={
                        "candidate_id": key[0],
                        "target_symbol": key[1],
                        "endpoint_id": key[2],
                    },
                )


def _detect_literature_disagreements(
    relations: list[GraphRelation],
    entities: dict[str, GraphEntity],
    emitted: dict[str, GraphRelation],
) -> None:
    supportive = [
        relation
        for relation in relations
        if relation.predicate in {"supports", "supported_by"}
        and _touches_literature_claim(relation, entities)
    ]
    contradictory = [
        relation
        for relation in relations
        if relation.predicate in {"contradicts", "contradicted_by"}
        and _touches_literature_claim(relation, entities)
    ]
    for support in supportive:
        for contradiction in contradictory:
            if not _same_claim_target(support, contradiction):
                continue
            _emit_advisory_relation(
                emitted,
                predicate="contradicts",
                subject=_claim_entity_id(support, entities) or support.subject_entity_id,
                object_id=_claim_entity_id(contradiction, entities)
                or contradiction.subject_entity_id,
                source_relations=[support, contradiction],
                reason="supportive_literature_vs_contradictory_literature",
                confidence=max(support.confidence, contradiction.confidence),
                direction="contradictory",
            )


def _detect_prediction_or_structure_vs_negative_experiment(
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    negatives = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.relation_type == "experimental"
        and context.qc_status != "failed"
        and _is_negative_assay(context)
    ]
    high_predictions = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.predicate == "predicted_by_model"
        and (context.score or context.relation.confidence) >= HIGH_SCORE_THRESHOLD
    ]
    high_structures = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.predicate
        in {"computational_pose_for", "computational_prioritization_for"}
        and (context.score or context.relation.confidence) >= HIGH_SCORE_THRESHOLD
    ]
    for prediction in high_predictions:
        for negative in negatives:
            if not _same_candidate_target(prediction, negative):
                continue
            _emit_advisory_relation(
                emitted,
                predicate="contradicts",
                subject=prediction.relation.subject_entity_id,
                object_id=negative.relation.object_entity_id,
                source_relations=[prediction.relation, negative.relation],
                reason="model_prediction_high_vs_experimental_negative",
                confidence=max(prediction.relation.confidence, negative.relation.confidence),
                direction="contradictory",
            )
    for structure in high_structures:
        for negative in negatives:
            if not _same_candidate_target(structure, negative):
                continue
            _emit_advisory_relation(
                emitted,
                predicate="contradicts",
                subject=structure.relation.subject_entity_id,
                object_id=negative.relation.object_entity_id,
                source_relations=[structure.relation, negative.relation],
                reason="structure_score_high_vs_experimental_negative",
                confidence=max(structure.relation.confidence, negative.relation.confidence),
                direction="contradictory",
            )


def _detect_review_acceptance_vs_safety_failure(
    relations: list[GraphRelation],
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    accepted_reviews = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.predicate == "reviewed_as"
        and _is_accept_decision(context.relation)
    ]
    safety_failures = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.predicate in {"has_developability_risk", "blocked_by"}
        and _is_critical_or_safety(context)
    ]
    for review in accepted_reviews:
        for failure in safety_failures:
            if review.candidate_id != failure.candidate_id:
                continue
            if review.relation.created_at > failure.relation.created_at:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="contradicts",
                subject=review.relation.subject_entity_id,
                object_id=failure.relation.object_entity_id,
                source_relations=[review.relation, failure.relation],
                reason="review_accepted_vs_later_safety_failure",
                confidence=failure.relation.confidence,
                direction="contradictory",
            )
    del relations


def _detect_generated_promising_vs_critical_risk(
    entities: dict[str, GraphEntity],
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    promising = [
        context
        for context in contexts
        if context.candidate_id
        and _is_generated_candidate(context.candidate_id, entities)
        and context.relation.predicate
        in {
            "predicted_by_model",
            "hypothesizes",
            "computational_pose_for",
            "computational_prioritization_for",
        }
        and (context.score or context.relation.confidence) >= HIGH_SCORE_THRESHOLD
    ]
    critical_risks = [
        context
        for context in contexts
        if context.candidate_id
        and _is_generated_candidate(context.candidate_id, entities)
        and context.relation.predicate in {"has_developability_risk", "blocked_by"}
        and _is_critical_or_safety(context)
    ]
    for predicted in promising:
        for risk in critical_risks:
            if predicted.candidate_id != risk.candidate_id:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="contradicts",
                subject=predicted.relation.subject_entity_id,
                object_id=risk.relation.object_entity_id,
                source_relations=[predicted.relation, risk.relation],
                reason="generated_promising_vs_critical_developability_risk",
                confidence=max(predicted.relation.confidence, risk.relation.confidence),
                direction="contradictory",
            )


def _detect_old_literature_superseded_by_newer_contradiction(
    relations: list[GraphRelation],
    emitted: dict[str, GraphRelation],
) -> None:
    old_support = [
        relation
        for relation in relations
        if relation.relation_type == "literature"
        and relation.predicate in {"supports", "supported_by"}
    ]
    newer_contradictions = [
        relation
        for relation in relations
        if relation.predicate in {"contradicts", "contradicted_by"}
    ]
    for support in old_support:
        for contradiction in newer_contradictions:
            if not _same_claim_target(support, contradiction):
                continue
            if support.created_at >= contradiction.created_at:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="stale_due_to",
                subject=support.subject_entity_id,
                object_id=contradiction.object_entity_id,
                source_relations=[support, contradiction],
                reason="old_literature_superseded_by_newer_contradiction",
                confidence=contradiction.confidence,
                direction="unknown",
            )


def _detect_models_before_new_assays(
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    models = [
        context
        for context in contexts
        if context.candidate_id and context.relation.predicate == "predicted_by_model"
    ]
    assays = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.relation_type == "experimental"
        and context.qc_status != "failed"
    ]
    for model in models:
        trained_at = _trained_at(model.relation) or model.relation.created_at
        for assay in assays:
            if not _same_candidate_target(model, assay):
                continue
            if trained_at >= assay.relation.created_at:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="stale_due_to",
                subject=model.relation.subject_entity_id,
                object_id=assay.relation.object_entity_id,
                source_relations=[model.relation, assay.relation],
                reason="model_trained_before_newer_assay_result",
                confidence=assay.relation.confidence,
                direction="unknown",
                metadata={"trained_at": trained_at.isoformat()},
            )


def _detect_portfolio_before_safety_update(
    relations: list[GraphRelation],
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    portfolio = [
        relation for relation in relations if relation.predicate == "selected_in_portfolio"
    ]
    risks = [
        context
        for context in contexts
        if context.candidate_id
        and context.relation.predicate in {"has_developability_risk", "blocked_by"}
    ]
    for selection in portfolio:
        candidate_id = selection.subject_entity_id
        for risk in risks:
            if candidate_id != risk.candidate_id:
                continue
            if selection.created_at >= risk.relation.created_at:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="stale_due_to",
                subject=selection.object_entity_id,
                object_id=risk.relation.object_entity_id,
                source_relations=[selection, risk.relation],
                reason="portfolio_selected_before_safety_update",
                confidence=risk.relation.confidence,
                direction="unknown",
            )


def _detect_review_before_new_experiment(
    relations: list[GraphRelation],
    contexts: list[RelationContext],
    emitted: dict[str, GraphRelation],
) -> None:
    reviews = [relation for relation in relations if relation.predicate == "reviewed_as"]
    assays = [
        context
        for context in contexts
        if context.candidate_id and context.relation.relation_type == "experimental"
    ]
    for review in reviews:
        candidate_id = review.object_entity_id
        for assay in assays:
            if candidate_id != assay.candidate_id:
                continue
            if review.created_at >= assay.relation.created_at:
                continue
            _emit_advisory_relation(
                emitted,
                predicate="stale_due_to",
                subject=review.subject_entity_id,
                object_id=assay.relation.object_entity_id,
                source_relations=[review, assay.relation],
                reason="review_decision_predates_new_experimental_result",
                confidence=assay.relation.confidence,
                direction="unknown",
            )


def _detect_mapping_version_staleness(
    graph: KnowledgeGraph,
    emitted: dict[str, GraphRelation],
) -> None:
    expected_versions = graph.metadata.get("ontology_versions")
    if not isinstance(expected_versions, dict):
        return
    expected = expected_versions.get("ontology_mapping")
    if not expected:
        return
    for relation in graph.relations:
        if relation.relation_type != "ontology_mapping":
            continue
        observed = relation.metadata.get("mapping_version") or relation.metadata.get("version")
        if observed is None or str(observed) == str(expected):
            continue
        _emit_advisory_relation(
            emitted,
            predicate="stale_due_to",
            subject=relation.subject_entity_id,
            object_id=relation.object_entity_id,
            source_relations=[relation],
            reason="external_mapping_version_changed",
            confidence=0.7,
            direction="unknown",
            metadata={
                "observed_mapping_version": str(observed),
                "expected_mapping_version": str(expected),
            },
        )


def _emit_advisory_relation(
    emitted: dict[str, GraphRelation],
    *,
    predicate: str,
    subject: str,
    object_id: str,
    source_relations: list[GraphRelation],
    reason: str,
    confidence: float,
    direction: str,
    metadata: dict[str, object] | None = None,
) -> None:
    source_relation_ids = sorted({relation.relation_id for relation in source_relations})
    relation_id = (
        f"{predicate}:"
        + uuid5(NAMESPACE_URL, "|".join([reason, subject, object_id, *source_relation_ids])).hex[
            :16
        ]
    )
    if relation_id in emitted:
        return
    created_at = max(
        (relation.created_at for relation in source_relations), default=datetime.now(UTC)
    )
    emitted[relation_id] = GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type="inferred",
        confidence=_bounded(confidence),
        direction=direction,
        source_artifact_ids=[f"graph_contradiction_detector:{relation_id}"],
        source_record_ids=source_relation_ids,
        created_at=created_at,
        updated_at=created_at,
        metadata={
            "reason": reason,
            "advisory": True,
            "preserve_older_evidence": True,
            "temporal_provenance": [
                {
                    "relation_id": relation.relation_id,
                    "created_at": relation.created_at.isoformat(),
                    "updated_at": relation.updated_at.isoformat(),
                }
                for relation in source_relations
            ],
            **(metadata or {}),
        },
    )


def _finding(kind: str, relation: GraphRelation) -> GraphFinding:
    reason = str(relation.metadata.get("reason") or kind)
    return GraphFinding(
        finding_id=f"{kind}:{relation.relation_id}",
        name=reason.replace("_", " "),
        status=kind,
        reason=f"Advisory {kind}: {reason}. Older graph records are preserved.",
        entity_ids=[relation.subject_entity_id, relation.object_entity_id],
        relation_ids=list(relation.source_record_ids),
        severity="medium" if kind == "contradiction" else "low",
    )


def _candidate_id(relation: GraphRelation, entities: dict[str, GraphEntity]) -> str | None:
    for entity_id in (relation.subject_entity_id, relation.object_entity_id):
        entity = entities.get(entity_id)
        if entity is not None and entity.entity_type in {"molecule", "generated_molecule"}:
            return entity.entity_id
    candidate_name = relation.metadata.get("candidate_name")
    if candidate_name:
        return str(candidate_name)
    return None


def _target_symbol(relation: GraphRelation, entities: dict[str, GraphEntity]) -> str | None:
    target_symbol = relation.metadata.get("target_symbol")
    if target_symbol:
        return str(target_symbol)
    for entity_id in (relation.subject_entity_id, relation.object_entity_id):
        entity = entities.get(entity_id)
        if entity is not None and entity.entity_type == "target":
            return entity.name
    return None


def _endpoint_id(relation: GraphRelation) -> str | None:
    for key in ("endpoint_id", "endpoint", "assay_name"):
        value = relation.metadata.get(key)
        if value:
            return str(value)
    return None


def _outcome(relation: GraphRelation) -> str | None:
    value = relation.metadata.get("outcome_label") or relation.metadata.get("outcome")
    if value:
        return str(value).lower()
    if relation.predicate in {"supports", "validated_by"}:
        return "positive"
    if relation.predicate in {"contradicts", "contradicted_by"}:
        return "negative"
    return None


def _qc_status(relation: GraphRelation) -> str | None:
    value = relation.metadata.get("qc_status")
    return str(value).lower() if value else None


def _score(relation: GraphRelation) -> float | None:
    for key in ("score", "prediction_score", "priority_score", "docking_score"):
        value = relation.metadata.get(key)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _severity(relation: GraphRelation) -> str | None:
    value = relation.metadata.get("severity") or relation.metadata.get("risk_level")
    return str(value).lower() if value else None


def _is_positive_assay(context: RelationContext) -> bool:
    return context.outcome in {"positive", "active", "validated"} or context.relation.predicate in {
        "supports",
        "validated_by",
    }


def _is_negative_assay(context: RelationContext) -> bool:
    return context.outcome in {
        "negative",
        "inactive",
        "contradicted",
    } or context.relation.predicate in {
        "contradicts",
        "contradicted_by",
    }


def _touches_literature_claim(
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> bool:
    return any(
        (
            entities.get(entity_id) is not None
            and entities[entity_id].entity_type == "literature_claim"
        )
        for entity_id in (relation.subject_entity_id, relation.object_entity_id)
    )


def _claim_entity_id(
    relation: GraphRelation,
    entities: dict[str, GraphEntity],
) -> str | None:
    for entity_id in (relation.subject_entity_id, relation.object_entity_id):
        entity = entities.get(entity_id)
        if entity is not None and entity.entity_type == "literature_claim":
            return entity_id
    return None


def _same_claim_target(left: GraphRelation, right: GraphRelation) -> bool:
    left_targets = {left.subject_entity_id, left.object_entity_id}
    right_targets = {right.subject_entity_id, right.object_entity_id}
    if left_targets & right_targets:
        return True
    return bool(
        set(left.metadata.get("entity_ids", []) or [])
        & set(right.metadata.get("entity_ids", []) or [])
    )


def _same_candidate_target(left: RelationContext, right: RelationContext) -> bool:
    if left.candidate_id != right.candidate_id:
        return False
    if left.target_symbol and right.target_symbol and left.target_symbol != right.target_symbol:
        return False
    return True


def _is_accept_decision(relation: GraphRelation) -> bool:
    decision = str(relation.metadata.get("decision") or "").lower()
    return any(token in decision for token in ("accept", "select", "advance", "followup"))


def _is_critical_or_safety(context: RelationContext) -> bool:
    text = " ".join(
        str(value).lower()
        for value in (
            context.severity,
            context.relation.metadata.get("risk_level"),
            context.relation.metadata.get("category"),
            context.relation.metadata.get("label"),
            context.relation.metadata.get("risk"),
            context.relation.object_entity_id,
        )
        if value is not None
    )
    return any(term in text for term in CRITICAL_RISK_TERMS | {"safety", "developability", "herg"})


def _is_generated_candidate(candidate_id: str, entities: dict[str, GraphEntity]) -> bool:
    entity = entities.get(candidate_id)
    if entity is not None:
        return entity.entity_type == "generated_molecule"
    return "generated" in candidate_id.lower()


def _trained_at(relation: GraphRelation) -> datetime | None:
    value = relation.metadata.get("trained_at") or relation.metadata.get("training_cutoff")
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _bounded(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "ContradictionReport",
    "StalenessReport",
    "build_contradiction_report",
    "build_staleness_report",
    "detect_contradiction_relations",
    "detect_contradictions",
    "detect_staleness_relations",
]
