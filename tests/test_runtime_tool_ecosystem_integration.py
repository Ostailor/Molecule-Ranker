from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor
from molecule_ranker.runtime_agents.planner import CodexRuntimePlanner, RuntimePlanValidationError
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.tool_ecosystem.mcp_adapter import MCPServerAdapter, MCPServerConfig
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2, hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_codex_uses_installed_approved_tool_from_governed_registry() -> None:
    governed = ToolRegistryV2(register_builtins=False)
    spec = _tool(
        "plugin.qc.score_qc_report",
        description="Approved QC report plugin for artifact metric summaries.",
        required_permissions=["qc:run"],
    )
    package = _register_package(governed, spec, status="approved")
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(governed)
    codex = FakeCodexPlannerClient(_plan_payload("plugin.qc.score_qc_report"))
    planner = CodexRuntimePlanner(registry=runtime_registry, codex_client=codex)

    plan = planner.plan(
        user_goal="Create a QC report using the approved plugin.",
        session_id="session-1",
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        user_permissions={"qc:run"},
    )

    assert plan.validated is True
    assert plan.steps[0].tool_name == "plugin.qc.score_qc_report"
    assert "plugin.qc.score_qc_report" in codex.prompts[0]
    assert package.package_id in codex.prompts[0]


def test_codex_cannot_use_quarantined_tool() -> None:
    governed = ToolRegistryV2(register_builtins=False)
    _register_package(
        governed,
        _tool("plugin.qc.quarantined_report", required_permissions=["qc:run"]),
        status="quarantined",
    )
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(governed)
    planner = CodexRuntimePlanner(
        registry=runtime_registry,
        codex_client=FakeCodexPlannerClient(_plan_payload("plugin.qc.quarantined_report")),
    )

    with pytest.raises(RuntimePlanValidationError, match="Tool is not allowed"):
        planner.plan(
            user_goal="Create a QC report with the quarantined plugin.",
            session_id="session-1",
            project_id="project-1",
            org_id="org-1",
            user_id="user-1",
            user_permissions={"qc:run"},
        )


def test_approved_mcp_fake_tool_runs_through_runtime_executor() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    server = FakeMCPServer()
    adapter.register_server_config(
        MCPServerConfig(
            server_id="benchling",
            name="benchling",
            enabled=True,
            approved=True,
            allowed_network_domains=["benchling.internal"],
            sandbox_profile="tool_external_read",
        ),
        client=server,
    )
    adapter.introspect_server("benchling")
    adapter.scan_server_manifest("benchling")
    adapter.approve_selected_tools(
        "benchling",
        ["search_entities"],
        approved_by="admin-1",
        approver_roles={"admin"},
    )
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(adapter.registry)
    tool_name = "mcp.benchling.search_entities"
    executor = RuntimeActionExecutor(
        registry=runtime_registry,
        tool_handlers={tool_name: adapter._handler("benchling", tool_name)},  # noqa: SLF001
    )

    result = executor.execute(
        _plan(runtime_registry, tool_name, user_permissions=["benchling:read"]),
        mode="execute_safe_tools",
        actor="codex",
        approvals=set(),
    )

    assert result.status == "succeeded"
    assert result.results[0].output["records"][0]["id"] == "entity-1"
    assert server.calls == [("search_entities", {"goal": "Run tool"})]
    assert adapter.registry.usage_records[0].tool_name == tool_name


def test_plugin_output_validation_failure_blocks_result() -> None:
    governed = ToolRegistryV2(register_builtins=False)
    spec = _tool("plugin.qc.fake_evidence_summary", required_permissions=["qc:run"])
    _register_package(governed, spec, status="approved")
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(governed)
    executor = RuntimeActionExecutor(
        registry=runtime_registry,
        tool_handlers={
            spec.tool_name: lambda step, tool_spec: {
                "status": "succeeded",
                "output": {
                    "summary": "Supported by PMID:12345678 with IC50 = 4 nM."
                },
            }
        },
    )

    result = executor.execute(
        _plan(runtime_registry, spec.tool_name, user_permissions=["qc:run"]),
        mode="execute_safe_tools",
        actor="codex",
        approvals=set(),
    )

    assert result.status == "failed"
    assert result.results[0].status == "validation_failed"
    assert "fake citation" in " ".join(result.results[0].warnings)
    assert governed.usage_records[0].status == "validation_failed"


def test_tool_version_appears_in_audit_and_usage() -> None:
    governed = ToolRegistryV2(register_builtins=False)
    spec = _tool("plugin.qc.audit_version", required_permissions=["qc:run"])
    package = _register_package(governed, spec, status="approved", version="2.3.4")
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(governed)
    executor = RuntimeActionExecutor(
        registry=runtime_registry,
        tool_handlers={
            spec.tool_name: lambda step, tool_spec: {
                "status": "succeeded",
                "output": {"summary": "Versioned tool completed."},
            }
        },
    )

    result = executor.execute(
        _plan(runtime_registry, spec.tool_name, user_permissions=["qc:run"]),
        mode="execute_safe_tools",
        actor="codex",
        approvals=set(),
    )

    step_succeeded = next(
        event for event in result.audit_events if event.event_type == "runtime_step_succeeded"
    )
    assert step_succeeded.metadata["package_id"] == package.package_id
    assert step_succeeded.metadata["tool_version"] == "2.3.4"
    assert governed.usage_records[0].package_id == package.package_id
    assert governed.usage_records[0].tool_version == "2.3.4"


class FakeCodexPlannerClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str:
        del sandbox_mode, jsonl_output_path
        self.prompts.append(prompt)
        return json.dumps(self.payload)


class FakeMCPServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_entities",
                "description": "Search Benchling entities.",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
                "required_permissions": ["benchling:read"],
                "side_effect_level": "external_read",
                "policy_tags": ["codex_visible"],
            }
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        return []

    def list_prompts(self) -> list[dict[str, Any]]:
        return [{"name": "safe_summary", "body": "Summarize approved artifacts."}]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {
            "status": "succeeded",
            "output": {"records": [{"id": "entity-1"}]},
            "artifact_ids": [],
            "metadata": {},
        }


def _register_package(
    registry: ToolRegistryV2,
    spec: RuntimeToolSpec,
    *,
    status: str,
    version: str = "1.0.0",
) -> ToolPackage:
    manifest = _manifest(spec, version=version)
    package = _package(manifest, status=status, version=version)
    registry.register_tool_package(package, manifest)
    return package


def _tool(
    tool_name: str,
    *,
    description: str = "Approved plugin tool.",
    required_permissions: list[str],
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="plugin",
        description=description,
        input_schema={
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "required": ["goal"],
            "additionalProperties": True,
        },
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": True,
        },
        required_permissions=required_permissions,
        policy_tags=["codex_visible"],
        side_effect_level="artifact_write",
        requires_approval_by_default=False,
        idempotent=False,
        metadata={"deterministic_entrypoint": f"{tool_name}.handler"},
    )


def _manifest(spec: RuntimeToolSpec, *, version: str) -> ToolManifest:
    return ToolManifest(
        manifest_id=f"{spec.tool_name}-manifest",
        package_id=f"{spec.tool_name.replace('.', '-')}-pack",
        package_name=f"{spec.tool_name.replace('.', '-')}-pack",
        package_version=version,
        tools=[spec],
        skills=[],
        workflows=[],
        required_permissions=list(spec.required_permissions),
        requested_filesystem_access=[],
        requested_network_access=[],
        requested_environment_variables=[],
        external_domains=[],
        side_effect_summary={spec.side_effect_level: 1},
        scientific_guardrail_tags=["no_direct_evidence_creation"],
        license=None,
        metadata={},
    )


def _package(manifest: ToolManifest, *, status: str, version: str) -> ToolPackage:
    metadata = (
        {"security_scan_status": "passed", "approval_status": "approved"}
        if status == "approved"
        else {}
    )
    return ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name=manifest.package_name,
        description="Runtime integration test package.",
        package_type="plugin",
        version=version,
        publisher="internal",
        source="internal_registry",
        status=status,  # type: ignore[arg-type]
        tool_count=len(manifest.tools),
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata=metadata,
    )


def _plan(
    registry: RuntimeToolRegistry,
    tool_name: str,
    *,
    user_permissions: list[str],
) -> RuntimeActionPlan:
    spec = registry.require(tool_name)
    step = RuntimeActionStep(
        step_id=f"step-{tool_name}",
        plan_id="plan-1",
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args={"goal": "Run tool"},
        requires_approval=False,
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={},
    )
    return RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Run tool",
        plan_summary="Run one tool",
        steps=[step],
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="codex",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                tool_name: {
                    "required_permissions": spec.required_permissions,
                    "side_effect_level": spec.side_effect_level,
                    "policy_tags": spec.policy_tags,
                    "tool_package": spec.metadata.get("tool_package"),
                    "tool_policy": spec.metadata.get("tool_policy"),
                }
            },
            "runtime_context": {
                "project_id": "project-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "user_permissions": user_permissions,
            },
        },
    )


def _plan_payload(tool_name: str) -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "session_id": "session-1",
        "user_goal": "Run tool",
        "plan_summary": "Run one tool",
        "steps": [
            {
                "step_id": "step-1",
                "plan_id": "plan-1",
                "step_index": 0,
                "action_type": tool_name,
                "tool_name": tool_name,
                "tool_args": {"goal": "Run tool"},
                "requires_approval": False,
                "approval_reason": None,
                "expected_outputs": [],
                "status": "pending",
                "result_id": None,
                "warnings": [],
                "metadata": {},
            }
        ],
        "required_approvals": [],
        "expected_artifacts": [],
        "risk_level": "low",
        "guardrail_warnings": [],
        "created_by": "codex",
        "validated": False,
        "validation_errors": [],
        "metadata": {},
    }
