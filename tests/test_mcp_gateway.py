from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionStep,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.tool_ecosystem.mcp_gateway import InternalMCPGateway, MCPGatewayContext
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2, hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_approved_tool_listed() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _tool("mcp.benchling.search_entities")
    _register_package(registry, spec, status="approved")
    gateway = InternalMCPGateway(registry=registry)

    response = gateway.tools_list(_context({"benchling:read"}))

    assert [tool["name"] for tool in response["tools"]] == ["mcp.benchling.search_entities"]
    assert "api_key" not in str(response["tools"]).lower()
    assert ".cache" not in str(response["tools"]).lower()


def test_unapproved_tool_hidden() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _tool("mcp.benchling.search_entities")
    _register_package(registry, spec, status="quarantined")
    gateway = InternalMCPGateway(registry=registry)

    response = gateway.tools_list(_context({"benchling:read"}))

    assert response["tools"] == []


def test_unauthorized_call_blocked() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _tool("mcp.benchling.search_entities")
    _register_package(registry, spec, status="approved")
    gateway = InternalMCPGateway(
        registry=registry,
        tool_handlers={spec.tool_name: _ok_handler},
    )

    result = gateway.tools_call(spec.tool_name, {"query": "BRCA1"}, _context(set()))

    assert result.status == "policy_blocked"
    assert "Unauthorized" in (result.error_summary or "")
    assert gateway.audit_events


def test_external_write_returns_approval_required() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _tool(
        "mcp.benchling.sync_write",
        side_effect_level="external_write",
        permission="benchling:write",
        requires_approval=True,
    )
    _register_package(registry, spec, status="approved", package_id="benchling-write-tools")
    gateway = InternalMCPGateway(
        registry=registry,
        tool_handlers={spec.tool_name: _ok_handler},
    )

    result = gateway.tools_call(
        spec.tool_name,
        {"record_id": "rec-1"},
        _context({"benchling:write"}),
    )

    assert result.status == "approval_required"
    assert result.error_summary == "External write requires approval."


def test_fake_evidence_output_blocked() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _tool("mcp.benchling.search_entities")
    _register_package(registry, spec, status="approved")
    gateway = InternalMCPGateway(
        registry=registry,
        tool_handlers={spec.tool_name: _fake_evidence_handler},
    )

    result = gateway.tools_call(spec.tool_name, {"query": "BRCA1"}, _context({"benchling:read"}))

    assert result.status == "validation_failed"
    assert "fake citation" in " ".join(result.warnings)
    assert result.usage_record is not None
    assert result.usage_record.status == "validation_failed"
    assert registry.usage_records == [result.usage_record]


def test_resources_and_prompts_only_return_approved_safe_entries() -> None:
    gateway = InternalMCPGateway(
        registry=ToolRegistryV2(register_builtins=False),
        approved_artifacts=[
            {
                "artifact_id": "artifact-1",
                "approved": True,
                "project_ids": ["project-1"],
                "required_permissions": ["artifact:read"],
                "path": "/artifacts/ranking.json",
            },
            {
                "artifact_id": "secret-1",
                "approved": True,
                "project_ids": ["project-1"],
                "required_permissions": ["artifact:read"],
                "path": "/secrets/token.txt",
            },
        ],
        approved_prompt_templates=[
            {
                "prompt_id": "prompt-1",
                "approved": True,
                "project_ids": ["project-1"],
                "required_permissions": ["prompt:read"],
                "body": "Summarize approved artifacts.",
            },
            {
                "prompt_id": "prompt-2",
                "approved": False,
                "body": "Unapproved prompt.",
            },
        ],
    )

    resources = gateway.resources_list(_context({"artifact:read"}))["resources"]
    prompts = gateway.prompts_list(_context({"prompt:read"}))["prompts"]

    assert [resource["artifact_id"] for resource in resources] == ["artifact-1"]
    assert [prompt["prompt_id"] for prompt in prompts] == ["prompt-1"]


def _context(permissions: set[str]) -> MCPGatewayContext:
    return MCPGatewayContext(
        user_id="user-1",
        project_id="project-1",
        org_id="org-1",
        user_permissions=permissions,
        sandbox_profile="artifact_write",
    )


def _ok_handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "output": {"records": [{"id": "entity-1"}]},
        "artifact_ids": [],
        "metadata": {},
    }


def _fake_evidence_handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> RuntimeToolResult:
    return RuntimeToolResult(
        result_id="result-1",
        step_id=step.step_id,
        tool_name=step.tool_name,
        status="succeeded",
        output={"summary": "Supported by PMID:12345678 with IC50 = 4 nM."},
        artifact_ids=[],
        job_ids=[],
        error_summary=None,
        warnings=[],
        started_at=NOW,
        completed_at=NOW,
        metadata={},
    )


def _tool(
    tool_name: str,
    *,
    side_effect_level: str = "external_read",
    permission: str = "benchling:read",
    requires_approval: bool = False,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="integration",
        description="Search entities.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=[permission],
        policy_tags=["external_write"] if side_effect_level == "external_write" else [],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval,
        idempotent=side_effect_level != "external_write",
        metadata={
            "deterministic_entrypoint": "example.handler",
            "api_key": "must-not-leak",
            "cache_path": "/tmp/.cache/tool.json",
            "tool_policy": {
                "required_permissions": [permission],
                "sandbox_profile": "artifact_write",
            },
        },
    )


def _register_package(
    registry: ToolRegistryV2,
    spec: RuntimeToolSpec,
    *,
    status: str,
    package_id: str = "benchling-tools",
) -> None:
    manifest = ToolManifest(
        manifest_id=f"{package_id}-manifest",
        package_id=package_id,
        package_name=package_id,
        package_version="1.0.0",
        tools=[spec],
        skills=[],
        workflows=[],
        required_permissions=list(spec.required_permissions),
        requested_filesystem_access=[],
        requested_network_access=[],
        requested_environment_variables=[],
        external_domains=[],
        side_effect_summary={spec.side_effect_level: 1},
        scientific_guardrail_tags=[],
        license=None,
        metadata={},
    )
    package = ToolPackage(
        package_id=package_id,
        name=package_id,
        display_name=package_id,
        description="Benchling test package.",
        package_type="mcp_server",
        version="1.0.0",
        publisher="example",
        source="internal_registry",
        status=status,  # type: ignore[arg-type]
        tool_count=1,
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata={"security_scan_status": "passed", "approval_status": "approved"}
        if status == "approved"
        else {},
    )
    registry.register_tool_package(package, manifest)
