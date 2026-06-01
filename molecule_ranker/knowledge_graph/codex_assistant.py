from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    detect_protocol_or_synthesis_text,
    redact_secrets,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.knowledge_graph.reasoning import analyze_cross_program_knowledge
from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph
from molecule_ranker.utils import slugify

GraphCodexTaskType = Literal[
    "explain_graph_patterns",
    "draft_graph_reuse_questions",
    "summarize_graph_contradictions",
    "explain_mechanism_hypothesis",
    "summarize_contradictions",
    "draft_graph_query_answer",
    "explain_cross_program_recommendation",
    "summarize_stale_decisions",
    "draft_mechanism_review_questions",
]


class GraphCodexProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class GraphCodexArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"codex-graph-{uuid4().hex[:16]}")
    graph_id: str
    task_type: GraphCodexTaskType
    status: str
    output_json: dict[str, Any] | None = None
    output_text: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexGraphAssistant:
    """Codex-backed graph assistant that reads graph context without mutating records."""

    def __init__(
        self, provider: GraphCodexProvider, *, working_directory: str | Path = "."
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory).resolve()

    def explain_graph_patterns(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self.explain_cross_program_recommendation(graph)

    def explain_mechanism_hypothesis(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "explain_mechanism_hypothesis",
            "Explain one or more supplied mechanism hypotheses using only graph context and "
            "explicitly cited graph records.",
        )

    def summarize_contradictions(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "summarize_contradictions",
            "Summarize graph-linked contradictions for expert review using only supplied graph "
            "records.",
        )

    def draft_graph_query_answer(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "draft_graph_query_answer",
            "Draft an answer to a graph query using only supplied graph entities, relations, "
            "provenance, and artifacts.",
        )

    def explain_cross_program_recommendation(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "explain_cross_program_recommendation",
            "Explain cross-program graph recommendations as advisory reuse context.",
        )

    def summarize_stale_decisions(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "summarize_stale_decisions",
            "Summarize stale graph-linked decisions and why they need review.",
        )

    def draft_mechanism_review_questions(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "draft_mechanism_review_questions",
            "Draft review questions for mechanism hypotheses using only supplied graph context.",
        )

    def draft_reuse_questions(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self.draft_mechanism_review_questions(graph)

    def summarize_graph_contradictions(self, graph: KnowledgeGraph) -> GraphCodexArtifact:
        return self._run(
            graph,
            "summarize_graph_contradictions",
            "Summarize graph-linked contradictions and unsupported hypotheses for review.",
        )

    def _run(
        self, graph: KnowledgeGraph, task_type: GraphCodexTaskType, prompt: str
    ) -> GraphCodexArtifact:
        context_path = self._write_context(graph, task_type)
        task = CodexTask(
            task_id=slugify(f"{graph.graph_id}-{task_type}-{uuid4().hex[:8]}"),
            task_type=task_type,  # type: ignore[arg-type]
            prompt=_graph_prompt(prompt),
            working_directory=str(self.working_directory),
            input_artifact_paths=[str(context_path)],
            allowed_commands=[],
            forbidden_commands=["git push", "rm -rf", "sudo", "curl |", "printenv", "cat .env"],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "graph_id": graph.graph_id,
                "graph_assistance_only": True,
                "cannot_create_evidence": True,
                "cannot_create_assay_results": True,
                "cannot_invent_graph_records": True,
                "cannot_invent_mechanisms": True,
                "cannot_change_confidence_scores": True,
                "cannot_remove_contradictions": True,
                "cannot_claim_causality": True,
            },
        )
        result = self.provider.run_task(task)
        return self._package(graph, task_type, task, result, context_path)

    def _write_context(self, graph: KnowledgeGraph, task_type: str) -> Path:
        context_dir = self.working_directory / ".knowledge_graph" / "codex_context"
        context_dir.mkdir(parents=True, exist_ok=True)
        path = context_dir / f"{slugify(graph.graph_id)}-{task_type}.json"
        payload = {
            "artifact_id": f"graph:{graph.graph_id}",
            "graph": graph.model_dump(mode="json"),
            "analysis": analyze_cross_program_knowledge(graph).model_dump(mode="json"),
            "boundaries": graph.limitations,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def _package(
        self,
        graph: KnowledgeGraph,
        task_type: GraphCodexTaskType,
        task: CodexTask,
        result: CodexTaskResult,
        context_path: Path,
    ) -> GraphCodexArtifact:
        output_text = redact_secrets(result.output_text or result.stdout)
        output_json = result.output_json
        raw = json.dumps(output_json or {}, sort_keys=True) + "\n" + output_text
        warnings = detect_graph_guardrail_violations(raw, graph=graph, output_json=output_json)
        guarded = check_output(
            result.model_copy(update={"output_text": output_text}),
            allowed_artifact_refs=_allowed_refs(graph, context_path),
            allowed_citation_ids=_allowed_citations(graph),
        )
        for warning in guarded.guardrail_warnings:
            if warning not in warnings:
                warnings.append(warning)
        if detect_protocol_or_synthesis_text(raw):
            for warning in detect_protocol_or_synthesis_text(raw):
                if warning not in warnings:
                    warnings.append(warning)
        status = "guardrail_failed" if warnings else guarded.status
        if status == "guardrail_failed":
            output_json = {
                "guardrail_failed": True,
                "message": (
                    "Codex graph output was withheld because it attempted to create records, "
                    "invent evidence/results, overclaim graph meaning, or include unsafe content."
                ),
                "artifact_refs": [f"graph:{graph.graph_id}", str(context_path)],
            }
            output_text = ""
        return GraphCodexArtifact(
            graph_id=graph.graph_id,
            task_type=task_type,
            status=status,
            output_json=output_json,
            output_text=output_text,
            artifact_refs=[f"graph:{graph.graph_id}", str(context_path)],
            guardrail_warnings=warnings,
            metadata={
                "codex_task_id": task.task_id,
                "codex_result_status": result.status,
                "graph_assistance_only": True,
            },
        )


def detect_graph_guardrail_violations(
    text: str,
    *,
    graph: KnowledgeGraph | None = None,
    output_json: dict[str, Any] | None = None,
) -> list[str]:
    warnings: list[str] = []
    patterns = [
        (
            re.compile(r"\bEvidenceItem\b"),
            "Codex graph output must not create EvidenceItem records.",
        ),
        (re.compile(r"\bAssayResult\b"), "Codex graph output must not create AssayResult records."),
        (
            re.compile(
                r"\b(?:new_nodes?|new_edges?|create_node|create_edge|suggested_edges?)\b", re.I
            ),
            "Codex graph output must not invent graph nodes or edges.",
        ),
        (
            re.compile(r"\bgraph path(?:s)?\s+(?:prove|proves|confirm|confirms)\b", re.I),
            "Graph paths must not be described as proof.",
        ),
        (
            re.compile(r"\bPMID:?\s*\d{4,9}\b", re.I),
            "Codex graph output must not invent citations.",
        ),
        (
            re.compile(
                r"\b(?:confidence[_ -]?score|confidence)\b.{0,40}"
                r"\b(?:changed|updated|set|increase|decrease|remove)\b",
                re.I | re.S,
            ),
            "Codex graph output must not change confidence scores.",
        ),
        (
            re.compile(r"\b(?:remove|delete|dismiss|discard)\b.{0,40}\bcontradictions?\b", re.I),
            "Codex graph output must not remove contradictions.",
        ),
        (
            re.compile(
                r"\b(?:graph paths?\s+(?:prove|confirm|show)|"
                r"[A-Z][A-Za-z0-9_.:-]*\s+(?:is|are)\s+(?:active|safe|efficacious)|"
                r"[A-Z][A-Za-z0-9_.:-]*\s+binds?)\b",
                re.I,
            ),
            "Codex graph output must not claim causality, activity, safety, efficacy, or binding.",
        ),
    ]
    for pattern, message in patterns:
        if pattern.search(text) and message not in warnings:
            warnings.append(message)
    if graph is not None:
        warnings.extend(_graph_reference_warnings(text, graph=graph, output_json=output_json))
    return warnings


def _graph_prompt(base_prompt: str) -> str:
    return (
        f"{base_prompt} Use only the supplied graph-context artifact. Return JSON only. "
        "Do not invent graph nodes, graph edges, citations, assay results, mechanisms, or "
        "EvidenceItem records. Graph-inferred relationships are hypotheses unless source-backed. "
        "Do not claim graph paths prove causality, efficacy, safety, binding, or activity. "
        "Do not create graph records, change confidence scores, remove contradictions, or provide "
        "medical advice, synthesis instructions, lab protocols, dosing, or patient guidance. "
        "Every graph explanation must include cited entity_ids, relation_ids, provenance_ids, and "
        "artifact_ids drawn exactly from the supplied context."
    )


def _allowed_refs(graph: KnowledgeGraph, context_path: Path) -> set[str]:
    refs = {
        str(context_path),
        context_path.name,
        f"graph:{graph.graph_id}",
        graph.graph_id,
        "entity_ids",
        "relation_ids",
        "provenance_ids",
        "artifact_ids",
        "mechanism_ids",
    }
    refs.update(entity.entity_id for entity in graph.entities)
    refs.update(relation.relation_id for relation in graph.relations)
    refs.update(provenance.provenance_id for provenance in graph.provenance)
    refs.update(
        artifact_id
        for entity in graph.entities
        for artifact_id in entity.source_artifact_ids
    )
    refs.update(
        artifact_id
        for relation in graph.relations
        for artifact_id in relation.source_artifact_ids
    )
    refs.update(
        provenance.source_artifact_id
        for provenance in graph.provenance
        if provenance.source_artifact_id
    )
    return refs


def _allowed_citations(graph: KnowledgeGraph) -> set[str]:
    refs: set[str] = set()
    for entity in graph.entities:
        for source in entity.created_from:
            if source.citation_ref:
                refs.add(source.citation_ref)
    for relation in graph.relations:
        for source in relation.provenance:
            if source.citation_ref:
                refs.add(source.citation_ref)
    return refs


def _graph_reference_warnings(
    text: str,
    *,
    graph: KnowledgeGraph,
    output_json: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    allowed = _allowed_graph_reference_sets(graph)
    observed = _observed_graph_references(text, output_json)
    required_labels = {
        "entity_ids": "entity IDs",
        "relation_ids": "relation IDs",
        "provenance_ids": "provenance IDs",
        "artifact_ids": "artifact IDs",
    }
    for key, label in required_labels.items():
        if not observed[key]:
            warnings.append(f"Codex graph output must cite {label}.")
    for key, label in required_labels.items():
        unknown = sorted(observed[key] - allowed[key])
        for value in unknown:
            warnings.append(f"Codex graph output cites unknown {label[:-1]}: {value}.")
    mechanism_ids = observed["mechanism_ids"]
    unknown_mechanisms = sorted(mechanism_ids - allowed["mechanism_ids"])
    for mechanism_id in unknown_mechanisms:
        warnings.append(f"Codex graph output cites invented mechanism ID: {mechanism_id}.")
    return warnings


def _allowed_graph_reference_sets(graph: KnowledgeGraph) -> dict[str, set[str]]:
    artifact_ids = {
        artifact_id
        for entity in graph.entities
        for artifact_id in entity.source_artifact_ids
    }
    artifact_ids.update(
        artifact_id
        for relation in graph.relations
        for artifact_id in relation.source_artifact_ids
    )
    artifact_ids.update(
        provenance.source_artifact_id
        for provenance in graph.provenance
        if provenance.source_artifact_id
    )
    return {
        "entity_ids": {entity.entity_id for entity in graph.entities},
        "relation_ids": {relation.relation_id for relation in graph.relations},
        "provenance_ids": {provenance.provenance_id for provenance in graph.provenance},
        "artifact_ids": artifact_ids | {f"graph:{graph.graph_id}", graph.graph_id},
        "mechanism_ids": {mechanism.mechanism_id for mechanism in graph.mechanisms},
    }


def _observed_graph_references(
    text: str,
    output_json: dict[str, Any] | None,
) -> dict[str, set[str]]:
    observed = {
        "entity_ids": set(),
        "relation_ids": set(),
        "provenance_ids": set(),
        "artifact_ids": set(),
        "mechanism_ids": set(),
    }
    if output_json is not None:
        _collect_structured_graph_refs(output_json, observed)
    _collect_textual_graph_refs(text, observed)
    return observed


def _collect_structured_graph_refs(value: Any, observed: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            target = _reference_bucket(normalized)
            if target is not None:
                for ref in _string_values(item):
                    observed[target].add(ref)
            _collect_structured_graph_refs(item, observed)
        return
    if isinstance(value, list):
        for item in value:
            _collect_structured_graph_refs(item, observed)


def _collect_textual_graph_refs(text: str, observed: dict[str, set[str]]) -> None:
    for bucket, pattern in {
        "entity_ids": r"\b(?:disease|target|pathway|mechanism|molecule|generated_molecule|"
        r"scaffold|chemical_series|assay|assay_result|literature_paper|literature_claim|"
        r"evidence_item|developability_alert|structure|docking_pose|model_prediction|"
        r"review_decision|project|program|portfolio|codex_summary):[A-Za-z0-9_.:-]+\b",
        "relation_ids": r"\b(?:rel|relation|contradicts|stale_due_to):[A-Za-z0-9_.:-]+\b",
        "provenance_ids": r"\b(?:prov|provenance):[A-Za-z0-9_.:-]+\b",
        "artifact_ids": r"\b(?:artifact|graph|kg-artifact|model-artifact|assay-artifact|"
        r"portfolio-artifact|developability-artifact):[A-Za-z0-9_.:-]+\b",
        "mechanism_ids": r"\bmechanism:[A-Za-z0-9_.:-]+\b",
    }.items():
        for match in re.finditer(pattern, text):
            observed[bucket].add(match.group(0))


def _reference_bucket(key: str) -> str | None:
    if key in {"entity_id", "entity_ids", "entities"}:
        return "entity_ids"
    if key in {"relation_id", "relation_ids", "relations", "edge_id", "edge_ids"}:
        return "relation_ids"
    if key in {"provenance_id", "provenance_ids", "provenance"}:
        return "provenance_ids"
    if key in {"artifact_id", "artifact_ids", "artifact_refs", "artifacts"}:
        return "artifact_ids"
    if key in {"mechanism_id", "mechanism_ids"}:
        return "mechanism_ids"
    return None


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, int | float | bool):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    return [str(value)]
