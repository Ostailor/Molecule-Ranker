from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from molecule_ranker.codex_backbone.guardrails import check_output, redact_secrets
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph
from molecule_ranker.utils import slugify

from .engine import HypothesisGenerationEngine
from .schemas import HypothesisCodexArtifact
from .validation import (
    allowed_hypothesis_reference_sets,
    detect_hypothesis_guardrail_violations,
    observed_hypothesis_references,
)


class HypothesisCodexProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class CodexHypothesisAssistant:
    """Codex-backed hypothesis assistant that drafts text without mutating graph records."""

    def __init__(
        self,
        provider: HypothesisCodexProvider,
        *,
        working_directory: str | Path = ".",
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory).resolve()

    def draft_hypotheses(self, graph: KnowledgeGraph) -> HypothesisCodexArtifact:
        return self._run(
            graph,
            "draft_hypotheses",
            "Draft reviewable hypothesis language from the supplied graph-backed hypothesis set.",
        )

    def draft_research_questions(self, graph: KnowledgeGraph) -> HypothesisCodexArtifact:
        return self._run(
            graph,
            "draft_research_questions",
            "Draft high-level research questions from the supplied graph-backed hypothesis set.",
        )

    def _run(
        self,
        graph: KnowledgeGraph,
        task_type: str,
        prompt: str,
    ) -> HypothesisCodexArtifact:
        context_path = self._write_context(graph, task_type)
        task = CodexTask(
            task_id=slugify(f"{graph.graph_id}-{task_type}-{uuid4().hex[:8]}"),
            task_type=task_type,  # type: ignore[arg-type]
            prompt=_hypothesis_prompt(prompt),
            working_directory=str(self.working_directory),
            input_artifact_paths=[str(context_path)],
            allowed_commands=[],
            forbidden_commands=["git push", "rm -rf", "sudo", "curl |", "printenv", "cat .env"],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "graph_id": graph.graph_id,
                "hypothesis_assistance_only": True,
                "must_validate_graph_references": True,
                "cannot_create_evidence": True,
                "cannot_create_assay_results": True,
                "cannot_invent_citations": True,
                "cannot_invent_graph_records": True,
                "cannot_invent_hypotheses_without_validation": True,
                "cannot_provide_lab_protocols": True,
                "cannot_provide_synthesis_routes": True,
                "cannot_provide_medical_advice": True,
            },
        )
        result = self.provider.run_task(task)
        return self._package(graph, task_type, result, context_path)

    def _write_context(self, graph: KnowledgeGraph, task_type: str) -> Path:
        context_dir = self.working_directory / ".hypotheses" / "codex_context"
        context_dir.mkdir(parents=True, exist_ok=True)
        path = context_dir / f"{slugify(graph.graph_id)}-{task_type}.json"
        hypothesis_set = HypothesisGenerationEngine(graph).generate()
        payload = {
            "artifact_id": f"hypotheses:{graph.graph_id}",
            "graph_id": graph.graph_id,
            "hypotheses": hypothesis_set.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
            "boundaries": hypothesis_set.limitations,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def _package(
        self,
        graph: KnowledgeGraph,
        task_type: str,
        result: CodexTaskResult,
        context_path: Path,
    ) -> HypothesisCodexArtifact:
        output_text = redact_secrets(result.output_text or result.stdout)
        output_json = result.output_json
        raw = json.dumps(output_json or {}, sort_keys=True) + "\n" + output_text
        warnings = detect_hypothesis_guardrail_violations(raw)
        warnings.extend(_reference_warnings(raw, graph, output_json))
        guarded = check_output(
            result.model_copy(update={"output_text": output_text}),
            allowed_artifact_refs=_allowed_refs(graph, context_path),
            allowed_citation_ids=allowed_hypothesis_reference_sets(graph)["citation_ids"],
        )
        for warning in guarded.guardrail_warnings:
            if warning not in warnings:
                warnings.append(warning)
        status = "guardrail_failed" if warnings else guarded.status
        if status == "guardrail_failed":
            output_json = {
                "guardrail_failed": True,
                "message": (
                    "Codex hypothesis output was withheld because it attempted to invent "
                    "records, citations, evidence, results, unsafe details, or unsupported "
                    "claims."
                ),
                "artifact_refs": [f"hypotheses:{graph.graph_id}", str(context_path)],
            }
            output_text = ""
        return HypothesisCodexArtifact(
            graph_id=graph.graph_id,
            task_type=task_type,
            status=status,
            output_json=output_json,
            output_text=output_text,
            artifact_refs=[f"hypotheses:{graph.graph_id}", str(context_path)],
            guardrail_warnings=warnings,
            metadata={
                "codex_result_status": result.status,
                "hypothesis_assistance_only": True,
            },
        )


def _hypothesis_prompt(base_prompt: str) -> str:
    return (
        f"{base_prompt} Use only the supplied graph and hypothesis context. Return JSON only. "
        "A hypothesis is not evidence. A research question is not a lab protocol. A validation "
        "plan is not an experimental procedure. Do not invent graph nodes, graph edges, "
        "citations, assay results, mechanisms, or hypotheses. Do not provide synthesis routes, "
        "reagents, concentrations, temperatures, incubation times, dosing, step-by-step "
        "experimental instructions, medical advice, or patient guidance. Every referenced "
        "entity_id, relation_id, provenance_id, artifact_id, and citation_id must be copied "
        "exactly from the supplied context."
    )


def _reference_warnings(
    text: str,
    graph: KnowledgeGraph,
    output_json: dict[str, Any] | None,
) -> list[str]:
    allowed = allowed_hypothesis_reference_sets(graph)
    observed = observed_hypothesis_references(text, output_json)
    warnings = []
    labels = {
        "entity_ids": "entity ID",
        "relation_ids": "relation ID",
        "provenance_ids": "provenance ID",
        "artifact_ids": "artifact ID",
        "citation_ids": "citation ID",
    }
    for bucket, label in labels.items():
        for value in sorted(observed[bucket] - allowed[bucket]):
            warnings.append(f"Codex hypothesis output cites unknown {label}: {value}.")
    return warnings


def _allowed_refs(graph: KnowledgeGraph, context_path: Path) -> set[str]:
    refs = {str(context_path), context_path.name, f"hypotheses:{graph.graph_id}"}
    for values in allowed_hypothesis_reference_sets(graph).values():
        refs.update(values)
    return refs
