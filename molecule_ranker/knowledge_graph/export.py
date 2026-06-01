from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph

GraphExportFormat = Literal["json", "csv", "ttl", "graphml"]

SECRET_KEY_RE = re.compile(
    r"(secret|password|token|api[_-]?key|oauth|credential|private[_-]?key|session|cookie)",
    re.I,
)
CACHE_KEY_RE = re.compile(r"(cache|http_client|raw_response|response_body)", re.I)
COPYRIGHT_TEXT_KEYS = {
    "full_text",
    "article_text",
    "article_body",
    "raw_article",
    "raw_pdf_text",
    "copyrighted_text",
    "body",
}
MAX_TEXT_EXPORT_CHARS = 500
BASE_IRI = "https://molecule-ranker.local/kg/"


def export_graph(
    graph: KnowledgeGraph, output_path: Path, export_format: GraphExportFormat
) -> Path:
    if export_format == "json":
        return export_graph_json(graph, output_path)
    if export_format == "csv":
        export_graph_csv(graph, output_path)
        return output_path
    if export_format == "ttl":
        return export_graph_turtle(graph, output_path)
    if export_format == "graphml":
        return export_graph_graphml(graph, output_path)
    raise ValueError(f"Unsupported graph export format: {export_format}")


def export_graph_json(graph: KnowledgeGraph, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sanitized_graph_payload(graph), indent=2, sort_keys=True) + "\n"
    )
    return output_path


def export_graph_csv(graph: KnowledgeGraph, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = output_dir / "nodes.csv"
    edges_path = output_dir / "edges.csv"
    entities = [_sanitized_entity(entity) for entity in graph.entities]
    relations = [_sanitized_relation(relation) for relation in graph.relations]
    with nodes_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "entity_id",
                "entity_type",
                "name",
                "canonical_id",
                "identifiers",
                "source_artifact_ids",
                "provenance_refs",
                "computational_hypothesis",
                "prediction_not_evidence",
                "metadata",
            ],
        )
        writer.writeheader()
        for entity in entities:
            writer.writerow(
                {
                    "entity_id": entity["entity_id"],
                    "entity_type": entity["entity_type"],
                    "name": entity["name"],
                    "canonical_id": entity.get("canonical_id") or "",
                    "identifiers": json.dumps(entity.get("identifiers", {}), sort_keys=True),
                    "source_artifact_ids": json.dumps(entity.get("source_artifact_ids", [])),
                    "provenance_refs": json.dumps(entity.get("provenance_refs", [])),
                    "computational_hypothesis": bool(
                        entity.get("metadata", {}).get("computational_hypothesis")
                    ),
                    "prediction_not_evidence": bool(
                        entity.get("metadata", {}).get("prediction_not_evidence")
                    ),
                    "metadata": json.dumps(entity.get("metadata", {}), sort_keys=True),
                }
            )
    with edges_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "relation_id",
                "subject_entity_id",
                "predicate",
                "object_entity_id",
                "relation_type",
                "confidence",
                "direction",
                "source_artifact_ids",
                "source_record_ids",
                "metadata",
            ],
        )
        writer.writeheader()
        for relation in relations:
            writer.writerow(
                {
                    "relation_id": relation["relation_id"],
                    "subject_entity_id": relation["subject_entity_id"],
                    "predicate": relation["predicate"],
                    "object_entity_id": relation["object_entity_id"],
                    "relation_type": relation["relation_type"],
                    "confidence": relation["confidence"],
                    "direction": relation.get("direction") or "",
                    "source_artifact_ids": json.dumps(relation.get("source_artifact_ids", [])),
                    "source_record_ids": json.dumps(relation.get("source_record_ids", [])),
                    "metadata": json.dumps(relation.get("metadata", {}), sort_keys=True),
                }
            )
    return {"nodes": nodes_path, "edges": edges_path}


def export_graph_turtle(graph: KnowledgeGraph, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "@prefix mr: <https://molecule-ranker.local/ontology/> .",
        "@prefix kg: <https://molecule-ranker.local/kg/> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    for entity in graph.entities:
        sanitized = _sanitized_entity(entity)
        entity_iri = _entity_iri(entity.entity_id)
        lines.append(f"{entity_iri} rdf:type mr:{_class_name(str(entity.entity_type))} .")
        lines.append(f"{entity_iri} rdfs:label {_literal(str(sanitized['name']))} .")
        if entity.entity_type == "generated_molecule":
            lines.append(f'{entity_iri} mr:computationalHypothesis "true"^^xsd:boolean .')
        if entity.entity_type == "model_prediction":
            lines.append(f'{entity_iri} mr:predictionNotEvidence "true"^^xsd:boolean .')
        for prefix, value in sorted((sanitized.get("identifiers") or {}).items()):
            lines.append(f"{entity_iri} mr:identifier {_literal(f'{prefix}:{value}')} .")
        for provenance in sanitized.get("provenance_refs") or []:
            lines.append(f"{entity_iri} prov:wasDerivedFrom {_literal(str(provenance))} .")
    for relation in graph.relations:
        sanitized = _sanitized_relation(relation)
        relation_iri = _relation_iri(relation.relation_id)
        subject = _entity_iri(relation.subject_entity_id)
        object_id = _entity_iri(relation.object_entity_id)
        predicate = (
            "owl:sameAs"
            if relation.predicate == "same_as"
            else f"mr:{_predicate_name(str(relation.predicate))}"
        )
        lines.append(f"{subject} {predicate} {object_id} .")
        lines.append(f"{relation_iri} rdf:type mr:GraphRelation .")
        lines.append(f"{relation_iri} mr:subject {subject} .")
        lines.append(f"{relation_iri} mr:predicate {_literal(str(relation.predicate))} .")
        lines.append(f"{relation_iri} mr:object {object_id} .")
        lines.append(f'{relation_iri} mr:confidence "{relation.confidence:.6g}"^^xsd:decimal .')
        lines.append(f"{relation_iri} mr:relationType {_literal(str(relation.relation_type))} .")
        for provenance in [
            *sanitized.get("source_artifact_ids", []),
            *sanitized.get("source_record_ids", []),
        ]:
            lines.append(f"{relation_iri} prov:wasDerivedFrom {_literal(str(provenance))} .")
        if relation.relation_type == "model_prediction":
            lines.append(f'{relation_iri} mr:predictionNotEvidence "true"^^xsd:boolean .')
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path


def export_graph_graphml(graph: KnowledgeGraph, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graphml = ET.Element("graphml", xmlns="http://graphml.graphdrawing.org/xmlns")
    graph_element = ET.SubElement(graphml, "graph", edgedefault="directed", id=graph.graph_id)
    for entity in graph.entities:
        sanitized = _sanitized_entity(entity)
        node = ET.SubElement(graph_element, "node", id=entity.entity_id)
        _data(node, "entity_type", str(entity.entity_type))
        _data(node, "name", str(sanitized["name"]))
        _data(node, "metadata", json.dumps(sanitized.get("metadata", {}), sort_keys=True))
    for relation in graph.relations:
        sanitized = _sanitized_relation(relation)
        edge = ET.SubElement(
            graph_element,
            "edge",
            id=relation.relation_id,
            source=relation.subject_entity_id,
            target=relation.object_entity_id,
        )
        _data(edge, "predicate", str(relation.predicate))
        _data(edge, "relation_type", str(relation.relation_type))
        _data(edge, "confidence", str(relation.confidence))
        _data(edge, "provenance", json.dumps(sanitized.get("source_artifact_ids", [])))
    ET.ElementTree(graphml).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def sanitized_graph_payload(graph: KnowledgeGraph) -> dict[str, Any]:
    payload = graph.model_dump(mode="json")
    payload["entities"] = [_sanitized_entity(entity) for entity in graph.entities]
    payload["relations"] = [_sanitized_relation(relation) for relation in graph.relations]
    payload["metadata"] = _sanitize_value(payload.get("metadata", {}))
    return _sanitize_value(payload)


def _sanitized_entity(entity: GraphEntity) -> dict[str, Any]:
    payload = entity.model_dump(mode="json")
    metadata = dict(_sanitize_value(payload.get("metadata", {})))
    if entity.entity_type == "generated_molecule":
        metadata["computational_hypothesis"] = True
        metadata["not_direct_evidence"] = True
    if entity.entity_type == "model_prediction":
        metadata["prediction_not_evidence"] = True
        metadata["not_evidence"] = True
    payload["metadata"] = metadata
    return _sanitize_value(payload)


def _sanitized_relation(relation: GraphRelation) -> dict[str, Any]:
    payload = relation.model_dump(mode="json")
    metadata = dict(_sanitize_value(payload.get("metadata", {})))
    if relation.relation_type == "model_prediction" or relation.predicate == "predicted_by_model":
        metadata["prediction_not_evidence"] = True
        metadata["not_evidence"] = True
    payload["metadata"] = metadata
    return _sanitize_value(payload)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text) or CACHE_KEY_RE.search(key_text):
                continue
            if key_text.lower() in COPYRIGHT_TEXT_KEYS:
                continue
            sanitized[key_text] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        if len(value) > MAX_TEXT_EXPORT_CHARS:
            return value[:MAX_TEXT_EXPORT_CHARS] + "... [truncated]"
        return value
    return value


def _entity_iri(entity_id: str) -> str:
    return f"<{BASE_IRI}entity/{quote(entity_id, safe='')}>"


def _relation_iri(relation_id: str) -> str:
    return f"<{BASE_IRI}relation/{quote(relation_id, safe='')}>"


def _literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _class_name(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", value) if part)


def _predicate_name(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    if not parts:
        return "relatedTo"
    return parts[0].lower() + "".join(part.capitalize() for part in parts[1:])


def _data(parent: ET.Element, key: str, value: str) -> None:
    element = ET.SubElement(parent, "data", key=key)
    element.text = value


__all__ = [
    "GraphExportFormat",
    "export_graph",
    "export_graph_csv",
    "export_graph_graphml",
    "export_graph_json",
    "export_graph_turtle",
    "sanitized_graph_payload",
]
