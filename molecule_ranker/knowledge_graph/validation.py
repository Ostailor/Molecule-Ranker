from __future__ import annotations

from molecule_ranker.knowledge_graph.schemas import GraphValidationReport, KnowledgeGraph


def validate_knowledge_graph(graph: KnowledgeGraph) -> GraphValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    entity_ids = {entity.entity_id for entity in graph.entities}
    for relation in graph.relations:
        if (
            relation.source_entity_id not in entity_ids
            or relation.target_entity_id not in entity_ids
        ):
            errors.append(f"{relation.relation_id}: relation references missing entity")
        if relation.assertion_type == "graph_inferred":
            warnings.append(
                f"{relation.relation_id}: graph-inferred relation is a hypothesis until backed."
            )
        if relation.relation_type == "inferred" and relation.predicate in {
            "validated_by",
            "supported_by",
            "supports",
        }:
            errors.append(f"{relation.relation_id}: inferred relation cannot create evidence")
        if relation.predicate in {"validated_by", "contradicted_by", "contradicts"} and not any(
            source.source_type in {"assay_result", "experimental"} for source in relation.provenance
        ):
            errors.append(f"{relation.relation_id}: assay relation lacks assay-result provenance")
    return GraphValidationReport(
        status="fail" if errors else "pass", errors=errors, warnings=warnings
    )
