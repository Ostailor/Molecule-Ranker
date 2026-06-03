from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

ExecutionMode = Literal[
    "dry_run",
    "suggest_only",
    "execute_safe_tools",
    "execute_with_approval",
    "full_auto_restricted",
]
ExecutionStatus = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "dry_run",
    "suggested",
    "approval_required",
    "policy_blocked",
]
ToolHandler = Callable[[RuntimeActionStep, RuntimeToolSpec], RuntimeToolResult | dict[str, Any]]
SAFE_SIDE_EFFECT_LEVELS = {"none", "artifact_write", "external_read"}
SCIENTIFIC_OUTPUT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:safe|active|effective|binding|synthesizable)\b", re.I),
        "Tool output contains unsupported molecule property claim.",
    ),
    (
        re.compile(r"\b(?:synthesis route|retrosynthesis|lab protocol|dosing|mg/kg)\b", re.I),
        "Tool output contains prohibited protocol, synthesis, or dosing content.",
    ),
    (
        re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I),
        "Tool output appears to invent quantitative assay results.",
    ),
)


class CancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class RuntimeExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: f"runtime-exec-{uuid4().hex[:12]}")
    plan: RuntimeActionPlan
    mode: ExecutionMode
    status: ExecutionStatus
    results: list[RuntimeToolResult] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    audit_events: list[RuntimeAgentAuditEvent] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeActionExecutor:
    """Execute validated runtime action plans through registered deterministic tools."""

    def __init__(
        self,
        *,
        registry: RuntimeToolRegistry | None = None,
        tool_handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.tool_handlers = tool_handlers or {}

    def execute(
        self,
        plan: RuntimeActionPlan,
        *,
        mode: ExecutionMode,
        actor: str,
        approvals: set[str] | list[str] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> RuntimeExecutionResult:
        approved = set(approvals or [])
        token = cancellation_token or CancellationToken()
        started_at = datetime.now(UTC)
        audit_events: list[RuntimeAgentAuditEvent] = [
            _audit(
                plan,
                "runtime_execution_started",
                actor,
                f"Runtime execution started in {mode} mode.",
            )
        ]
        if not plan.validated:
            audit_events.append(
                _audit(plan, "runtime_execution_failed", actor, "Plan is not validated.")
            )
            return _execution_result(
                plan=plan,
                mode=mode,
                status="failed",
                audit_events=audit_events,
                started_at=started_at,
                warnings=["RuntimeActionPlan must be validated before execution."],
            )
        if mode in {"dry_run", "suggest_only"}:
            status: ExecutionStatus = "dry_run" if mode == "dry_run" else "suggested"
            for step in plan.steps:
                step.status = "skipped"
            audit_events.append(_audit(plan, "runtime_execution_completed", actor, status))
            return _execution_result(
                plan=plan,
                mode=mode,
                status=status,
                audit_events=audit_events,
                started_at=started_at,
            )

        results: list[RuntimeToolResult] = []
        artifact_ids: list[str] = []
        job_ids: list[str] = []
        warnings: list[str] = []

        for step in sorted(plan.steps, key=lambda item: item.step_index):
            if token.cancelled:
                _skip_remaining(plan, from_index=step.step_index)
                audit_events.append(
                    _audit(plan, "runtime_execution_cancelled", actor, "Execution cancelled.")
                )
                return _execution_result(
                    plan=plan,
                    mode=mode,
                    status="cancelled",
                    results=results,
                    artifact_ids=artifact_ids,
                    job_ids=job_ids,
                    warnings=warnings,
                    audit_events=audit_events,
                    started_at=started_at,
                )

            spec = self.registry.get(step.tool_name)
            if spec is None:
                result = _result_for_step(
                    step,
                    status="policy_blocked",
                    error_summary=f"Tool is not registered: {step.tool_name}",
                )
                results.append(result)
                step.status = "failed"
                _skip_remaining(plan, from_index=step.step_index + 1)
                audit_events.append(
                    _audit(plan, "runtime_step_policy_blocked", actor, result.error_summary or "")
                )
                return _execution_result(
                    plan=plan,
                    mode=mode,
                    status="policy_blocked",
                    results=results,
                    artifact_ids=artifact_ids,
                    job_ids=job_ids,
                    warnings=warnings,
                    audit_events=audit_events,
                    started_at=started_at,
                )

            policy_error = _policy_error(step, spec, mode, actor, approved)
            if policy_error:
                result_status = (
                    "approval_required"
                    if policy_error
                    in {
                        "External write requires approval.",
                        "Destructive action requires approval.",
                        "Tool requires approval.",
                    }
                    else "policy_blocked"
                )
                result = _result_for_step(
                    step,
                    status=result_status,
                    error_summary=policy_error,
                )
                results.append(result)
                audit_events.append(
                    _audit(plan, f"runtime_step_{result_status}", actor, policy_error)
                )
                return _execution_result(
                    plan=plan,
                    mode=mode,
                    status=result_status,
                    results=results,
                    artifact_ids=artifact_ids,
                    job_ids=job_ids,
                    warnings=warnings,
                    audit_events=audit_events,
                    started_at=started_at,
                )

            step.status = "running"
            audit_events.append(
                _audit(
                    plan,
                    "runtime_step_started",
                    actor,
                    f"Started {step.tool_name}.",
                    object_type="RuntimeActionStep",
                    object_id=step.step_id,
                )
            )
            try:
                tool_result = _normalize_tool_result(
                    step,
                    self._call_tool(step, spec),
                )
                validation_errors = _validate_output(tool_result.output, spec.output_schema)
                if validation_errors:
                    tool_result.status = "validation_failed"
                    tool_result.error_summary = "; ".join(validation_errors)
                if tool_result.artifact_ids:
                    guardrail_warnings = _scientific_guardrail_warnings(tool_result)
                    if guardrail_warnings:
                        tool_result.status = "validation_failed"
                        tool_result.warnings.extend(guardrail_warnings)
                        tool_result.error_summary = "; ".join(guardrail_warnings)
                if tool_result.status == "succeeded":
                    step.status = "succeeded"
                    step.result_id = tool_result.result_id
                    artifact_ids.extend(tool_result.artifact_ids)
                    job_ids.extend(tool_result.job_ids)
                    audit_events.append(
                        _audit(
                            plan,
                            "runtime_step_succeeded",
                            actor,
                            f"{step.tool_name} succeeded.",
                            object_type="RuntimeToolResult",
                            object_id=tool_result.result_id,
                        )
                    )
                    results.append(tool_result)
                    continue
                step.status = "failed"
                step.result_id = tool_result.result_id
                results.append(tool_result)
                audit_events.append(
                    _audit(
                        plan,
                        "runtime_step_failed",
                        actor,
                        tool_result.error_summary or f"{step.tool_name} failed.",
                        object_type="RuntimeToolResult",
                        object_id=tool_result.result_id,
                    )
                )
                if _step_optional(step):
                    warnings.append(tool_result.error_summary or f"{step.tool_name} failed.")
                    continue
                _skip_remaining(plan, from_index=step.step_index + 1)
                return _execution_result(
                    plan=plan,
                    mode=mode,
                    status="failed",
                    results=results,
                    artifact_ids=artifact_ids,
                    job_ids=job_ids,
                    warnings=warnings,
                    audit_events=audit_events,
                    started_at=started_at,
                )
            except Exception as exc:
                result = _result_for_step(step, status="failed", error_summary=str(exc))
                step.status = "failed"
                step.result_id = result.result_id
                results.append(result)
                audit_events.append(
                    _audit(
                        plan,
                        "runtime_step_failed",
                        actor,
                        str(exc),
                        object_type="RuntimeToolResult",
                        object_id=result.result_id,
                    )
                )
                if _step_optional(step):
                    warnings.append(str(exc))
                    continue
                _skip_remaining(plan, from_index=step.step_index + 1)
                return _execution_result(
                    plan=plan,
                    mode=mode,
                    status="failed",
                    results=results,
                    artifact_ids=artifact_ids,
                    job_ids=job_ids,
                    warnings=warnings,
                    audit_events=audit_events,
                    started_at=started_at,
                )

        audit_events.append(
            _audit(plan, "runtime_execution_completed", actor, "Runtime execution succeeded.")
        )
        return _execution_result(
            plan=plan,
            mode=mode,
            status="succeeded",
            results=results,
            artifact_ids=artifact_ids,
            job_ids=job_ids,
            warnings=warnings,
            audit_events=audit_events,
            started_at=started_at,
        )

    def _call_tool(
        self,
        step: RuntimeActionStep,
        spec: RuntimeToolSpec,
    ) -> RuntimeToolResult | dict[str, Any]:
        handler = self.tool_handlers.get(step.tool_name)
        if handler is None:
            raise RuntimeError(
                "No deterministic handler configured for runtime tool "
                f"{step.tool_name}; use {spec.metadata.get('deterministic_entrypoint')}."
            )
        return handler(step, spec)


def _policy_error(
    step: RuntimeActionStep,
    spec: RuntimeToolSpec,
    mode: ExecutionMode,
    actor: str,
    approvals: set[str],
) -> str | None:
    if spec.category == "codex":
        return "Codex tools cannot be executed by RuntimeActionExecutor."
    if mode == "execute_safe_tools" and spec.side_effect_level not in SAFE_SIDE_EFFECT_LEVELS:
        return f"Tool {step.tool_name} is not safe for execute_safe_tools mode."
    if spec.side_effect_level == "external_write" and not _is_approved(
        step, approvals, "external_write"
    ):
        return "External write requires approval."
    if "destructive_action" in spec.policy_tags and not _is_approved(
        step, approvals, "destructive_action"
    ):
        return "Destructive action requires approval."
    if ("stage_gate" in spec.policy_tags or "campaign_advance" in spec.policy_tags) and (
        actor == "codex" or not _is_approved(step, approvals, "stage_gate")
    ):
        return "Campaign or stage-gate approval requires a human actor with approval."
    if spec.requires_approval_by_default and not _is_approved(step, approvals, step.tool_name):
        return "Tool requires approval."
    return None


def _is_approved(step: RuntimeActionStep, approvals: set[str], approval_type: str) -> bool:
    return bool({approval_type, step.step_id, step.tool_name}.intersection(approvals))


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
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return RuntimeToolResult(
        result_id=str(raw.get("result_id") or f"runtime-result-{uuid4().hex[:12]}"),
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


def _result_for_step(
    step: RuntimeActionStep,
    *,
    status: str,
    error_summary: str | None = None,
) -> RuntimeToolResult:
    now = datetime.now(UTC)
    return RuntimeToolResult(
        result_id=f"runtime-result-{uuid4().hex[:12]}",
        step_id=step.step_id,
        tool_name=step.tool_name,
        status=status,  # type: ignore[arg-type]
        output={},
        artifact_ids=[],
        job_ids=[],
        error_summary=error_summary,
        warnings=[],
        started_at=now,
        completed_at=now,
    )


def _execution_result(
    *,
    plan: RuntimeActionPlan,
    mode: ExecutionMode,
    status: ExecutionStatus,
    audit_events: list[RuntimeAgentAuditEvent],
    started_at: datetime,
    results: list[RuntimeToolResult] | None = None,
    artifact_ids: list[str] | None = None,
    job_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        plan=plan,
        mode=mode,
        status=status,
        results=results or [],
        artifact_ids=artifact_ids or [],
        job_ids=job_ids or [],
        warnings=warnings or [],
        audit_events=audit_events,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _validate_output(output: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in output:
                errors.append(f"missing required output field {key}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, property_schema in properties.items():
            if key in output and isinstance(property_schema, dict):
                expected_type = property_schema.get("type")
                if expected_type and not _json_type_matches(output[key], str(expected_type)):
                    errors.append(f"output field {key} must be {expected_type}")
    return errors


def _json_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _scientific_guardrail_warnings(result: RuntimeToolResult) -> list[str]:
    text = json.dumps(result.output, sort_keys=True, default=str)
    return [warning for pattern, warning in SCIENTIFIC_OUTPUT_PATTERNS if pattern.search(text)]


def _skip_remaining(plan: RuntimeActionPlan, *, from_index: int) -> None:
    for step in plan.steps:
        if step.step_index >= from_index and step.status in {"pending", "running"}:
            step.status = "skipped"


def _step_optional(step: RuntimeActionStep) -> bool:
    return bool(step.metadata.get("optional") or step.metadata.get("allow_failure"))


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _audit(
    plan: RuntimeActionPlan,
    event_type: str,
    actor: str,
    summary: str,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"runtime-audit-{uuid4().hex[:12]}",
        session_id=plan.session_id,
        event_type=event_type,
        actor=actor,
        timestamp=datetime.now(UTC),
        summary=summary,
        object_type=object_type,
        object_id=object_id,
        before=None,
        after=None,
        metadata={"plan_id": plan.plan_id},
    )


__all__ = [
    "CancellationToken",
    "RuntimeActionExecutor",
    "RuntimeExecutionResult",
]
