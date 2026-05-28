from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.codex import create_llm_provider
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.schemas import Disease, MoleculeCandidate, Target


class DesignPlanValidationError(ValueError):
    """Raised when a Codex-proposed scientific design plan fails validation."""


class DesignPlan(BaseModel):
    design_plan_id: str
    disease_name: str
    target_priorities: list[dict[str, Any]] = Field(default_factory=list)
    design_objectives: list[dict[str, Any]] = Field(default_factory=list)
    seed_strategy: dict[str, Any] = Field(default_factory=dict)
    generator_strategy: dict[str, Any] = Field(default_factory=dict)
    oracle_strategy: dict[str, Any] = Field(default_factory=dict)
    diversity_strategy: dict[str, Any] = Field(default_factory=dict)
    uncertainty_strategy: dict[str, Any] = Field(default_factory=dict)
    experiment_readiness_strategy: dict[str, Any] = Field(default_factory=dict)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    required_followups: list[dict[str, Any]] = Field(default_factory=list)
    codex_task_result_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("design_plan_id", "disease_name", "codex_task_result_id")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class ScientificDesignPlannerProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult:
        """Run a bounded Codex planning task."""
        ...


UNSAFE_PLAN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blab\s+protocols?\b", re.I), "lab protocol"),
    (re.compile(r"\bprotocol\b", re.I), "protocol"),
    (re.compile(r"\bsynthesis\b", re.I), "synthesis"),
    (re.compile(r"\breaction\s+conditions?\b", re.I), "reaction conditions"),
    (re.compile(r"\breagents?\b", re.I), "reagents"),
    (re.compile(r"\bdos(e|ing)\b", re.I), "dosing"),
    (re.compile(r"\banimal\s+study\b", re.I), "animal study"),
    (re.compile(r"\bpatient\b", re.I), "patient guidance"),
)


class ScientificDesignPlannerAgent:
    """Use Codex CLI to propose a bounded, source-grounded V1.1 design plan."""

    name = "ScientificDesignPlannerAgent"

    def __init__(
        self,
        provider: ScientificDesignPlannerProvider | None = None,
        *,
        working_directory: str | Path = ".",
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory)

    def build_plan(
        self,
        *,
        disease: Disease,
        targets: list[Target],
        existing_candidates: list[MoleculeCandidate],
        literature_evidence: Any,
        developability_assessments: Sequence[Any],
        experimental_results: Sequence[Any],
        review_decisions: Sequence[Any],
        active_learning_history: Sequence[Any],
        artifact_manifests: Sequence[Any],
    ) -> DesignPlan:
        context = _ValidationContext.from_inputs(
            disease=disease,
            targets=targets,
            existing_candidates=existing_candidates,
            literature_evidence=literature_evidence,
            developability_assessments=developability_assessments,
            experimental_results=experimental_results,
            review_decisions=review_decisions,
            active_learning_history=active_learning_history,
            artifact_manifests=artifact_manifests,
        )
        task = self._build_task(context)
        provider = self.provider or create_llm_provider(
            {
                "enable_codex_backbone": True,
                "codex_working_dir": self.working_directory,
                "codex_require_json": True,
                "codex_allow_shell_commands": False,
            }
        )
        result = provider.run_task(task)
        plan = self._plan_from_result(result)
        return self._validate_plan(plan, context)

    def _build_task(self, context: _ValidationContext) -> CodexTask:
        prompt = {
            "task": "scientific_design_plan",
            "instructions": [
                "Create a bounded generated-molecule design plan from existing artifacts only.",
                "Return JSON matching the DesignPlan schema.",
                "Do not invent targets, molecules, citations, assay results, or evidence.",
                "Do not include synthesis instructions, lab protocols, dosing, "
                "or patient guidance.",
                "Design objectives must be machine-readable objects.",
            ],
            "allowed_references": {
                "disease_name": context.disease_name,
                "target_symbols": sorted(context.target_symbols),
                "candidate_names": sorted(context.candidate_names),
                "evidence_refs": sorted(context.evidence_refs),
                "artifact_refs": sorted(context.artifact_refs),
            },
            "source_context": context.prompt_payload,
        }
        return CodexTask(
            task_id="codex-scientific-design-plan",
            task_type="plan_followup_run",
            prompt=json.dumps(prompt, indent=2, sort_keys=True),
            working_directory=str(self.working_directory),
            input_artifact_paths=sorted(context.artifact_refs),
            allowed_commands=[],
            forbidden_commands=[],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "planning_mode": "scientific_design_v1_1",
                "no_shell_or_tool_nodes": True,
            },
        )

    def _plan_from_result(self, result: CodexTaskResult) -> DesignPlan:
        if result.status != "succeeded":
            raise DesignPlanValidationError(
                f"Codex design planning failed guardrails or execution: {result.status}."
            )
        if result.output_json is None:
            raise DesignPlanValidationError("Codex design planning did not return JSON.")
        payload = dict(result.output_json)
        payload.setdefault("codex_task_result_id", result.task_id)
        try:
            return DesignPlan(**payload)
        except Exception as exc:
            raise DesignPlanValidationError(
                f"Codex design plan schema validation failed: {exc}"
            ) from exc

    def _validate_plan(self, plan: DesignPlan, context: _ValidationContext) -> DesignPlan:
        if plan.disease_name != context.disease_name:
            raise DesignPlanValidationError(
                f"Design plan disease {plan.disease_name!r} does not match source disease."
            )
        serialized = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
        for pattern, label in UNSAFE_PLAN_PATTERNS:
            if pattern.search(serialized):
                raise DesignPlanValidationError(
                    f"Design plan contains unsafe {label} content."
                )
        for target in _referenced_targets(plan):
            if target not in context.target_symbols:
                raise DesignPlanValidationError(f"Design plan references unknown target: {target}.")
        for candidate in _referenced_candidates(plan):
            if candidate not in context.candidate_names:
                raise DesignPlanValidationError(
                    f"Design plan references unknown molecule/candidate: {candidate}."
                )
        for evidence_ref in _referenced_evidence(plan):
            if evidence_ref not in context.evidence_refs:
                raise DesignPlanValidationError(
                    f"Design plan references unknown evidence/citation: {evidence_ref}."
                )
        if not plan.design_objectives:
            raise DesignPlanValidationError("Design plan must include design_objectives.")
        for objective in plan.design_objectives:
            if not isinstance(objective, dict):
                raise DesignPlanValidationError(
                    "Design objectives must be machine-readable objects."
                )
            for key in ("objective_id", "target_symbol", "objective_type", "constraints"):
                if key not in objective:
                    raise DesignPlanValidationError(
                        f"Design objective is missing machine-readable field: {key}."
                    )
            if not isinstance(objective.get("constraints"), dict):
                raise DesignPlanValidationError("Design objective constraints must be an object.")

        metadata = {
            **plan.metadata,
            "deterministic_validation": {
                "approved": True,
                "validated_at": datetime.now(UTC).isoformat(),
                "target_count": len(context.target_symbols),
                "candidate_count": len(context.candidate_names),
                "evidence_ref_count": len(context.evidence_refs),
            },
        }
        return plan.model_copy(update={"metadata": metadata})


class _ValidationContext(BaseModel):
    disease_name: str
    target_symbols: set[str]
    candidate_names: set[str]
    evidence_refs: set[str]
    artifact_refs: set[str]
    prompt_payload: dict[str, Any]

    @classmethod
    def from_inputs(
        cls,
        *,
        disease: Disease,
        targets: list[Target],
        existing_candidates: list[MoleculeCandidate],
        literature_evidence: Any,
        developability_assessments: Sequence[Any],
        experimental_results: Sequence[Any],
        review_decisions: Sequence[Any],
        active_learning_history: Sequence[Any],
        artifact_manifests: Sequence[Any],
    ) -> _ValidationContext:
        prompt_payload = {
            "disease": disease.model_dump(mode="json"),
            "targets": [target.model_dump(mode="json") for target in targets],
            "existing_candidates": [
                candidate.model_dump(mode="json") for candidate in existing_candidates
            ],
            "literature_evidence": _jsonable(literature_evidence),
            "developability_assessments": [
                _jsonable(item) for item in developability_assessments
            ],
            "experimental_results": [_jsonable(item) for item in experimental_results],
            "review_decisions": [_jsonable(item) for item in review_decisions],
            "active_learning_history": [_jsonable(item) for item in active_learning_history],
            "artifact_manifests": [_jsonable(item) for item in artifact_manifests],
        }
        evidence_refs = set()
        for target in targets:
            evidence_refs.update(
                str(item.source_record_id) for item in target.evidence if item.source_record_id
            )
        for candidate in existing_candidates:
            evidence_refs.update(
                str(item.source_record_id)
                for item in candidate.evidence
                if item.source_record_id
            )
        evidence_refs.update(_collect_reference_values(literature_evidence))
        evidence_refs.update(_collect_reference_values(experimental_results))
        evidence_refs.update(_collect_reference_values(review_decisions))
        evidence_refs.update(_collect_reference_values(active_learning_history))

        artifact_refs = _artifact_refs(artifact_manifests)
        return cls(
            disease_name=disease.canonical_name,
            target_symbols={target.symbol for target in targets},
            candidate_names={candidate.name for candidate in existing_candidates},
            evidence_refs=evidence_refs,
            artifact_refs=artifact_refs,
            prompt_payload=prompt_payload,
        )


def _referenced_targets(plan: DesignPlan) -> set[str]:
    values = set()
    for item in [*plan.target_priorities, *plan.design_objectives, *plan.required_followups]:
        if isinstance(item, dict):
            value = item.get("target_symbol") or item.get("target")
            if value not in (None, ""):
                values.add(str(value))
    return values


def _referenced_candidates(plan: DesignPlan) -> set[str]:
    values = set()
    for item in _walk(plan.model_dump(mode="json")):
        if not isinstance(item, dict):
            continue
        for key in ("candidate_name", "molecule_name", "seed_candidate_name"):
            value = item.get(key)
            if value not in (None, ""):
                values.add(str(value))
        for key in ("candidate_names", "molecule_names", "seed_candidate_names"):
            raw = item.get(key)
            if isinstance(raw, list):
                values.update(str(value) for value in raw if value not in (None, ""))
    return values


def _referenced_evidence(plan: DesignPlan) -> set[str]:
    values = set()
    for item in _walk(plan.model_dump(mode="json")):
        if not isinstance(item, dict):
            continue
        for key in ("evidence_ref", "source_record_id", "citation", "citation_id", "pmid", "doi"):
            value = item.get(key)
            if value not in (None, ""):
                values.add(str(value))
        raw = item.get("evidence_refs") or item.get("citations")
        if isinstance(raw, list):
            values.update(str(value) for value in raw if value not in (None, ""))
    return values


def _artifact_refs(artifact_manifests: Sequence[Any]) -> set[str]:
    refs = set()
    for item in _walk(_jsonable(artifact_manifests)):
        if not isinstance(item, dict):
            continue
        value = item.get("path") or item.get("artifact_path")
        if value not in (None, ""):
            refs.add(str(value))
        artifact_id = item.get("artifact_id")
        if artifact_id not in (None, ""):
            refs.add(str(artifact_id))
    return refs


def _collect_reference_values(value: Any) -> set[str]:
    refs = set()
    for item in _walk(_jsonable(value)):
        if not isinstance(item, dict):
            continue
        for key in (
            "source_record_id",
            "record_id",
            "result_id",
            "evidence_id",
            "citation_id",
            "pmid",
            "doi",
        ):
            raw = item.get(key)
            if raw not in (None, ""):
                refs.add(str(raw))
    return refs


def _walk(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, Mapping):
        for child in value.values():
            items.extend(_walk(child))
    elif isinstance(value, list | tuple):
        for child in value:
            items.extend(_walk(child))
    return items


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(child) for child in value]
    return value
