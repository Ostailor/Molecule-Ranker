from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.approvals import RuntimeApprovalController
from molecule_ranker.runtime_agents.schemas import (
    AutonomyLevel,
    RuntimeActionStep,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

COMMON_RUNTIME_FAILURE_TYPES: tuple[str, ...] = (
    "disease_resolution_failure",
    "no_candidates_found",
    "external_api_unavailable",
    "literature_unavailable",
    "generation_no_valid_molecules",
    "developability_failed",
    "assay_import_validation_failed",
    "graph_build_failed",
    "model_unavailable",
    "codex_output_parse_failed",
    "guardrail_failure",
    "job_timed_out",
    "permission_denied",
)

NON_FABRICATION_GUARDRAILS = [
    "Never bypass deterministic validators.",
    "Never fabricate missing data.",
    "Recovery output is operational context, not biomedical evidence.",
]

ToolHandler = Callable[[RuntimeActionStep, RuntimeToolSpec], RuntimeToolResult | dict[str, Any]]


class RuntimeRecoverySuggestion(BaseModel):
    failure_type: str
    diagnosis: str
    safe_next_actions: list[str] = Field(default_factory=list)
    approval_required: bool = False
    approval_reason: str | None = None
    recovery_tool_name: str | None = None
    recovery_tool_args: dict[str, Any] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeRecoveryResult(BaseModel):
    suggestion: RuntimeRecoverySuggestion
    auto_recovery_allowed: bool
    auto_recovery_attempted: bool
    tool_result: RuntimeToolResult | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeFailureRecovery:
    """Diagnose common runtime failures and run only permitted recovery tools."""

    def __init__(
        self,
        *,
        registry: RuntimeToolRegistry | None = None,
        approval_controller: RuntimeApprovalController | None = None,
    ) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.approval_controller = approval_controller or RuntimeApprovalController()

    def diagnose(
        self,
        failure: str | Mapping[str, Any] | RuntimeToolResult,
    ) -> RuntimeRecoverySuggestion:
        payload = _failure_payload(failure)
        failure_type = _classify_failure(payload)
        template = RECOVERY_TEMPLATES.get(failure_type, RECOVERY_TEMPLATES["unknown_failure"])
        metadata = _string_key_dict(payload.get("metadata"))
        tool_args = _recovery_tool_args(failure_type, payload, metadata)
        guardrails = [*NON_FABRICATION_GUARDRAILS, *template.get("guardrails", [])]

        return RuntimeRecoverySuggestion(
            failure_type=failure_type,
            diagnosis=str(template["diagnosis"]),
            safe_next_actions=list(template["safe_next_actions"]),
            approval_required=bool(template.get("approval_required", False)),
            approval_reason=template.get("approval_reason")
            if isinstance(template.get("approval_reason"), str)
            else None,
            recovery_tool_name=template.get("recovery_tool_name")
            if isinstance(template.get("recovery_tool_name"), str)
            else None,
            recovery_tool_args=tool_args,
            guardrails=list(dict.fromkeys(guardrails)),
            metadata={
                "error_summary": payload.get("error_summary"),
                "source_failure_type": payload.get("failure_type"),
                **metadata,
            },
        )

    def recover(
        self,
        failure: str | Mapping[str, Any] | RuntimeToolResult,
        *,
        autonomy_level: AutonomyLevel,
        actor: str = "codex",
        tool_handlers: dict[str, ToolHandler] | None = None,
    ) -> RuntimeRecoveryResult:
        suggestion = self.diagnose(failure)
        if suggestion.recovery_tool_name is None:
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=False,
                auto_recovery_attempted=False,
                warnings=["No deterministic recovery tool is required for this failure."],
            )

        spec = self.registry.get(suggestion.recovery_tool_name)
        if spec is None:
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=False,
                auto_recovery_attempted=False,
                warnings=[
                    f"Recovery tool is not registered: {suggestion.recovery_tool_name}."
                ],
            )

        autonomy_check = self.approval_controller.check_tool_allowed(
            autonomy_level,
            spec,
            actor=actor,
        )
        if spec.category == "codex" and autonomy_check.allowed:
            autonomy_check = autonomy_check.model_copy(
                update={
                    "allowed": False,
                    "requires_approval": True,
                    "reason": "Codex subprocess recovery requires explicit approval.",
                }
            )
        if not autonomy_check.allowed:
            warning = autonomy_check.reason
            if autonomy_check.requires_approval or suggestion.approval_required:
                warning = f"{warning} Request approval before executing recovery."
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=False,
                auto_recovery_attempted=False,
                warnings=[warning],
                metadata={"approval_type": autonomy_check.approval_type},
            )

        handlers = tool_handlers or {}
        handler = handlers.get(spec.tool_name)
        if handler is None:
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=True,
                auto_recovery_attempted=False,
                warnings=[
                    "No deterministic recovery handler configured; recovery was not executed."
                ],
            )

        step = RuntimeActionStep(
            step_id=f"recovery-step-{uuid4().hex[:12]}",
            plan_id=str(suggestion.metadata.get("plan_id") or "runtime-recovery"),
            step_index=0,
            action_type="recover_failure",
            tool_name=spec.tool_name,
            tool_args=suggestion.recovery_tool_args,
            requires_approval=False,
            approval_reason=None,
            expected_outputs=[],
            status="running",
            result_id=None,
            warnings=[],
            metadata={"failure_type": suggestion.failure_type},
        )

        try:
            tool_result = _normalize_tool_result(step, handler(step, spec))
            validation_errors = _validate_output(tool_result.output, spec.output_schema)
            if validation_errors:
                tool_result = tool_result.model_copy(
                    update={
                        "status": "validation_failed",
                        "error_summary": "; ".join(validation_errors),
                    }
                )
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=True,
                auto_recovery_attempted=True,
                tool_result=tool_result,
                warnings=list(tool_result.warnings),
            )
        except Exception as exc:
            now = datetime.now(UTC)
            return RuntimeRecoveryResult(
                suggestion=suggestion,
                auto_recovery_allowed=True,
                auto_recovery_attempted=True,
                tool_result=RuntimeToolResult(
                    result_id=f"runtime-recovery-result-{uuid4().hex[:12]}",
                    step_id=step.step_id,
                    tool_name=spec.tool_name,
                    status="failed",
                    output={},
                    artifact_ids=[],
                    job_ids=[],
                    error_summary=str(exc),
                    warnings=[],
                    started_at=now,
                    completed_at=now,
                    metadata={"failure_type": suggestion.failure_type},
                ),
                warnings=[str(exc)],
            )


def diagnose_failure(
    failure: str | Mapping[str, Any] | RuntimeToolResult,
    *,
    registry: RuntimeToolRegistry | None = None,
) -> RuntimeRecoverySuggestion:
    return RuntimeFailureRecovery(registry=registry).diagnose(failure)


def recover_failure(
    failure: str | Mapping[str, Any] | RuntimeToolResult,
    *,
    autonomy_level: AutonomyLevel,
    actor: str = "codex",
    registry: RuntimeToolRegistry | None = None,
    tool_handlers: dict[str, ToolHandler] | None = None,
) -> RuntimeRecoveryResult:
    return RuntimeFailureRecovery(registry=registry).recover(
        failure,
        autonomy_level=autonomy_level,
        actor=actor,
        tool_handlers=tool_handlers,
    )


RECOVERY_TEMPLATES: dict[str, dict[str, Any]] = {
    "disease_resolution_failure": {
        "diagnosis": "The disease objective could not be resolved to an approved identifier.",
        "safe_next_actions": [
            "Ask the user for a canonical disease identifier or alias.",
            (
                "Retry resolution through the approved ontology resolver after the "
                "identifier is clarified."
            ),
            "Keep downstream ranking blocked until disease provenance is recorded.",
        ],
        "guardrails": ["Do not infer a disease identity without a source-backed identifier."],
    },
    "no_candidates_found": {
        "diagnosis": "Ranking completed without source-backed candidates.",
        "safe_next_actions": [
            "Check whether the disease identifier is too narrow or stale.",
            "Suggest a broader target limit or a different disease identifier.",
            "Rerun ranking only after source inputs and filters are reviewed.",
        ],
        "recovery_tool_name": "summarize_ranking",
        "guardrails": ["Do not create candidates outside the ranking or generation pipeline."],
    },
    "external_api_unavailable": {
        "diagnosis": "An external provider was unavailable or rate-limited.",
        "safe_next_actions": [
            "Record the provider error and retry window.",
            "Use cached or previously validated artifacts only when policy allows.",
            "Request approval before broad external access or write-enabled retries.",
        ],
        "recovery_tool_name": "run_readiness",
        "approval_required": True,
        "approval_reason": "External access retries may require user or project approval.",
    },
    "literature_unavailable": {
        "diagnosis": "Literature evidence could not be refreshed from approved sources.",
        "safe_next_actions": [
            "Continue with a warning when the workflow is non-strict.",
            "For strict workflows, request approval for a later literature rerun.",
            "Do not create citations or evidence items from missing literature.",
        ],
        "recovery_tool_name": "run_literature_update",
        "approval_required": True,
        "approval_reason": "Strict literature reruns require approval before external access.",
        "guardrails": ["Do not invent citations or article summaries."],
    },
    "generation_no_valid_molecules": {
        "diagnosis": "The generation pipeline returned no valid generated molecules.",
        "safe_next_actions": [
            "Reduce generator constraints in a reviewable plan.",
            "Use a different approved seed set through the generation pipeline.",
            "Keep generated-molecule advancement blocked until review and validation pass.",
        ],
        "guardrails": ["Do not invent molecules outside the generation pipeline."],
    },
    "developability_failed": {
        "diagnosis": "Developability assessment failed or produced an invalid artifact.",
        "safe_next_actions": [
            "Inspect the developability artifact contract and provenance.",
            "Rerun developability only after invalid inputs are corrected.",
            "Preserve the failed job output for review.",
        ],
        "recovery_tool_name": "assess_developability_artifact",
    },
    "assay_import_validation_failed": {
        "diagnosis": "User-provided assay import failed deterministic validation.",
        "safe_next_actions": [
            "Produce a validation error report for the submitted assay file.",
            "Ask the data owner to correct rejected rows and metadata.",
            "Do not create assay results from invalid rows.",
        ],
        "recovery_tool_name": "summarize_assay_results",
        "guardrails": ["Do not create or modify assay results during recovery."],
    },
    "graph_build_failed": {
        "diagnosis": "Knowledge graph build failed before a valid graph artifact was produced.",
        "safe_next_actions": [
            "Validate input artifact references and provenance.",
            (
                "Run read-only contradiction and staleness checks on the last valid "
                "graph if available."
            ),
            "Rebuild the graph only after artifact contracts are corrected.",
        ],
        "recovery_tool_name": "detect_contradictions",
        "guardrails": ["Do not add graph edges without source-backed provenance."],
    },
    "model_unavailable": {
        "diagnosis": "A predictive model or model endpoint was unavailable.",
        "safe_next_actions": [
            "Record model identifier, version, and provider status.",
            "Use deterministic baseline reporting when policy allows.",
            "Do not create predictions from unavailable models.",
        ],
        "recovery_tool_name": "run_readiness",
        "guardrails": ["Do not invent model predictions."],
    },
    "codex_output_parse_failed": {
        "diagnosis": "Codex output could not be parsed as the required runtime schema.",
        "safe_next_actions": [
            "Retry with deterministic template planning where available.",
            "Capture the parse error and rejected payload for audit.",
            "Keep execution blocked until the plan validates against registered tools.",
        ],
        "guardrails": ["Do not execute unparsed or partially parsed Codex output."],
    },
    "guardrail_failure": {
        "diagnosis": "Runtime guardrails blocked unsafe plan, output, or state mutation.",
        "safe_next_actions": [
            "Stop the blocked step and preserve the guardrail report.",
            "Ask for human review when a policy exception is requested.",
            "Create a new plan that removes the prohibited action.",
        ],
        "approval_required": True,
        "approval_reason": "Guardrail overrides require explicit human approval.",
        "guardrails": ["Do not bypass guardrail findings."],
    },
    "job_timed_out": {
        "diagnosis": "A runtime job exceeded its configured deadline.",
        "safe_next_actions": [
            "Inspect job status and partial artifacts without treating them as validated.",
            "Retry with a smaller scope, lower batch size, or adjusted cost budget.",
            "Request approval before launching high-cost reruns.",
        ],
        "recovery_tool_name": "run_readiness",
        "approval_required": True,
        "approval_reason": "High-cost or expanded retries require approval.",
    },
    "permission_denied": {
        "diagnosis": "The actor lacks required RBAC permission for the requested action.",
        "safe_next_actions": [
            (
                "Ask an admin or authorized user to grant the required permission or "
                "perform the action."
            ),
            "Do not bypass RBAC or policy.",
            "Keep the failed action blocked until permission is resolved.",
        ],
        "approval_required": True,
        "approval_reason": "Permission changes require an authorized human actor.",
        "guardrails": ["Do not attempt privilege escalation."],
    },
    "unknown_failure": {
        "diagnosis": "The runtime failure did not match a known recovery template.",
        "safe_next_actions": [
            "Capture the error summary and affected artifact or job identifiers.",
            "Ask for human review before rerunning risky steps.",
            "Use only registered deterministic tools for follow-up diagnostics.",
        ],
    },
}


def _failure_payload(failure: str | Mapping[str, Any] | RuntimeToolResult) -> dict[str, Any]:
    if isinstance(failure, RuntimeToolResult):
        payload = failure.model_dump(mode="python")
        if failure.error_summary:
            payload["error_summary"] = failure.error_summary
        payload["metadata"] = dict(failure.metadata)
        return payload
    if isinstance(failure, str):
        return {"error_summary": failure}
    return dict(failure)


def _classify_failure(payload: Mapping[str, Any]) -> str:
    explicit_type = _normalize_failure_type(payload.get("failure_type"))
    if explicit_type in COMMON_RUNTIME_FAILURE_TYPES:
        return explicit_type
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_type = _normalize_failure_type(metadata.get("failure_type"))
        if metadata_type in COMMON_RUNTIME_FAILURE_TYPES:
            return metadata_type

    text = " ".join(
        str(value)
        for value in (
            payload.get("error_summary"),
            payload.get("status"),
            payload.get("tool_name"),
        )
        if value is not None
    ).lower()
    for pattern, failure_type in FAILURE_PATTERNS:
        if pattern.search(text):
            return failure_type
    return "unknown_failure"


def _normalize_failure_type(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def _recovery_tool_args(
    failure_type: str,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    args: dict[str, Any] = {"failure_type": failure_type}
    for key in (
        "artifact_id",
        "assay_artifact_id",
        "developability_artifact_id",
        "graph_artifact_id",
        "job_id",
        "project_id",
        "provider",
        "strict",
    ):
        raw = metadata.get(key, payload.get(key))
        if raw is not None:
            args[key] = raw
    error_summary = payload.get("error_summary")
    if isinstance(error_summary, str) and error_summary:
        args["error_summary"] = error_summary
    return args


def _normalize_tool_result(
    step: RuntimeActionStep,
    raw: RuntimeToolResult | dict[str, Any],
) -> RuntimeToolResult:
    if isinstance(raw, RuntimeToolResult):
        return raw
    now = datetime.now(UTC)
    output = raw.get("output")
    if not isinstance(output, dict):
        output = {
            key: value for key, value in raw.items() if key not in {"artifact_ids", "job_ids"}
        }
    metadata = _string_key_dict(raw.get("metadata"))
    return RuntimeToolResult(
        result_id=str(raw.get("result_id") or f"runtime-recovery-result-{uuid4().hex[:12]}"),
        step_id=step.step_id,
        tool_name=step.tool_name,
        status=str(raw.get("status") or "succeeded"),  # type: ignore[arg-type]
        output=output,
        artifact_ids=_string_list(raw.get("artifact_ids")),
        job_ids=_string_list(raw.get("job_ids")),
        error_summary=raw.get("error_summary")
        if isinstance(raw.get("error_summary"), str)
        else None,
        warnings=_string_list(raw.get("warnings")),
        started_at=now,
        completed_at=now,
        metadata=metadata,
    )


def _validate_output(output: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        errors.extend(
            f"missing required output field {key}"
            for key in required
            if isinstance(key, str) and key not in output
        )
    return errors


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _string_key_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str)}


FAILURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:permission denied|forbidden|unauthorized|403)\b"), "permission_denied"),
    (re.compile(r"\b(?:guardrail|policy blocked)\b"), "guardrail_failure"),
    (
        re.compile(r"\b(?:codex).*(?:parse|json|schema)\b|\b(?:parse).*(?:codex)\b"),
        "codex_output_parse_failed",
    ),
    (re.compile(r"\b(?:timed out|timeout|deadline)\b"), "job_timed_out"),
    (
        re.compile(r"\b(?:assay).*(?:validation|invalid|import)\b"),
        "assay_import_validation_failed",
    ),
    (
        re.compile(
            r"\b(?:no valid molecules|generation produced no|generator)"
            r".*(?:invalid|none|zero)\b"
        ),
        "generation_no_valid_molecules",
    ),
    (
        re.compile(r"\b(?:no candidates|zero candidates|candidates found: 0)\b"),
        "no_candidates_found",
    ),
    (
        re.compile(
            r"\b(?:disease).*(?:resolve|resolution|identifier|ontology|mondo|mesh)\b"
        ),
        "disease_resolution_failure",
    ),
    (re.compile(r"\b(?:literature|pubmed|openalex|article)\b"), "literature_unavailable"),
    (
        re.compile(r"\b(?:external api|provider unavailable|rate limit|503)\b"),
        "external_api_unavailable",
    ),
    (re.compile(r"\b(?:developability)\b"), "developability_failed"),
    (re.compile(r"\b(?:graph build|knowledge graph)\b"), "graph_build_failed"),
    (re.compile(r"\b(?:model unavailable|model endpoint|model registry)\b"), "model_unavailable"),
)


__all__ = [
    "COMMON_RUNTIME_FAILURE_TYPES",
    "RuntimeFailureRecovery",
    "RuntimeRecoveryResult",
    "RuntimeRecoverySuggestion",
    "diagnose_failure",
    "recover_failure",
]
