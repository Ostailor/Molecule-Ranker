from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.parser import parse_codex_json
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.hypotheses.validation import (
    detect_hypothesis_guardrail_violations,
    observed_hypothesis_references,
)
from molecule_ranker.utils import slugify

CodexHypothesisDraftTask = Literal[
    "draft_hypothesis_statement",
    "explain_hypothesis_evidence",
    "draft_research_questions",
    "summarize_evidence_gaps",
    "draft_falsification_criteria",
    "explain_contradiction_hypothesis",
    "draft_hypothesis_review_questions",
    "draft_limitations",
    "draft_review_questions",
]
DraftStatus = Literal["accepted", "fallback"]


class CodexDraftingProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class CodexHypothesisDraft(BaseModel):
    hypothesis_id: str
    task_type: CodexHypothesisDraftTask
    status: DraftStatus
    used_fallback: bool
    statement: str = ""
    explanation: str = ""
    evidence_gap_summary: str = ""
    contradiction_explanation: str = ""
    research_questions: list[str] = Field(default_factory=list)
    falsification_criteria: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    review_questions: list[str] = Field(default_factory=list)
    output_json: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexHypothesisDrafter:
    """Candidate-level Codex wording assistant with deterministic validation."""

    def __init__(
        self,
        provider: CodexDraftingProvider,
        *,
        working_directory: str | Path = ".",
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory).resolve()

    def draft_hypothesis_statement(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_hypothesis_statement", **context)

    def explain_hypothesis_evidence(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "explain_hypothesis_evidence", **context)

    def draft_research_questions(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_research_questions", **context)

    def summarize_evidence_gaps(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "summarize_evidence_gaps", **context)

    def draft_falsification_criteria(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_falsification_criteria", **context)

    def explain_contradiction_hypothesis(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "explain_contradiction_hypothesis", **context)

    def draft_hypothesis_review_questions(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_hypothesis_review_questions", **context)

    def draft_limitations(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_limitations", **context)

    def draft_review_questions(
        self,
        candidate: ResearchHypothesis,
        **context: Any,
    ) -> CodexHypothesisDraft:
        return self._draft(candidate, "draft_review_questions", **context)

    def _draft(
        self,
        candidate: ResearchHypothesis,
        task_type: CodexHypothesisDraftTask,
        *,
        graph_paths: Iterable[Any] | None = None,
        evidence_summaries: Iterable[str] | None = None,
        contradiction_summaries: Iterable[str] | None = None,
        allowed_entity_ids: Iterable[str] | None = None,
        allowed_relation_ids: Iterable[str] | None = None,
        allowed_provenance_ids: Iterable[str] | None = None,
        allowed_artifact_ids: Iterable[str] | None = None,
        allowed_assay_result_ids: Iterable[str] | None = None,
    ) -> CodexHypothesisDraft:
        allowed = _allowed_refs(
            candidate,
            allowed_entity_ids=allowed_entity_ids,
            allowed_relation_ids=allowed_relation_ids,
            allowed_provenance_ids=allowed_provenance_ids,
            allowed_artifact_ids=allowed_artifact_ids,
            allowed_assay_result_ids=allowed_assay_result_ids,
        )
        task = self._task(
            candidate,
            task_type,
            graph_paths=list(graph_paths or []),
            evidence_summaries=list(evidence_summaries or []),
            contradiction_summaries=list(contradiction_summaries or []),
            allowed=allowed,
        )
        result = self.provider.run_task(task)
        payload, warnings = _parse_result_json(result)
        if payload is not None:
            warnings.extend(_validate_payload(payload, allowed))
        if result.status != "succeeded":
            warnings.append(f"Codex task did not succeed: {result.status}.")
        if payload is None or warnings:
            return _fallback(candidate, task_type, warnings, result)
        return _accepted(candidate, task_type, payload, result)

    def _task(
        self,
        candidate: ResearchHypothesis,
        task_type: CodexHypothesisDraftTask,
        *,
        graph_paths: list[Any],
        evidence_summaries: list[str],
        contradiction_summaries: list[str],
        allowed: dict[str, list[str]],
    ) -> CodexTask:
        context = {
            "deterministic_hypothesis_candidate": candidate.model_dump(mode="json"),
            "graph_paths": graph_paths,
            "evidence_summaries": evidence_summaries,
            "contradiction_summaries": contradiction_summaries,
            "allowed_entity_ids": allowed["entity_ids"],
            "allowed_relation_ids": allowed["relation_ids"],
            "allowed_provenance_ids": allowed["provenance_ids"],
            "allowed_artifact_ids": allowed["artifact_ids"],
            "allowed_assay_result_ids": allowed["assay_result_ids"],
            "required_json_shape": _required_json_shape(task_type),
            "boundaries": [
                "A hypothesis is not evidence.",
                "A research question is not a lab protocol.",
                "A validation plan is not an experimental procedure.",
                "Use Codex CLI for hypothesis explanation and wording only.",
                "Do not add entities, relations, citations, assay results, or mechanisms.",
                "Do not provide protocols, synthesis instructions, dosing, or medical advice.",
                "Do not claim causality, activity, safety, binding, inhibition, or activation.",
                "Do not approve hypotheses or change review status.",
            ],
        }
        prompt = (
            f"Task: {task_type}. Explain or improve wording only from the supplied JSON "
            "context. Return JSON only. Cite the hypothesis_id plus allowed entity IDs, "
            "relation IDs, provenance IDs, and artifact IDs. "
            "Do not create unsupported hypotheses, approve hypotheses, or add citations, "
            "assay results, graph records, mechanisms, protocols, "
            "synthesis instructions, dosing, causality claims, activity claims, "
            "or safety claims.\n\n"
            f"{json.dumps(context, indent=2, sort_keys=True)}"
        )
        return CodexTask(
            task_id=slugify(f"{candidate.hypothesis_id}-{task_type}-{uuid4().hex[:8]}"),
            task_type=task_type,
            prompt=prompt,
            working_directory=str(self.working_directory),
            input_artifact_paths=[],
            allowed_commands=[],
            forbidden_commands=["git push", "rm -rf", "sudo", "curl |", "printenv", "cat .env"],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "hypothesis_id": candidate.hypothesis_id,
                "hypothesis_drafting_only": True,
                "hypothesis_explanation_only": True,
                "must_validate_references": True,
                "fallback_on_validation_failure": True,
                "allowed_entity_ids": allowed["entity_ids"],
                "allowed_relation_ids": allowed["relation_ids"],
                "allowed_provenance_ids": allowed["provenance_ids"],
                "allowed_artifact_ids": allowed["artifact_ids"],
                "allowed_assay_result_ids": allowed["assay_result_ids"],
            },
        )


def _parse_result_json(result: CodexTaskResult) -> tuple[dict[str, Any] | None, list[str]]:
    if result.output_json is not None:
        return result.output_json, []
    text = result.output_text or result.stdout
    try:
        return parse_codex_json(text), []
    except ValueError as exc:
        return None, [f"Codex output must be JSON: {exc}"]


def _validate_payload(payload: dict[str, Any], allowed: dict[str, list[str]]) -> list[str]:
    raw_text = json.dumps(payload, sort_keys=True)
    warnings = detect_hypothesis_guardrail_violations(raw_text)
    observed = observed_hypothesis_references(raw_text, payload)
    allowed_sets = {key: set(value) for key, value in allowed.items()}
    hypothesis_id = payload.get("hypothesis_id")
    if hypothesis_id != allowed["hypothesis_id"][0]:
        warnings.append("Codex output must cite the exact hypothesis_id.")
    if _contains_forbidden_creation_or_approval(payload):
        warnings.append(
            "Codex hypothesis output may not create hypotheses or approve hypotheses."
        )
    for required_key in ("entity_ids", "relation_ids", "provenance_ids", "artifact_ids"):
        values = _string_list(payload.get(required_key))
        if not values:
            warnings.append(f"Codex output must cite {required_key}.")
    for value in sorted(observed["entity_ids"] - allowed_sets["entity_ids"]):
        warnings.append(f"unknown entity ID: {value}")
    for value in sorted(observed["relation_ids"] - allowed_sets["relation_ids"]):
        warnings.append(f"unknown relation ID: {value}")
    for value in sorted(observed["provenance_ids"] - allowed_sets["provenance_ids"]):
        warnings.append(f"unknown provenance ID: {value}")
    for value in sorted(observed["artifact_ids"] - allowed_sets["artifact_ids"]):
        warnings.append(f"unknown artifact ID: {value}")
    for value in sorted(_structured_ids(payload, {"assay_result_id", "assay_result_ids"})):
        if value not in allowed_sets["assay_result_ids"]:
            warnings.append(f"unknown assay result ID: {value}")
    if observed["citation_ids"]:
        warnings.append("Codex draft must not add citation IDs.")
    return _unique(warnings)


def _accepted(
    candidate: ResearchHypothesis,
    task_type: CodexHypothesisDraftTask,
    payload: dict[str, Any],
    result: CodexTaskResult,
) -> CodexHypothesisDraft:
    return CodexHypothesisDraft(
        hypothesis_id=candidate.hypothesis_id,
        task_type=task_type,
        status="accepted",
        used_fallback=False,
        statement=_string(payload.get("statement")),
        explanation=_string(payload.get("explanation")),
        evidence_gap_summary=_string(payload.get("evidence_gap_summary")),
        contradiction_explanation=_string(payload.get("contradiction_explanation")),
        research_questions=_string_list(
            payload.get("questions") or payload.get("research_questions")
        ),
        falsification_criteria=_string_list(
            payload.get("criteria") or payload.get("falsification_criteria")
        ),
        limitations=_string_list(payload.get("limitations")),
        review_questions=_string_list(payload.get("review_questions")),
        output_json=payload,
        warnings=[],
        metadata={"codex_result_status": result.status},
    )


def _fallback(
    candidate: ResearchHypothesis,
    task_type: CodexHypothesisDraftTask,
    warnings: list[str],
    result: CodexTaskResult,
) -> CodexHypothesisDraft:
    return CodexHypothesisDraft(
        hypothesis_id=candidate.hypothesis_id,
        task_type=task_type,
        status="fallback",
        used_fallback=True,
        statement=candidate.statement,
        explanation=(
            "Deterministic fallback: the hypothesis remains a graph-backed planning "
            "object and is not evidence."
        ),
        evidence_gap_summary=(
            "Deterministic fallback: evidence gaps should be reviewed at a high level."
        ),
        contradiction_explanation=(
            "Deterministic fallback: contradiction hypotheses require scoped review."
        ),
        research_questions=[
            "What high-level graph-backed evidence would reduce uncertainty for this hypothesis?"
        ],
        falsification_criteria=[
            "A source-backed contradiction to the referenced graph relations would lower priority."
        ],
        limitations=[*candidate.limitations, *candidate.warnings],
        review_questions=[
            "Are all referenced entities, relations, and artifacts present in the graph?"
        ],
        output_json={},
        warnings=_unique(warnings),
        metadata={"codex_result_status": result.status},
    )


def _allowed_refs(
    candidate: ResearchHypothesis,
    *,
    allowed_entity_ids: Iterable[str] | None,
    allowed_relation_ids: Iterable[str] | None,
    allowed_provenance_ids: Iterable[str] | None,
    allowed_artifact_ids: Iterable[str] | None,
    allowed_assay_result_ids: Iterable[str] | None,
) -> dict[str, list[str]]:
    entity_ids = set(allowed_entity_ids or [])
    relation_ids = set(allowed_relation_ids or [])
    provenance_ids = set(allowed_provenance_ids or [])
    artifact_ids = set(allowed_artifact_ids or [])
    assay_result_ids = set(allowed_assay_result_ids or [])
    entity_ids.update(candidate.disease_entity_ids)
    entity_ids.update(candidate.target_entity_ids)
    entity_ids.update(candidate.molecule_entity_ids)
    entity_ids.update(candidate.generated_molecule_entity_ids)
    entity_ids.update(candidate.scaffold_entity_ids)
    entity_ids.update(candidate.mechanism_entity_ids)
    relation_ids.update(candidate.supporting_relation_ids)
    relation_ids.update(candidate.contradicting_relation_ids)
    artifact_ids.update(candidate.source_artifact_ids)
    assay_result_ids.update(candidate.assay_result_ids)
    return {
        "hypothesis_id": [candidate.hypothesis_id],
        "entity_ids": sorted(entity_ids),
        "relation_ids": sorted(relation_ids),
        "provenance_ids": sorted(provenance_ids),
        "artifact_ids": sorted(artifact_ids),
        "assay_result_ids": sorted(assay_result_ids),
    }


def _required_json_shape(task_type: CodexHypothesisDraftTask) -> dict[str, Any]:
    common: dict[str, Any] = {
        "hypothesis_id": "copy the supplied hypothesis_id",
        "entity_ids": ["copy allowed IDs only"],
        "relation_ids": ["copy allowed IDs only"],
        "provenance_ids": ["copy allowed IDs only"],
        "artifact_ids": ["copy allowed IDs only"],
    }
    if task_type == "draft_hypothesis_statement":
        return {"statement": "concise hypothesis wording", **common}
    if task_type == "explain_hypothesis_evidence":
        return {"explanation": "concise graph-backed evidence explanation", **common}
    if task_type == "draft_research_questions":
        return {"questions": ["high-level research question"], **common}
    if task_type == "summarize_evidence_gaps":
        return {"evidence_gap_summary": "high-level evidence-gap summary", **common}
    if task_type == "draft_falsification_criteria":
        return {"criteria": ["high-level falsification criterion"], **common}
    if task_type == "explain_contradiction_hypothesis":
        return {
            "contradiction_explanation": "high-level contradiction explanation",
            **common,
        }
    if task_type == "draft_limitations":
        return {"limitations": ["limitation"], **common}
    return {"review_questions": ["expert review question"], **common}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_values.append(value)
    return unique_values


def _structured_ids(payload: dict[str, Any], keys: set[str]) -> set[str]:
    values: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) in keys:
                    values.update(_string_list(item))
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return values


def _contains_forbidden_creation_or_approval(payload: dict[str, Any]) -> bool:
    forbidden_keys = {
        "hypotheses",
        "new_hypothesis",
        "new_hypotheses",
        "created_hypotheses",
        "review_decision",
        "review_decisions",
        "decision",
        "status",
    }
    approval_values = {"accept_for_planning", "accepted_for_planning", "approved", "approve"}

    def check(value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).lower()
                if normalized_key in forbidden_keys:
                    return True
                if check(item):
                    return True
        elif isinstance(value, list):
            return any(check(item) for item in value)
        elif isinstance(value, str):
            normalized = value.lower().strip()
            return normalized in approval_values
        return False

    return check(payload)
