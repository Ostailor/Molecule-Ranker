from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.knowledge_graph.schemas import (
    CrossProgramAnalysis,
    GraphEntity,
    GraphFinding,
    GraphPattern,
    GraphRecommendation,
    GraphRelation,
    KnowledgeGraph,
    TargetPattern,
)

GRAPH_QUERY_WARNING = "Graph query results are graph-derived summaries, not new evidence."


class GraphEntityRef(BaseModel):
    entity_id: str
    entity_type: str
    name: str


class GraphRelationRef(BaseModel):
    relation_id: str
    predicate: str
    subject_entity_id: str
    object_entity_id: str
    provenance: list[str] = Field(default_factory=list)


class GraphQueryResult(BaseModel):
    query_name: str
    entity_refs: list[GraphEntityRef] = Field(default_factory=list)
    relation_refs: list[GraphRelationRef] = Field(default_factory=list)
    path_entity_ids: list[str] = Field(default_factory=list)
    path_relation_ids: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=lambda: [GRAPH_QUERY_WARNING])
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphReasoner:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self.entities = graph.entity_map()
        self.relations_by_id = {relation.relation_id: relation for relation in graph.relations}
        self.relations_by_subject: dict[str, list[GraphRelation]] = defaultdict(list)
        self.relations_by_entity: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in graph.relations:
            self.relations_by_subject[relation.subject_entity_id].append(relation)
            self.relations_by_entity[relation.subject_entity_id].append(relation)
            self.relations_by_entity[relation.object_entity_id].append(relation)

    def candidates_for_target(self, target_symbol: str) -> list[GraphQueryResult]:
        targets = self._matching_entities("target", target_symbol)
        target_ids = {target.entity_id for target in targets}
        results = []
        for relation in self.graph.relations:
            if relation.predicate not in {"targets", "modulates", "hypothesizes"}:
                continue
            if relation.object_entity_id not in target_ids:
                continue
            candidate = self.entities.get(relation.subject_entity_id)
            target = self.entities.get(relation.object_entity_id)
            if candidate is None or target is None:
                continue
            results.append(
                self._result(
                    "candidates_for_target",
                    [candidate, target],
                    [relation],
                    confidence=relation.confidence,
                    metadata={"target_symbol": target_symbol},
                )
            )
        return _sort_results(results)

    def mechanisms_for_disease(self, disease_name: str) -> list[GraphQueryResult]:
        diseases = self._matching_entities("disease", disease_name)
        disease_ids = {disease.entity_id for disease in diseases}
        results: list[GraphQueryResult] = []
        for disease_target in self.graph.relations:
            if (
                disease_target.predicate != "associated_with"
                or disease_target.subject_entity_id not in disease_ids
            ):
                continue
            target = self.entities.get(disease_target.object_entity_id)
            disease = self.entities.get(disease_target.subject_entity_id)
            if disease is None or target is None:
                continue
            for mechanism_relation in self._relations_from(target.entity_id):
                mechanism = self.entities.get(mechanism_relation.object_entity_id)
                if mechanism is None or mechanism.entity_type not in {"mechanism", "pathway"}:
                    continue
                if mechanism_relation.predicate not in {"has_mechanism", "associated_with"}:
                    continue
                results.append(
                    self._result(
                        "mechanisms_for_disease",
                        [disease, target, mechanism],
                        [disease_target, mechanism_relation],
                        confidence=min(disease_target.confidence, mechanism_relation.confidence),
                        metadata={"disease_name": disease_name},
                    )
                )
        return _sort_results(results)

    def generated_molecules_without_direct_evidence(self) -> list[GraphQueryResult]:
        results = []
        for entity in self.entities.values():
            if entity.entity_type != "generated_molecule":
                continue
            direct_experimental = self._has_direct_experimental_result(entity.entity_id)
            no_evidence_relations = [
                relation
                for relation in self.relations_by_entity.get(entity.entity_id, [])
                if relation.predicate == "has_no_direct_evidence"
            ]
            if direct_experimental or not no_evidence_relations:
                continue
            results.append(
                self._result(
                    "generated_molecules_without_direct_evidence",
                    [entity],
                    no_evidence_relations,
                    confidence=max(relation.confidence for relation in no_evidence_relations),
                    warnings=[
                        GRAPH_QUERY_WARNING,
                        "Generated molecules without direct evidence remain hypotheses.",
                    ],
                )
            )
        return _sort_results(results)

    def candidates_with_contradictory_evidence(self) -> list[GraphQueryResult]:
        results = []
        for relation in self.graph.relations:
            if relation.predicate not in {"contradicts", "contradicted_by"}:
                continue
            candidate = self._candidate_for_relation(relation)
            if candidate is None:
                continue
            entities = [candidate]
            for entity_id in (relation.subject_entity_id, relation.object_entity_id):
                entity = self.entities.get(entity_id)
                if entity is not None and entity.entity_id != candidate.entity_id:
                    entities.append(entity)
            results.append(
                self._result(
                    "candidates_with_contradictory_evidence",
                    entities,
                    [relation],
                    confidence=relation.confidence,
                    warnings=[GRAPH_QUERY_WARNING, "Contradictions require expert review."],
                    metadata={"reason": relation.metadata.get("reason")},
                )
            )
        return _sort_results(results)

    def scaffolds_with_positive_assay_history(self) -> list[GraphQueryResult]:
        scaffold_links = self._candidate_scaffold_links()
        positive = self._positive_assay_relations_by_candidate()
        results = []
        for candidate_id, links in scaffold_links.items():
            if candidate_id not in positive:
                continue
            candidate = self.entities.get(candidate_id)
            if candidate is None:
                continue
            for link in links:
                scaffold = self.entities.get(link.object_entity_id)
                if scaffold is None:
                    continue
                relations = [link, *positive[candidate_id]]
                results.append(
                    self._result(
                        "scaffolds_with_positive_assay_history",
                        [scaffold, candidate],
                        relations,
                        confidence=_mean_confidence(relations),
                    )
                )
        return _dedupe_results(_sort_results(results))

    def targets_with_repeated_developability_failures(self) -> list[GraphQueryResult]:
        target_candidates = self._target_candidates()
        risk_relations = self._risk_relations_by_candidate()
        results = []
        for target_id, candidate_ids in target_candidates.items():
            failed_candidates = [
                candidate_id for candidate_id in candidate_ids if candidate_id in risk_relations
            ]
            if len(set(failed_candidates)) < 2:
                continue
            target = self.entities.get(target_id)
            if target is None:
                continue
            relations = [
                relation
                for candidate_id in failed_candidates
                for relation in risk_relations[candidate_id]
            ]
            entities = [target, *self._entities_for_ids(failed_candidates)]
            results.append(
                self._result(
                    "targets_with_repeated_developability_failures",
                    entities,
                    relations,
                    confidence=_mean_confidence(relations),
                    warnings=[
                        GRAPH_QUERY_WARNING,
                        "Repeated developability failures are advisory.",
                    ],
                    metadata={"failed_candidate_count": len(set(failed_candidates))},
                )
            )
        return _sort_results(results)

    def mechanisms_supported_across_programs(self) -> list[GraphQueryResult]:
        mechanism_relations: dict[str, list[GraphRelation]] = defaultdict(list)
        programs: dict[str, set[str]] = defaultdict(set)
        for relation in self.graph.relations:
            mechanism = self.entities.get(relation.object_entity_id)
            if relation.predicate != "has_mechanism" or mechanism is None:
                continue
            if mechanism.entity_type not in {"mechanism", "pathway"}:
                continue
            mechanism_relations[mechanism.entity_id].append(relation)
            programs[mechanism.entity_id].update(_program_refs(relation))
        results = []
        for mechanism_id, refs in mechanism_relations.items():
            if len(programs[mechanism_id]) < 2:
                continue
            mechanism = self.entities.get(mechanism_id)
            if mechanism is None:
                continue
            results.append(
                self._result(
                    "mechanisms_supported_across_programs",
                    [mechanism],
                    refs,
                    confidence=_mean_confidence(refs),
                    metadata={"program_ids": sorted(programs[mechanism_id])},
                )
            )
        return _sort_results(results)

    def molecules_with_safety_concerns_across_programs(self) -> list[GraphQueryResult]:
        risk_relations = self._risk_relations_by_candidate()
        results = []
        for candidate_id, relations in risk_relations.items():
            programs = {program for relation in relations for program in _program_refs(relation)}
            if len(programs) < 2:
                continue
            candidate = self.entities.get(candidate_id)
            if candidate is None:
                continue
            entities = [
                candidate,
                *self._entities_for_ids([relation.object_entity_id for relation in relations]),
            ]
            results.append(
                self._result(
                    "molecules_with_safety_concerns_across_programs",
                    entities,
                    relations,
                    confidence=_mean_confidence(relations),
                    warnings=[GRAPH_QUERY_WARNING, "Safety concerns require expert review."],
                    metadata={"program_ids": sorted(programs)},
                )
            )
        return _sort_results(results)

    def portfolios_reusing_same_scaffold_risk(self) -> list[GraphQueryResult]:
        scaffold_links = self._candidate_scaffold_links()
        risk_links = self._risk_relations_by_candidate()
        portfolio_links = [
            relation
            for relation in self.graph.relations
            if relation.predicate == "selected_in_portfolio"
        ]
        grouped: dict[tuple[str, str], list[GraphRelation]] = defaultdict(list)
        for selected in portfolio_links:
            candidate_id = selected.subject_entity_id
            for scaffold in scaffold_links.get(candidate_id, []):
                for risk in risk_links.get(candidate_id, []):
                    grouped[(scaffold.object_entity_id, risk.object_entity_id)].extend(
                        [selected, scaffold, risk]
                    )
        results = []
        for (scaffold_id, risk_id), relations in grouped.items():
            portfolio_ids = {
                relation.object_entity_id
                for relation in relations
                if relation.predicate == "selected_in_portfolio"
            }
            if len(portfolio_ids) < 2:
                continue
            entities = self._entities_for_ids([scaffold_id, risk_id, *portfolio_ids])
            results.append(
                self._result(
                    "portfolios_reusing_same_scaffold_risk",
                    entities,
                    relations,
                    confidence=_mean_confidence(relations),
                    warnings=[GRAPH_QUERY_WARNING, "Portfolio reuse does not prove shared risk."],
                    metadata={"portfolio_ids": sorted(portfolio_ids)},
                )
            )
        return _dedupe_results(_sort_results(results))

    def projects_with_stale_model_predictions(self) -> list[GraphQueryResult]:
        stale_relations = [
            relation
            for relation in self.graph.relations
            if relation.predicate == "stale_due_to"
            and str(relation.metadata.get("reason") or "").startswith("model_")
        ]
        results = []
        for relation in stale_relations:
            model = self.entities.get(relation.subject_entity_id)
            if model is None:
                continue
            project_id = (
                relation.metadata.get("project_id")
                or model.metadata.get("project_id")
                or _first_project_ref(relation)
            )
            project = self.entities.get(str(project_id)) if project_id else None
            entities = [model]
            if project is not None:
                entities.insert(0, project)
            results.append(
                self._result(
                    "projects_with_stale_model_predictions",
                    entities,
                    [relation],
                    confidence=relation.confidence,
                    warnings=[
                        GRAPH_QUERY_WARNING,
                        "Stale predictions should be reviewed before reuse.",
                    ],
                    metadata={"project_id": project_id},
                )
            )
        return _sort_results(results)

    def graph_paths_between_disease_and_molecule(
        self,
        disease_name: str,
        molecule_id: str,
        *,
        max_depth: int = 4,
    ) -> list[GraphQueryResult]:
        diseases = self._matching_entities("disease", disease_name)
        molecules = self._matching_entities("molecule", molecule_id) + self._matching_entities(
            "generated_molecule", molecule_id
        )
        target_ids = {entity.entity_id for entity in molecules}
        results = []
        for disease in diseases:
            for entity_ids, relation_ids in self._paths(
                disease.entity_id, target_ids, max_depth=max_depth
            ):
                relations = [self._relation_by_id(relation_id) for relation_id in relation_ids]
                if any(
                    relation is None or not _relation_provenance(relation) for relation in relations
                ):
                    continue
                concrete_relations = [relation for relation in relations if relation is not None]
                results.append(
                    self._result(
                        "graph_paths_between_disease_and_molecule",
                        self._entities_for_ids(entity_ids),
                        concrete_relations,
                        confidence=_mean_confidence(concrete_relations),
                        path_entity_ids=entity_ids,
                        path_relation_ids=relation_ids,
                        warnings=[GRAPH_QUERY_WARNING, "Graph paths do not prove causality."],
                    )
                )
        return _sort_results(results)

    def evidence_gaps_for_candidate(self, candidate_id: str) -> list[GraphQueryResult]:
        candidates = self._matching_entities("molecule", candidate_id) + self._matching_entities(
            "generated_molecule", candidate_id
        )
        results = []
        for candidate in candidates:
            relations = [
                relation
                for relation in self.graph.relations
                if candidate.entity_id in {relation.subject_entity_id, relation.object_entity_id}
            ]
            warnings = [GRAPH_QUERY_WARNING]
            if (
                candidate.entity_type == "generated_molecule"
                and not self._has_direct_experimental_result(candidate.entity_id)
            ):
                warnings.append("Generated candidate lacks direct experimental evidence.")
            if not any(_is_positive_relation(relation) for relation in relations):
                warnings.append("No positive source-backed or QC-passed assay support found.")
            if any(
                relation.predicate in {"contradicts", "contradicted_by"} for relation in relations
            ):
                warnings.append("Candidate has contradictory evidence requiring review.")
            if any(relation.predicate == "stale_due_to" for relation in relations):
                warnings.append("Candidate has stale graph-linked records.")
            if not any(
                relation.predicate in {"has_developability_risk", "blocked_by"}
                for relation in relations
            ):
                warnings.append("No developability risk assessment linked in graph.")
            if len(warnings) == 1:
                warnings.append("No obvious evidence gaps detected in the graph.")
            results.append(
                self._result(
                    "evidence_gaps_for_candidate",
                    [candidate],
                    relations,
                    confidence=_mean_confidence(relations) if relations else 0.0,
                    warnings=warnings,
                    metadata={"gap_count": len(warnings) - 1},
                )
            )
        return _sort_results(results)

    def _result(
        self,
        query_name: str,
        entities: list[GraphEntity],
        relations: list[GraphRelation],
        *,
        confidence: float,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        path_entity_ids: list[str] | None = None,
        path_relation_ids: list[str] | None = None,
    ) -> GraphQueryResult:
        return GraphQueryResult(
            query_name=query_name,
            entity_refs=[_entity_ref(entity) for entity in _unique_entities(entities)],
            relation_refs=[_relation_ref(relation) for relation in _unique_relations(relations)],
            path_entity_ids=path_entity_ids or [],
            path_relation_ids=path_relation_ids or [],
            provenance=sorted(
                {
                    provenance
                    for relation in relations
                    for provenance in _relation_provenance(relation)
                }
            ),
            confidence=_bounded(confidence),
            warnings=warnings or [GRAPH_QUERY_WARNING],
            metadata=metadata or {},
        )

    def _matching_entities(self, entity_type: str, value: str) -> list[GraphEntity]:
        needle = value.lower()
        return [
            entity
            for entity in self.entities.values()
            if entity.entity_type == entity_type
            and (
                entity.entity_id == value
                or entity.name.lower() == needle
                or entity.entity_id.lower().endswith(needle)
                or needle in {item.lower() for item in entity.identifiers.values()}
            )
        ]

    def _relations_from(self, entity_id: str) -> list[GraphRelation]:
        return list(self.relations_by_subject.get(entity_id, []))

    def _relation_by_id(self, relation_id: str) -> GraphRelation | None:
        return self.relations_by_id.get(relation_id)

    def _candidate_for_relation(self, relation: GraphRelation) -> GraphEntity | None:
        for entity_id in (relation.subject_entity_id, relation.object_entity_id):
            entity = self.entities.get(entity_id)
            if entity is not None and entity.entity_type in {"molecule", "generated_molecule"}:
                return entity
        return None

    def _has_direct_experimental_result(self, entity_id: str) -> bool:
        return any(
            relation.relation_type == "experimental"
            and relation.predicate in {"supports", "contradicts", "validated_by", "contradicted_by"}
            for relation in self.relations_by_entity.get(entity_id, [])
        )

    def _candidate_scaffold_links(self) -> dict[str, list[GraphRelation]]:
        links: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in self.graph.relations:
            if relation.predicate == "has_scaffold":
                links[relation.subject_entity_id].append(relation)
        return links

    def _positive_assay_relations_by_candidate(self) -> dict[str, list[GraphRelation]]:
        grouped: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in self.graph.relations:
            if _is_positive_relation(relation):
                candidate = self._candidate_for_relation(relation)
                if candidate is not None:
                    grouped[candidate.entity_id].append(relation)
        return grouped

    def _risk_relations_by_candidate(self) -> dict[str, list[GraphRelation]]:
        grouped: dict[str, list[GraphRelation]] = defaultdict(list)
        for relation in self.graph.relations:
            if relation.predicate in {"has_developability_risk", "blocked_by"}:
                grouped[relation.subject_entity_id].append(relation)
        return grouped

    def _target_candidates(self) -> dict[str, set[str]]:
        grouped: dict[str, set[str]] = defaultdict(set)
        for relation in self.graph.relations:
            if relation.predicate in {"targets", "modulates", "hypothesizes"}:
                target = self.entities.get(relation.object_entity_id)
                candidate = self.entities.get(relation.subject_entity_id)
                if target is not None and candidate is not None and target.entity_type == "target":
                    grouped[target.entity_id].add(candidate.entity_id)
        return grouped

    def _entities_for_ids(self, entity_ids: list[str] | set[str]) -> list[GraphEntity]:
        return [self.entities[entity_id] for entity_id in entity_ids if entity_id in self.entities]

    def _paths(
        self, source_id: str, target_ids: set[str], *, max_depth: int
    ) -> list[tuple[list[str], list[str]]]:
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for relation in self.graph.relations:
            adjacency[relation.subject_entity_id].append(
                (relation.object_entity_id, relation.relation_id)
            )
            adjacency[relation.object_entity_id].append(
                (relation.subject_entity_id, relation.relation_id)
            )
        queue: deque[tuple[str, list[str], list[str]]] = deque([(source_id, [source_id], [])])
        paths = []
        while queue and len(paths) < 25:
            entity_id, entity_path, relation_path = queue.popleft()
            if entity_id in target_ids and relation_path:
                paths.append((entity_path, relation_path))
                continue
            if len(relation_path) >= max_depth:
                continue
            for next_entity, relation_id in adjacency.get(entity_id, []):
                if next_entity in entity_path:
                    continue
                queue.append(
                    (next_entity, [*entity_path, next_entity], [*relation_path, relation_id])
                )
        return paths


def candidates_for_target(graph: KnowledgeGraph, target_symbol: str) -> list[GraphQueryResult]:
    return GraphReasoner(graph).candidates_for_target(target_symbol)


def mechanisms_for_disease(graph: KnowledgeGraph, disease_name: str) -> list[GraphQueryResult]:
    return GraphReasoner(graph).mechanisms_for_disease(disease_name)


def generated_molecules_without_direct_evidence(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).generated_molecules_without_direct_evidence()


def candidates_with_contradictory_evidence(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).candidates_with_contradictory_evidence()


def scaffolds_with_positive_assay_history(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).scaffolds_with_positive_assay_history()


def targets_with_repeated_developability_failures(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).targets_with_repeated_developability_failures()


def mechanisms_supported_across_programs(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).mechanisms_supported_across_programs()


def molecules_with_safety_concerns_across_programs(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).molecules_with_safety_concerns_across_programs()


def portfolios_reusing_same_scaffold_risk(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).portfolios_reusing_same_scaffold_risk()


def projects_with_stale_model_predictions(graph: KnowledgeGraph) -> list[GraphQueryResult]:
    return GraphReasoner(graph).projects_with_stale_model_predictions()


def graph_paths_between_disease_and_molecule(
    graph: KnowledgeGraph,
    disease_name: str,
    molecule_id: str,
    *,
    max_depth: int = 4,
) -> list[GraphQueryResult]:
    return GraphReasoner(graph).graph_paths_between_disease_and_molecule(
        disease_name, molecule_id, max_depth=max_depth
    )


def evidence_gaps_for_candidate(graph: KnowledgeGraph, candidate_id: str) -> list[GraphQueryResult]:
    return GraphReasoner(graph).evidence_gaps_for_candidate(candidate_id)


def analyze_cross_program_knowledge(
    graph: KnowledgeGraph,
    *,
    stale_after_days: int = 180,
) -> CrossProgramAnalysis:
    mechanism_patterns = _patterns_for_targets(
        graph,
        entity_type="mechanism",
        relation_type="has_mechanism",
        rationale="Mechanism recurs across graph-backed program artifacts.",
    )
    scaffold_patterns = _patterns_for_targets(
        graph,
        entity_type="scaffold",
        relation_type="has_scaffold",
        rationale="Scaffold or generated family recurs across candidates.",
    )
    risk_patterns = _patterns_for_targets(
        graph,
        entity_type="developability_risk",
        relation_type="blocked_by",
        rationale="Developability risk repeatedly blocks or penalizes candidates.",
    )
    target_patterns = _target_patterns(graph)
    contradictions = _contradictions(graph)
    novelty = _novelty(graph)
    hypotheses = _hypothesis_status(graph, stale_after_days=stale_after_days)
    review = _review_patterns(graph)
    recommendations = _recommendations(mechanism_patterns, target_patterns, risk_patterns)

    return CrossProgramAnalysis(
        recurring_mechanisms=mechanism_patterns,
        target_patterns=target_patterns,
        scaffold_patterns=scaffold_patterns,
        contradictions=contradictions,
        repeated_developability_risks=risk_patterns,
        novelty_assessments=novelty,
        hypothesis_status=hypotheses,
        review_outcome_patterns=review,
        recommendations=recommendations,
    )


def _patterns_for_targets(
    graph: KnowledgeGraph,
    *,
    entity_type: str,
    relation_type: str,
    rationale: str,
) -> list[GraphPattern]:
    entities = graph.entity_map()
    related: dict[str, set[str]] = defaultdict(set)
    programs: dict[str, set[str]] = defaultdict(set)
    for relation in graph.relations:
        target = entities.get(relation.target_entity_id)
        if (
            relation.predicate != relation_type
            or target is None
            or target.entity_type != entity_type
        ):
            continue
        related[target.entity_id].add(relation.source_entity_id)
        for provenance in relation.provenance:
            programs[target.entity_id].add(provenance.source_id)
    patterns = [
        GraphPattern(
            name=entities[entity_id].name,
            entity_id=entity_id,
            count=len(source_ids),
            program_ids=sorted(programs[entity_id]),
            related_entity_ids=sorted(source_ids),
            rationale=rationale,
        )
        for entity_id, source_ids in related.items()
        if len(source_ids) >= 1
    ]
    return sorted(patterns, key=lambda item: (-item.count, item.name))


def _target_patterns(graph: KnowledgeGraph) -> list[TargetPattern]:
    entities = graph.entity_map()
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    for relation in graph.relations:
        target = entities.get(relation.target_entity_id)
        if relation.predicate != "targets" or target is None or target.entity_type != "target":
            continue
        score = relation.metadata.get("candidate_score")
        if isinstance(score, int | float):
            if score >= 0.7:
                stats[target.entity_id]["strong"] += 1
            elif score <= 0.4:
                stats[target.entity_id]["weak"] += 1
        if relation.polarity == "contradicts":
            stats[target.entity_id]["contradictions"] += 1
    for relation in graph.relations:
        if relation.predicate == "contradicted_by" or relation.predicate == "contradicts":
            target_symbol = relation.metadata.get("target_symbol")
            if target_symbol:
                target_id = f"target:symbol:{target_symbol}"
                stats[target_id]["weak"] += 1
                stats[target_id]["contradictions"] += 1
    patterns = []
    for entity_id, counts in stats.items():
        target = entities.get(entity_id)
        patterns.append(
            TargetPattern(
                target_entity_id=entity_id,
                name=target.name if target else entity_id,
                strong_candidate_count=counts["strong"],
                weak_candidate_count=counts["weak"],
                contradiction_count=counts["contradictions"],
                rationale="Target has repeated candidate, assay, or portfolio outcomes.",
            )
        )
    return sorted(
        patterns,
        key=lambda item: (
            -(item.strong_candidate_count + item.weak_candidate_count + item.contradiction_count),
            item.name,
        ),
    )


def _contradictions(graph: KnowledgeGraph) -> list[GraphFinding]:
    entities = graph.entity_map()
    findings = []
    for relation in graph.relations:
        if relation.predicate not in {"contradicted_by", "contradicts"}:
            continue
        source = entities.get(relation.source_entity_id)
        target = entities.get(relation.target_entity_id)
        findings.append(
            GraphFinding(
                finding_id=f"contradiction:{relation.relation_id}",
                name=source.name if source else relation.source_entity_id,
                status="contradicted",
                reason=(
                    f"Computational or literature hypothesis has a negative assay outcome "
                    f"or contradiction from {target.name if target else relation.target_entity_id}."
                ),
                entity_ids=[relation.source_entity_id, relation.target_entity_id],
                relation_ids=[relation.relation_id],
                severity="medium",
            )
        )
    return findings


def _novelty(graph: KnowledgeGraph) -> list[GraphFinding]:
    entities = graph.entity_map()
    findings = []
    for relation in graph.relations:
        if relation.predicate != "novel_vs_known":
            continue
        status = str(relation.metadata.get("status") or "requires_review")
        findings.append(
            GraphFinding(
                finding_id=f"novelty:{relation.relation_id}",
                name=entities.get(
                    relation.source_entity_id, entities[relation.target_entity_id]
                ).name,
                status=status,
                reason="Generated chemistry overlaps a prior known molecule or series.",
                entity_ids=[relation.source_entity_id, relation.target_entity_id],
                relation_ids=[relation.relation_id],
                severity="medium" if status == "rediscovered_known_chemistry" else "info",
            )
        )
    return findings


def _hypothesis_status(graph: KnowledgeGraph, *, stale_after_days: int) -> list[GraphFinding]:
    findings = []
    for relation in graph.relations:
        if not relation.is_hypothesis:
            continue
        stale = relation.is_stale or (
            relation.created_at
            and (relation.created_at.tzinfo is not None)
            and (relation.stale_after_days is None)
        )
        if not stale and relation.stale_after_days is None:
            age_days = (graph.generated_at - relation.created_at).days
            stale = age_days > stale_after_days
        status = "stale" if stale else "unsupported"
        findings.append(
            GraphFinding(
                finding_id=f"hypothesis:{relation.relation_id}",
                name=relation.predicate,
                status=status,
                reason=(
                    "Graph-inferred hypothesis is stale or unsupported until source evidence, "
                    "assay result, or expert review is attached."
                ),
                entity_ids=[relation.source_entity_id, relation.target_entity_id],
                relation_ids=[relation.relation_id],
                severity="medium" if stale else "low",
            )
        )
    return findings


def _review_patterns(graph: KnowledgeGraph) -> list[GraphFinding]:
    findings = []
    for relation in graph.relations:
        if relation.predicate == "reviewed_as":
            decision = relation.metadata.get("decision", "reviewed")
            findings.append(
                GraphFinding(
                    finding_id=f"review:{relation.relation_id}",
                    name=str(decision),
                    status="review_observed",
                    reason="Expert-review decision is linked for later outcome correlation.",
                    entity_ids=[relation.source_entity_id, relation.target_entity_id],
                    relation_ids=[relation.relation_id],
                )
            )
    return findings


def _recommendations(
    mechanisms: list[GraphPattern],
    targets: list[TargetPattern],
    risks: list[GraphPattern],
) -> list[GraphRecommendation]:
    recommendations = []
    if mechanisms:
        recommendations.append(
            GraphRecommendation(
                recommendation_id="reuse-recurring-mechanism",
                rationale=f"Reuse prior knowledge for recurring mechanism {mechanisms[0].name}.",
                reuse_entity_ids=[mechanisms[0].entity_id],
                warnings=["Treat graph paths as hypotheses unless source-backed."],
            )
        )
    if targets:
        recommendations.append(
            GraphRecommendation(
                recommendation_id="review-repeated-target-outcomes",
                rationale=(
                    f"Reuse target outcome history for {targets[0].name} before new prioritization."
                ),
                reuse_entity_ids=[targets[0].target_entity_id],
                warnings=["Target patterns do not prove causality, activity, or safety."],
            )
        )
    if risks:
        recommendations.append(
            GraphRecommendation(
                recommendation_id="check-repeated-developability-blocker",
                rationale=f"Reuse risk history for repeated blocker {risks[0].name}.",
                reuse_entity_ids=[risks[0].entity_id],
                warnings=["Risk recurrence is advisory and requires expert review."],
            )
        )
    return recommendations


def _entity_ref(entity: GraphEntity) -> GraphEntityRef:
    return GraphEntityRef(
        entity_id=entity.entity_id,
        entity_type=str(entity.entity_type),
        name=entity.name,
    )


def _relation_ref(relation: GraphRelation) -> GraphRelationRef:
    return GraphRelationRef(
        relation_id=relation.relation_id,
        predicate=str(relation.predicate),
        subject_entity_id=relation.subject_entity_id,
        object_entity_id=relation.object_entity_id,
        provenance=_relation_provenance(relation),
    )


def _relation_provenance(relation: GraphRelation) -> list[str]:
    refs = [*relation.source_artifact_ids, *relation.source_record_ids]
    return sorted({ref for ref in refs if ref})


def _unique_entities(entities: list[GraphEntity]) -> list[GraphEntity]:
    seen: set[str] = set()
    unique = []
    for entity in entities:
        if entity.entity_id in seen:
            continue
        seen.add(entity.entity_id)
        unique.append(entity)
    return unique


def _unique_relations(relations: list[GraphRelation]) -> list[GraphRelation]:
    seen: set[str] = set()
    unique = []
    for relation in relations:
        if relation.relation_id in seen:
            continue
        seen.add(relation.relation_id)
        unique.append(relation)
    return unique


def _sort_results(results: list[GraphQueryResult]) -> list[GraphQueryResult]:
    return sorted(
        results,
        key=lambda result: (
            -result.confidence,
            ",".join(ref.entity_id for ref in result.entity_refs),
            ",".join(ref.relation_id for ref in result.relation_refs),
        ),
    )


def _dedupe_results(results: list[GraphQueryResult]) -> list[GraphQueryResult]:
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    deduped = []
    for result in results:
        key = (
            result.query_name,
            tuple(sorted(ref.entity_id for ref in result.entity_refs)),
            tuple(sorted(ref.relation_id for ref in result.relation_refs)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def _mean_confidence(relations: list[GraphRelation]) -> float:
    if not relations:
        return 0.0
    return _bounded(sum(relation.confidence for relation in relations) / len(relations))


def _bounded(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _is_positive_relation(relation: GraphRelation) -> bool:
    if relation.metadata.get("qc_status") == "failed":
        return False
    outcome = str(
        relation.metadata.get("outcome_label") or relation.metadata.get("outcome") or ""
    ).lower()
    if outcome in {"positive", "active", "validated"}:
        return True
    return relation.predicate in {"supports", "validated_by"} and relation.relation_type in {
        "experimental",
        "evidence_backed",
        "literature",
    }


def _program_refs(relation: GraphRelation) -> set[str]:
    refs = set(_relation_provenance(relation))
    programs = {
        str(value)
        for key, value in relation.metadata.items()
        if key in {"program_id", "project_id", "run_id"} and value
    }
    refs.update(programs)
    return refs


def _first_project_ref(relation: GraphRelation) -> str | None:
    for ref in _relation_provenance(relation):
        if ref.startswith("project:"):
            return ref
    return None
