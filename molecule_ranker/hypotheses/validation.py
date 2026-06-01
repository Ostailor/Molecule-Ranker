from __future__ import annotations

import re
from typing import Any

from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph

from .schemas import Hypothesis, HypothesisValidationReport


class HypothesisValidationError(ValueError):
    pass


def validate_hypothesis_references(
    hypothesis: Hypothesis,
    graph: KnowledgeGraph,
) -> HypothesisValidationReport:
    allowed = allowed_hypothesis_reference_sets(graph)
    errors = []
    for value in sorted(set(hypothesis.entity_ids) - allowed["entity_ids"]):
        errors.append(f"unknown entity ID: {value}")
    for value in sorted(set(hypothesis.relation_ids) - allowed["relation_ids"]):
        errors.append(f"unknown relation ID: {value}")
    for value in sorted(set(hypothesis.provenance_ids) - allowed["provenance_ids"]):
        errors.append(f"unknown provenance ID: {value}")
    for value in sorted(set(hypothesis.artifact_ids) - allowed["artifact_ids"]):
        errors.append(f"unknown artifact ID: {value}")
    for value in sorted(set(hypothesis.citation_ids) - allowed["citation_ids"]):
        errors.append(f"unknown citation ID: {value}")
    for warning in detect_hypothesis_guardrail_violations(
        " ".join([hypothesis.title, hypothesis.summary])
    ):
        errors.append(warning)
    return HypothesisValidationReport(status="fail" if errors else "pass", errors=errors)


def validate_hypothesis_set(hypotheses: list[Hypothesis], graph: KnowledgeGraph) -> None:
    failures = [
        error
        for hypothesis in hypotheses
        for error in validate_hypothesis_references(hypothesis, graph).errors
    ]
    if failures:
        raise HypothesisValidationError("; ".join(failures))


def allowed_hypothesis_reference_sets(graph: KnowledgeGraph) -> dict[str, set[str]]:
    artifact_ids = {f"graph:{graph.graph_id}", graph.graph_id}
    citation_ids: set[str] = set()
    for entity in graph.entities:
        artifact_ids.update(entity.source_artifact_ids)
    for relation in graph.relations:
        artifact_ids.update(relation.source_artifact_ids)
    for provenance in graph.provenance:
        if provenance.source_artifact_id:
            artifact_ids.add(provenance.source_artifact_id)
        if provenance.source_record_id:
            citation_ids.add(provenance.source_record_id)
        if provenance.source_url:
            citation_ids.add(provenance.source_url)
    return {
        "entity_ids": {entity.entity_id for entity in graph.entities},
        "relation_ids": {relation.relation_id for relation in graph.relations},
        "provenance_ids": {provenance.provenance_id for provenance in graph.provenance},
        "artifact_ids": artifact_ids,
        "citation_ids": citation_ids,
    }


def detect_hypothesis_guardrail_violations(text: str) -> list[str]:
    patterns = [
        (
            re.compile(r"\b(?:EvidenceItem|new evidence|create evidence)\b", re.I),
            "Hypothesis output must not create evidence.",
        ),
        (
            re.compile(r"\b(?:AssayResult|invented assay result|assay result rows?)\b", re.I),
            "Hypothesis output must not create assay results.",
        ),
        (
            re.compile(r"\b(?:new citation|fake citation|PMID:?\s*\d{4,9})\b", re.I),
            "Hypothesis output must not invent citations.",
        ),
        (
            re.compile(r"\b(?:new node|new edge|create_node|create_edge|suggested edge)\b", re.I),
            "Hypothesis output must not invent graph nodes or edges.",
        ),
        (
            re.compile(
                r"\b(?:protocol|synthesis route|reagent|concentration|temperature|"
                r"incubat(?:e|ion)|animal dosing|human dosing|dose|step-by-step|"
                r"\d+(?:\.\d+)?\s*(?:um|µm|mm|nm|mg/kg|hours?|hrs?|minutes?|mins?)|"
                r"\d+(?:\.\d+)?\s*(?:°?\s*c|celsius))\b",
                re.I,
            ),
            "Hypothesis output must not provide lab protocols, synthesis routes, reagents, "
            "concentrations, temperatures, incubation times, dosing, or step-by-step "
            "experimental instructions.",
        ),
        (
            re.compile(
                r"\b(?:cures|treats|is safe|are safe|binds|inhibits|activates|is active|"
                r"are active)\b",
                re.I,
            ),
            "Hypothesis output must not claim cure, treatment, safety, binding, inhibition, "
            "activation, or activity without exact evidence scope.",
        ),
        (
            re.compile(r"\b(?:medical advice|patient treatment|clinical guidance)\b", re.I),
            "Hypothesis output must not provide medical advice or patient treatment guidance.",
        ),
    ]
    warnings = []
    for pattern, message in patterns:
        if pattern.search(text) and message not in warnings:
            warnings.append(message)
    return warnings


def observed_hypothesis_references(
    text: str,
    output_json: dict[str, Any] | None = None,
) -> dict[str, set[str]]:
    observed = {
        "entity_ids": set(),
        "relation_ids": set(),
        "provenance_ids": set(),
        "artifact_ids": set(),
        "citation_ids": set(),
    }
    if output_json is not None:
        _collect_structured_refs(output_json, observed)
    for bucket, pattern in {
        "entity_ids": r"\b(?:disease|target|pathway|mechanism|molecule|generated_molecule|"
        r"scaffold|chemical_series|assay|assay_result|literature_paper|literature_claim|"
        r"evidence_item|developability_alert|structure|docking_pose|model_prediction|"
        r"review_decision|project|program|portfolio):[A-Za-z0-9_.:-]+\b",
        "relation_ids": r"\b(?:rel|relation):[A-Za-z0-9_.:-]+\b",
        "provenance_ids": r"\b(?:prov|provenance):[A-Za-z0-9_.:-]+\b",
        "artifact_ids": r"\b(?:artifact|graph|kg-artifact|model-artifact|assay-artifact|"
        r"portfolio-artifact|developability-artifact):[A-Za-z0-9_.:-]+\b",
        "citation_ids": r"\b(?:record|citation):[A-Za-z0-9_.:-]+\b",
    }.items():
        for match in re.finditer(pattern, text):
            observed[bucket].add(match.group(0))
    return observed


def _collect_structured_refs(value: Any, observed: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            bucket = _bucket(str(key).lower())
            if bucket is not None:
                observed[bucket].update(_string_values(item))
            _collect_structured_refs(item, observed)
        return
    if isinstance(value, list):
        for item in value:
            _collect_structured_refs(item, observed)


def _bucket(key: str) -> str | None:
    if key in {"entity_id", "entity_ids", "entities"}:
        return "entity_ids"
    if key in {"relation_id", "relation_ids", "relations"}:
        return "relation_ids"
    if key in {"provenance_id", "provenance_ids", "provenance"}:
        return "provenance_ids"
    if key in {"artifact_id", "artifact_ids", "artifact_refs", "artifacts"}:
        return "artifact_ids"
    if key in {"citation_id", "citation_ids", "citations"}:
        return "citation_ids"
    return None


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, int | float | bool):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    return [str(value)]
