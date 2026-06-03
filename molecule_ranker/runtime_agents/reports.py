from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.context import redact_sensitive_context
from molecule_ranker.runtime_agents.executor import RuntimeExecutionResult
from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailResult
from molecule_ranker.runtime_agents.schemas import (
    RuntimeAgentSession,
    RuntimeApprovalRequest,
)

RUNTIME_ARTIFACT_FILENAMES = {
    "session": "runtime_session.json",
    "plan": "runtime_action_plan.json",
    "tool_results": "runtime_tool_results.json",
    "audit_log": "runtime_audit_log.json",
    "guardrail_report": "runtime_guardrail_report.json",
    "summary": "runtime_summary.md",
}
RUNTIME_DISCLAIMERS = [
    "Codex runtime actions are orchestrated tool calls.",
    "Codex output is not biomedical evidence.",
    "Tool outputs must be validated.",
    "No medical advice.",
    "No lab protocols.",
    "No synthesis instructions.",
    "No dosing.",
]
FORBIDDEN_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)lab protocol:\s*[^.。\n]*(?:[.。]|$)"),
    re.compile(r"(?i)\bincubate\s+[^.。\n]*(?:[.。]|$)"),
    re.compile(r"(?i)synthesis route\s+[^.。\n]*(?:[.。]|$)"),
    re.compile(r"(?i)\bdose\s+[^.。\n]*(?:[.。]|$)"),
    re.compile(r"(?i)\b\d+(?:\.\d+)?\s*mg/kg\b"),
    re.compile(r"(?i)\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d[^,;.\n]*"),
)
SANITIZED_TEXT = "[REDACTED_RUNTIME_GUARDRAIL_TEXT]"


class RuntimeArtifactBundle(BaseModel):
    output_dir: str
    artifact_paths: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


def write_runtime_artifacts(
    output_dir: str | Path,
    *,
    session: RuntimeAgentSession,
    execution_result: RuntimeExecutionResult,
    approvals: list[RuntimeApprovalRequest] | None = None,
    guardrail_report: RuntimeGuardrailResult | None = None,
    next_recommended_actions: list[str] | None = None,
    limitations: list[str] | None = None,
) -> RuntimeArtifactBundle:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    approval_requests = approvals or []
    guardrails = guardrail_report or RuntimeGuardrailResult(allowed=True)
    artifacts = {
        "session": _safe_payload(session.model_dump(mode="json")),
        "plan": _safe_payload(execution_result.plan.model_dump(mode="json")),
        "tool_results": _safe_payload(
            [result.model_dump(mode="json") for result in execution_result.results]
        ),
        "audit_log": _safe_payload(
            [event.model_dump(mode="json") for event in execution_result.audit_events]
        ),
        "guardrail_report": _safe_payload(guardrails.model_dump(mode="json")),
    }
    written: dict[str, str] = {}
    for key, filename in RUNTIME_ARTIFACT_FILENAMES.items():
        path = target / filename
        if key == "summary":
            path.write_text(
                build_runtime_summary_markdown(
                    session=session,
                    execution_result=execution_result,
                    approvals=approval_requests,
                    guardrail_report=guardrails,
                    next_recommended_actions=next_recommended_actions or [],
                    limitations=limitations or [],
                ),
                encoding="utf-8",
            )
        else:
            path.write_text(
                json.dumps(artifacts[key], indent=2, sort_keys=True),
                encoding="utf-8",
            )
        written[filename] = str(path)
    return RuntimeArtifactBundle(
        output_dir=str(target),
        artifact_paths=written,
        metadata={
            "artifact_count": len(written),
            "guardrail_allowed": guardrails.allowed,
            "approval_count": len(approval_requests),
        },
    )


def build_runtime_summary_markdown(
    *,
    session: RuntimeAgentSession,
    execution_result: RuntimeExecutionResult,
    approvals: list[RuntimeApprovalRequest],
    guardrail_report: RuntimeGuardrailResult,
    next_recommended_actions: list[str],
    limitations: list[str],
) -> str:
    plan = execution_result.plan
    lines = [
        "# Runtime Summary",
        "",
        *[f"- {disclaimer}" for disclaimer in RUNTIME_DISCLAIMERS],
        "",
        "## 1. User goal",
        _safe_text(session.user_goal),
        "",
        "## 2. Autonomy level",
        _safe_text(session.autonomy_level),
        "",
        "## 3. Plan summary",
        _safe_text(plan.plan_summary),
        "",
        "## 4. Steps executed",
        *_step_lines(execution_result),
        "",
        "## 5. Approvals requested",
        *_approval_lines(approvals, plan.required_approvals),
        "",
        "## 6. Artifacts produced",
        *_list_lines(execution_result.artifact_ids),
        "",
        "## 7. Jobs launched",
        *_list_lines(execution_result.job_ids),
        "",
        "## 8. Failures and recovery",
        *_failure_lines(execution_result),
        "",
        "## 9. Guardrail warnings",
        *_guardrail_lines(execution_result, guardrail_report),
        "",
        "## 10. Next recommended actions",
        *_list_lines(next_recommended_actions or _default_next_actions(execution_result)),
        "",
        "## 11. Limitations",
        *_list_lines(limitations or RUNTIME_DISCLAIMERS),
        "",
    ]
    return "\n".join(lines)


def _step_lines(execution_result: RuntimeExecutionResult) -> list[str]:
    if not execution_result.plan.steps:
        return ["- None."]
    result_by_step = {result.step_id: result for result in execution_result.results}
    lines: list[str] = []
    for step in sorted(execution_result.plan.steps, key=lambda item: item.step_index):
        result = result_by_step.get(step.step_id)
        detail = f"- {step.step_index + 1}. `{step.tool_name}`: {step.status}"
        if result is not None:
            detail += f" (`{result.status}` result `{result.result_id}`)"
        lines.append(_safe_text(detail))
    return lines


def _approval_lines(
    approvals: list[RuntimeApprovalRequest],
    required_approvals: list[str],
) -> list[str]:
    lines: list[str] = []
    for approval in approvals:
        lines.append(
            _safe_text(
                "- "
                f"{approval.approval_id}: {approval.approval_type} "
                f"status={approval.status}; reason={approval.reason}"
            )
        )
    for approval_type in required_approvals:
        if not any(approval.approval_type == approval_type for approval in approvals):
            lines.append(_safe_text(f"- Required by plan: {approval_type}."))
    return lines or ["- None."]


def _failure_lines(execution_result: RuntimeExecutionResult) -> list[str]:
    lines: list[str] = []
    for result in execution_result.results:
        if result.status in {"failed", "policy_blocked", "validation_failed"}:
            lines.append(
                _safe_text(
                    f"- `{result.tool_name}` {result.status}: "
                    f"{result.error_summary or 'No error summary provided.'}"
                )
            )
    for warning in execution_result.warnings:
        lines.append(_safe_text(f"- Warning: {warning}"))
    return lines or ["- No failures recorded."]


def _guardrail_lines(
    execution_result: RuntimeExecutionResult,
    guardrail_report: RuntimeGuardrailResult,
) -> list[str]:
    lines = [_safe_text(f"- Execution warning: {warning}") for warning in execution_result.warnings]
    for violation in guardrail_report.violations:
        lines.append(
            _safe_text(
                f"- {violation.scope}/{violation.code}: {violation.message} "
                f"(severity={violation.severity})"
            )
        )
    for warning in guardrail_report.warnings:
        lines.append(_safe_text(f"- {warning}"))
    return lines or ["- None."]


def _default_next_actions(execution_result: RuntimeExecutionResult) -> list[str]:
    if execution_result.status in {"failed", "policy_blocked", "approval_required"}:
        return [
            "Inspect failed jobs or blocked steps.",
            "Request required approvals from a human reviewer.",
            "Rerun only after deterministic validators pass.",
        ]
    return [
        "Review produced artifacts.",
        "Validate tool outputs through deterministic validators.",
        "Create a review workspace if human assessment is needed.",
    ]


def _list_lines(values: list[str]) -> list[str]:
    return [_safe_text(f"- {value}") for value in values] or ["- None."]


def _safe_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {key: _safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _safe_text(text: str) -> str:
    redacted = redact_sensitive_context(text)
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        redacted = pattern.sub(SANITIZED_TEXT, redacted)
    return redacted


__all__ = [
    "RUNTIME_ARTIFACT_FILENAMES",
    "RUNTIME_DISCLAIMERS",
    "RuntimeArtifactBundle",
    "build_runtime_summary_markdown",
    "write_runtime_artifacts",
]
