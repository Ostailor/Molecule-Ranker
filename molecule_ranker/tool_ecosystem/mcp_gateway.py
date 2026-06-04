from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor, ToolHandler
from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2
from molecule_ranker.tool_ecosystem.schemas import ToolUsageRecord

MCPGatewayStatus = Literal[
    "succeeded",
    "failed",
    "approval_required",
    "policy_blocked",
    "validation_failed",
]
SAFE_SIDE_EFFECT_LEVELS = {"none", "artifact_write", "external_read"}
SECRET_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "client_secret",
    "credentials",
    "password",
    "secret",
    "token",
}
CACHE_MARKERS = ("/.cache/", "/cache/", "\\cache\\", "\\.cache\\")


class MCPGatewayContext(BaseModel):
    user_id: str
    project_id: str | None = None
    org_id: str | None = None
    user_permissions: set[str] = Field(default_factory=set)
    approvals: set[str] = Field(default_factory=set)
    sandbox_profile: str = "read_only"
    actor: str = "codex"
    known_artifacts: set[str] = Field(default_factory=set)
    known_citations: set[str] = Field(default_factory=set)
    known_molecules: set[str] = Field(default_factory=set)


class MCPGatewayResult(BaseModel):
    status: MCPGatewayStatus
    tool_name: str | None = None
    tool_version: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    audit_events: list[RuntimeAgentAuditEvent] = Field(default_factory=list)
    usage_record: ToolUsageRecord | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


ArtifactValidator = Callable[[RuntimeToolResult], list[str]]


class InternalMCPGateway:
    """Controlled internal MCP-compatible gateway for approved tools."""

    def __init__(
        self,
        *,
        registry: ToolRegistryV2 | None = None,
        tool_handlers: dict[str, ToolHandler] | None = None,
        approved_artifacts: list[dict[str, Any]] | None = None,
        approved_prompt_templates: list[dict[str, Any]] | None = None,
        artifact_validator: ArtifactValidator | None = None,
    ) -> None:
        self.registry = registry or ToolRegistryV2.default()
        self.tool_handlers = tool_handlers or {}
        self.approved_artifacts = approved_artifacts or []
        self.approved_prompt_templates = approved_prompt_templates or []
        self.artifact_validator = artifact_validator or _default_artifact_validator
        self.audit_events: list[RuntimeAgentAuditEvent] = []

    def tools_list(self, context: MCPGatewayContext) -> dict[str, Any]:
        tools = self.registry.list_tools_visible_to_user(
            user_permissions=context.user_permissions,
            project_id=context.project_id,
            org_id=context.org_id,
        )
        return {
            "tools": [_tool_descriptor(tool) for tool in tools],
            "metadata": {"count": len(tools)},
        }

    def tools_get(self, tool_name: str, context: MCPGatewayContext) -> dict[str, Any]:
        visible = {
            tool.tool_name
            for tool in self.registry.list_tools_visible_to_user(
                user_permissions=context.user_permissions,
                project_id=context.project_id,
                org_id=context.org_id,
            )
        }
        spec = self.registry.resolve_tool(tool_name)
        if spec.tool_name not in visible:
            raise PermissionError(f"tool is not visible to user/project: {tool_name}")
        return _tool_descriptor(spec)

    def tools_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: MCPGatewayContext,
        *,
        version: str | None = None,
    ) -> MCPGatewayResult:
        try:
            spec = self.registry.resolve_tool(tool_name, version=version)
        except Exception as exc:
            return self._blocked_result(
                "policy_blocked",
                tool_name=tool_name,
                error_summary=str(exc),
                context=context,
            )
        visibility_error = self._visibility_error(spec, context)
        if visibility_error is not None:
            return self._blocked_result(
                "policy_blocked",
                tool_name=spec.tool_name,
                error_summary=visibility_error,
                context=context,
            )
        policy_error = self._preflight_policy_error(spec, context)
        if policy_error is not None:
            status: MCPGatewayStatus = (
                "approval_required"
                if "approval" in policy_error.lower()
                or spec.side_effect_level == "external_write"
                else "policy_blocked"
            )
            return self._blocked_result(
                status,
                tool_name=spec.tool_name,
                tool_version=_tool_version(spec),
                error_summary=policy_error,
                context=context,
            )

        plan = _plan_for_call(spec, arguments, context)
        executor = RuntimeActionExecutor(
            registry=self.registry.to_runtime_tool_registry(),
            tool_handlers=self.tool_handlers,
        )
        execution = executor.execute(
            plan,
            mode="execute_with_approval",
            actor=context.actor,
            approvals=context.approvals,
        )
        self.audit_events.extend(execution.audit_events)
        if not execution.results:
            return self._blocked_result(
                "failed",
                tool_name=spec.tool_name,
                tool_version=_tool_version(spec),
                error_summary="Tool execution returned no result.",
                context=context,
                audit_events=execution.audit_events,
            )
        result = execution.results[0]
        validation_errors = self._result_validation_errors(result, context)
        status: MCPGatewayStatus = _gateway_status(result.status)
        if validation_errors:
            status = "validation_failed"
            result = result.model_copy(
                update={
                    "status": "validation_failed",
                    "error_summary": "; ".join(validation_errors),
                    "warnings": [*result.warnings, *validation_errors],
                }
            )
        usage = self._record_usage(spec, result, status, context)
        return MCPGatewayResult(
            status=status,
            tool_name=spec.tool_name,
            tool_version=_tool_version(spec),
            output=_sanitize(result.output),
            artifact_ids=result.artifact_ids,
            job_ids=result.job_ids,
            error_summary=result.error_summary,
            warnings=result.warnings,
            audit_events=execution.audit_events,
            usage_record=usage,
            metadata={"execution_status": execution.status},
        )

    def resources_list(self, context: MCPGatewayContext) -> dict[str, Any]:
        resources = [
            _sanitize(resource)
            for resource in self.approved_artifacts
            if _artifact_visible(resource, context)
        ]
        return {"resources": resources, "metadata": {"count": len(resources)}}

    def prompts_list(self, context: MCPGatewayContext) -> dict[str, Any]:
        prompts = [
            _sanitize(prompt)
            for prompt in self.approved_prompt_templates
            if prompt.get("approved") is True
            and _project_allowed(prompt, context.project_id)
            and _permission_allowed(prompt, context.user_permissions)
        ]
        return {"prompts": prompts, "metadata": {"count": len(prompts)}}

    def handle(self, endpoint: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        context = MCPGatewayContext.model_validate(payload.get("context", {}))
        if endpoint == "tools/list":
            return self.tools_list(context)
        if endpoint == "tools/get":
            return self.tools_get(str(payload["tool_name"]), context)
        if endpoint == "tools/call":
            result = self.tools_call(
                str(payload["tool_name"]),
                _dict(payload.get("arguments")),
                context,
                version=payload.get("version") if isinstance(payload.get("version"), str) else None,
            )
            return result.model_dump(mode="json")
        if endpoint == "resources/list":
            return self.resources_list(context)
        if endpoint == "prompts/list":
            return self.prompts_list(context)
        raise ValueError(f"Unknown MCP gateway endpoint: {endpoint}")

    def _visibility_error(self, spec: RuntimeToolSpec, context: MCPGatewayContext) -> str | None:
        visible = {
            tool.tool_name
            for tool in self.registry.list_tools_visible_to_user(
                user_permissions=context.user_permissions,
                project_id=context.project_id,
                org_id=context.org_id,
            )
        }
        if spec.tool_name not in visible:
            return f"Unauthorized tool: {spec.tool_name}"
        return None

    def _preflight_policy_error(
        self,
        spec: RuntimeToolSpec,
        context: MCPGatewayContext,
    ) -> str | None:
        if spec.category == "codex":
            return "Codex tools cannot be executed through the MCP gateway."
        if spec.side_effect_level == "external_write" and not _approved(
            context, spec, "external_write"
        ):
            return "External write requires approval."
        if spec.requires_approval_by_default and not _approved(context, spec, spec.tool_name):
            return "Tool requires approval."
        if ("stage_gate" in spec.policy_tags or "campaign_advance" in spec.policy_tags) and (
            context.actor == "codex" or not _approved(context, spec, "stage_gate")
        ):
            return "Campaign or stage-gate approval requires a human actor with approval."
        if spec.side_effect_level not in SAFE_SIDE_EFFECT_LEVELS and not _approved(
            context, spec, "execute_plan"
        ):
            return f"Tool side-effect level requires approval: {spec.side_effect_level}."
        sandbox_error = _sandbox_error(spec, context.sandbox_profile)
        if sandbox_error is not None:
            return sandbox_error
        return None

    def _result_validation_errors(
        self,
        result: RuntimeToolResult,
        context: MCPGatewayContext,
    ) -> list[str]:
        errors = list(self.artifact_validator(result))
        runtime_registry = self.registry.to_runtime_tool_registry()
        checker = RuntimeGuardrailChecker(registry=runtime_registry)
        output_guardrail = checker.check_output(
            result,
            known_citations=context.known_citations,
            known_molecules=context.known_molecules,
        )
        errors.extend(violation.message for violation in output_guardrail.violations)
        state_guardrail = checker.check_state(
            result,
            expected_output_schema=runtime_registry.require(result.tool_name).output_schema,
            known_artifacts=context.known_artifacts,
            known_citations=context.known_citations,
        )
        errors.extend(violation.message for violation in state_guardrail.violations)
        return list(dict.fromkeys(errors))

    def _record_usage(
        self,
        spec: RuntimeToolSpec,
        result: RuntimeToolResult,
        status: MCPGatewayStatus,
        context: MCPGatewayContext,
    ) -> ToolUsageRecord:
        package = spec.metadata.get("tool_package")
        package_id = package.get("package_id") if isinstance(package, dict) else "unknown"
        invoked_by = (
            context.actor
            if context.actor in {"codex", "user", "workflow", "system"}
            else "system"
        )
        usage = self.registry.track_usage(
            package_id=str(package_id),
            tool_name=spec.tool_name,
            tool_version=_tool_version(spec),
            invoked_by=invoked_by,
            status=status,
            session_id=result.step_id,
            project_id=context.project_id,
            artifact_ids=result.artifact_ids,
            warnings=result.warnings,
            metadata={"result_id": result.result_id},
            started_at=result.started_at,
            completed_at=result.completed_at,
        )
        return usage

    def _blocked_result(
        self,
        status: MCPGatewayStatus,
        *,
        tool_name: str,
        error_summary: str,
        context: MCPGatewayContext,
        tool_version: str | None = None,
        audit_events: list[RuntimeAgentAuditEvent] | None = None,
    ) -> MCPGatewayResult:
        event = _audit_event(
            event_type=f"mcp_gateway_{status}",
            actor=context.actor,
            summary=error_summary,
            object_type="RuntimeToolSpec",
            object_id=tool_name,
        )
        events = [*(audit_events or []), event]
        self.audit_events.extend(events)
        return MCPGatewayResult(
            status=status,
            tool_name=tool_name,
            tool_version=tool_version,
            error_summary=error_summary,
            audit_events=events,
        )


def _plan_for_call(
    spec: RuntimeToolSpec,
    arguments: dict[str, Any],
    context: MCPGatewayContext,
) -> RuntimeActionPlan:
    plan_id = f"mcp-plan-{uuid4().hex[:12]}"
    step = RuntimeActionStep(
        step_id=f"mcp-step-{uuid4().hex[:12]}",
        plan_id=plan_id,
        step_index=0,
        action_type=spec.tool_name,
        tool_name=spec.tool_name,
        tool_args=arguments,
        requires_approval=spec.requires_approval_by_default
        or spec.side_effect_level == "external_write",
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={"mcp_gateway": True},
    )
    return RuntimeActionPlan(
        plan_id=plan_id,
        session_id=f"mcp-session-{uuid4().hex[:12]}",
        user_goal=f"Invoke MCP tool {spec.tool_name}.",
        plan_summary=f"MCP gateway call for {spec.tool_name}.",
        steps=[step],
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                spec.tool_name: {
                    "required_permissions": spec.required_permissions,
                    "side_effect_level": spec.side_effect_level,
                    "policy_tags": spec.policy_tags,
                    "tool_package": spec.metadata.get("tool_package"),
                    "tool_policy": spec.metadata.get("tool_policy"),
                }
            },
            "runtime_context": {
                "project_id": context.project_id,
                "org_id": context.org_id,
                "user_id": context.user_id,
                "user_permissions": sorted(context.user_permissions),
            },
        },
    )


def _tool_descriptor(spec: RuntimeToolSpec) -> dict[str, Any]:
    return _sanitize(
        {
            "name": spec.tool_name,
            "description": spec.description,
            "inputSchema": spec.input_schema,
            "outputSchema": spec.output_schema,
            "annotations": {
                "category": spec.category,
                "side_effect_level": spec.side_effect_level,
                "required_permissions": spec.required_permissions,
                "policy_tags": spec.policy_tags,
                "requires_approval": spec.requires_approval_by_default,
                "tool_package": spec.metadata.get("tool_package"),
                "tool_version": spec.metadata.get("tool_version"),
            },
        }
    )


def _gateway_status(status: str) -> MCPGatewayStatus:
    if status in {
        "succeeded",
        "failed",
        "policy_blocked",
        "approval_required",
        "validation_failed",
    }:
        return status  # type: ignore[return-value]
    return "failed"


def _tool_version(spec: RuntimeToolSpec) -> str:
    raw = spec.metadata.get("tool_version")
    if isinstance(raw, dict) and isinstance(raw.get("version"), str):
        return raw["version"]
    package = spec.metadata.get("tool_package")
    if isinstance(package, dict) and isinstance(package.get("version"), str):
        return package["version"]
    return "unknown"


def _approved(context: MCPGatewayContext, spec: RuntimeToolSpec, approval_type: str) -> bool:
    return bool({approval_type, spec.tool_name}.intersection(context.approvals))


def _sandbox_error(spec: RuntimeToolSpec, sandbox_profile: str) -> str | None:
    policy = spec.metadata.get("tool_policy")
    approved_profile = None
    if isinstance(policy, dict) and isinstance(policy.get("sandbox_profile"), str):
        approved_profile = policy["sandbox_profile"]
    if approved_profile is not None and sandbox_profile != approved_profile:
        return f"Sandbox profile mismatch: expected {approved_profile}."
    if sandbox_profile == "read_only" and spec.side_effect_level in {
        "artifact_write",
        "db_write",
        "external_write",
        "codex_subprocess",
    }:
        return f"Sandbox profile read_only blocks {spec.side_effect_level}."
    if sandbox_profile == "artifact_write" and spec.side_effect_level in {
        "db_write",
        "external_write",
        "codex_subprocess",
    }:
        return f"Sandbox profile artifact_write blocks {spec.side_effect_level}."
    return None


def _default_artifact_validator(result: RuntimeToolResult) -> list[str]:
    if not result.artifact_ids:
        return []
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    if metadata.get("artifact_provenance"):
        return []
    return ["Artifact validation failed: artifact provenance is required."]


def _artifact_visible(resource: dict[str, Any], context: MCPGatewayContext) -> bool:
    if resource.get("approved") is not True:
        return False
    if not _project_allowed(resource, context.project_id):
        return False
    if not _permission_allowed(resource, context.user_permissions):
        return False
    path = str(resource.get("path") or resource.get("uri") or "")
    return not _looks_sensitive_path(path)


def _project_allowed(value: Mapping[str, Any], project_id: str | None) -> bool:
    projects = value.get("project_ids")
    if not isinstance(projects, list) or not projects:
        return True
    return project_id in {item for item in projects if isinstance(item, str)}


def _permission_allowed(value: Mapping[str, Any], permissions: set[str]) -> bool:
    required = value.get("required_permissions")
    if not isinstance(required, list) or not required:
        return True
    return {item for item in required if isinstance(item, str)}.issubset(permissions)


def _looks_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in CACHE_MARKERS) or any(
        key in lowered for key in SECRET_KEYS
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SECRET_KEYS or "credential" in normalized:
                continue
            if normalized in {"cache_path", "cache_file"}:
                continue
            if isinstance(item, str) and _looks_sensitive_path(item):
                continue
            sanitized[key] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        if _looks_sensitive_path(value):
            return "[redacted]"
        return value
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _audit_event(
    *,
    event_type: str,
    actor: str,
    summary: str,
    object_type: str | None = None,
    object_id: str | None = None,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"mcp-audit-{uuid4().hex[:12]}",
        session_id="mcp-gateway",
        event_type=event_type,
        actor=actor,
        timestamp=datetime.now(UTC),
        summary=summary,
        object_type=object_type,
        object_id=object_id,
        before=None,
        after=None,
        metadata={},
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


__all__ = [
    "InternalMCPGateway",
    "MCPGatewayContext",
    "MCPGatewayResult",
]
