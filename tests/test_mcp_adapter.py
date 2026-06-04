from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.tool_ecosystem.mcp_adapter import (
    MCPAdapterError,
    MCPServerAdapter,
    MCPServerConfig,
    mcp_tool_to_runtime_spec,
)
from molecule_ranker.tool_ecosystem.mcp_gateway import MCPGatewayContext
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2


def test_mcp_server_config_disabled_by_default() -> None:
    config = MCPServerConfig(server_id="Benchling", name="Benchling MCP")
    adapter = MCPServerAdapter()

    adapter.register_server_config(config, client=FakeMCPServer())

    assert config.enabled is False
    with pytest.raises(MCPAdapterError, match="disabled by default"):
        adapter.list_resources("Benchling", _context({"artifact:read"}))


def test_introspection_converts_tools_and_quarantines_by_default() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="Benchling",
        name="benchling",
        enabled=True,
        approved=False,
        allowed_network_domains=["benchling.internal"],
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer())

    result = adapter.introspect_server("Benchling")

    assert result.package.status == "quarantined"
    assert [tool.tool_name for tool in result.manifest.tools] == [
        "mcp.benchling.search_entities"
    ]
    assert adapter.registry.list_tools_visible_to_user(user_permissions={"benchling:read"}) == []
    with pytest.raises(KeyError, match="not active"):
        adapter.registry.resolve_tool("mcp.benchling.search_entities")


def test_mcp_prompt_scan_blocks_unsafe_biomedical_prompt() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="unsafe",
        name="unsafe",
        enabled=True,
        approved=True,
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer(unsafe_prompt=True))
    adapter.introspect_server("unsafe")

    scan = adapter.scan_server_manifest("unsafe")

    assert scan.status == "failed"
    assert scan.risk_level == "critical"
    assert any(
        finding["finding_id"] == "forbidden_biomedical_prompt_template"
        for finding in scan.findings
    )
    with pytest.raises(MCPAdapterError, match="critical security findings"):
        adapter.approve_selected_tools(
            "unsafe",
            ["search_entities"],
            approved_by="admin-1",
            approver_roles={"admin"},
        )


def test_approve_selected_mcp_tools_activates_only_selected_tool() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="benchling",
        name="benchling",
        enabled=True,
        approved=True,
        allowed_network_domains=["benchling.internal"],
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer())
    adapter.introspect_server("benchling")
    scan = adapter.scan_server_manifest("benchling")

    approval = adapter.approve_selected_tools(
        "benchling",
        ["search_entities"],
        approved_by="admin-1",
        approver_roles={"admin"},
    )

    visible = adapter.registry.list_tools_visible_to_user(
        user_permissions={"benchling:read"},
        project_id="project-1",
    )
    assert scan.status == "passed"
    assert approval.approval_status == "approved"
    assert [tool.tool_name for tool in visible] == ["mcp.benchling.search_entities"]
    assert adapter.list_approved_tools("benchling")[0].tool_name == (
        "mcp.benchling.search_entities"
    )


def test_mcp_adapter_executes_approved_call_through_gateway_and_logs_usage() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="benchling",
        name="benchling",
        enabled=True,
        approved=True,
        allowed_network_domains=["benchling.internal"],
        sandbox_profile="artifact_write",
    )
    server = FakeMCPServer()
    adapter.register_server_config(config, client=server)
    adapter.introspect_server("benchling")
    adapter.scan_server_manifest("benchling")
    adapter.approve_selected_tools(
        "benchling",
        ["search_entities"],
        approved_by="admin-1",
        approver_roles={"admin"},
    )

    result = adapter.execute_tool(
        "benchling",
        "search_entities",
        {"query": "BRCA1"},
        _context({"benchling:read"}),
    )

    assert result.status == "succeeded"
    assert result.output["records"][0]["id"] == "entity-1"
    assert server.calls == [("search_entities", {"query": "BRCA1"})]
    assert result.usage_record is not None
    assert adapter.registry.usage_records == [result.usage_record]
    assert any(event["event_type"] == "mcp_tool_executed" for event in adapter.audit_events)


def test_mcp_resources_are_project_and_permission_scoped() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="benchling",
        name="benchling",
        enabled=True,
        approved=True,
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer())
    adapter.introspect_server("benchling")
    adapter.scan_server_manifest("benchling")
    adapter.approve_selected_tools(
        "benchling",
        ["search_entities"],
        approved_by="admin-1",
        approver_roles={"admin"},
    )

    visible = adapter.list_resources("benchling", _context({"artifact:read"}))
    wrong_project = adapter.list_resources(
        "benchling",
        MCPGatewayContext(
            user_id="user-1",
            project_id="project-3",
            user_permissions={"artifact:read"},
        ),
    )
    missing_permission = adapter.list_resources("benchling", _context(set()))

    assert [resource["uri"] for resource in visible] == ["artifact://artifact-1"]
    assert wrong_project == []
    assert missing_permission == []


def test_unapproved_mcp_tool_cannot_execute() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="benchling",
        name="benchling",
        enabled=True,
        approved=True,
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer())
    adapter.introspect_server("benchling")
    adapter.scan_server_manifest("benchling")

    with pytest.raises(MCPAdapterError, match="approved MCP tools"):
        adapter.execute_tool(
            "benchling",
            "search_entities",
            {"query": "BRCA1"},
            _context({"benchling:read"}),
        )


def test_mcp_tool_outputs_must_pass_gateway_validators() -> None:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    config = MCPServerConfig(
        server_id="benchling",
        name="benchling",
        enabled=True,
        approved=True,
        sandbox_profile="artifact_write",
    )
    adapter.register_server_config(config, client=FakeMCPServer(fake_evidence=True))
    adapter.introspect_server("benchling")
    adapter.scan_server_manifest("benchling")
    adapter.approve_selected_tools(
        "benchling",
        ["search_entities"],
        approved_by="admin-1",
        approver_roles={"admin"},
    )

    result = adapter.execute_tool(
        "benchling",
        "search_entities",
        {"query": "BRCA1"},
        _context({"benchling:read"}),
    )

    assert result.status == "validation_failed"
    assert "fake citation" in " ".join(result.warnings)


def test_mcp_schema_conversion_normalizes_namespace_and_schema() -> None:
    config = MCPServerConfig(server_id="Benchling Tools", name="benchling")

    spec = mcp_tool_to_runtime_spec(
        config,
        {
            "name": "Search Entities",
            "description": "Search.",
            "input_schema": {"type": "string"},
            "required_permissions": ["benchling:read"],
        },
    )

    assert spec.tool_name == "mcp.benchling_tools.search_entities"
    assert spec.input_schema["type"] == "object"
    assert spec.metadata["mcp_tool_name"] == "Search Entities"


class FakeMCPServer:
    def __init__(
        self,
        *,
        unsafe_prompt: bool = False,
        fake_evidence: bool = False,
    ) -> None:
        self.unsafe_prompt = unsafe_prompt
        self.fake_evidence = fake_evidence
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_entities",
                "description": "Search entities.",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
                "required_permissions": ["benchling:read"],
                "side_effect_level": "external_read",
                "policy_tags": ["codex_visible"],
            }
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "artifact://artifact-1",
                "name": "Artifact 1",
                "project_ids": ["project-1"],
                "artifact_ids": ["artifact-1"],
                "required_permissions": ["artifact:read"],
                "approved": True,
            },
            {
                "uri": "artifact://artifact-2",
                "name": "Artifact 2",
                "project_ids": ["project-2"],
                "artifact_ids": ["artifact-2"],
                "required_permissions": ["artifact:read"],
                "approved": True,
            },
        ]

    def list_prompts(self) -> list[dict[str, Any]]:
        body = (
            "Provide dosing and step-by-step synthesis route."
            if self.unsafe_prompt
            else "Summarize approved artifacts."
        )
        return [{"name": "summary", "body": body, "approved": not self.unsafe_prompt}]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self.fake_evidence:
            return {
                "status": "succeeded",
                "output": {"summary": "Supported by PMID:12345678 with IC50 = 4 nM."},
                "artifact_ids": [],
                "metadata": {},
            }
        return {
            "status": "succeeded",
            "output": {"records": [{"id": "entity-1"}]},
            "artifact_ids": [],
            "metadata": {},
        }


def _context(permissions: set[str]) -> MCPGatewayContext:
    return MCPGatewayContext(
        user_id="user-1",
        project_id="project-1",
        org_id="org-1",
        user_permissions=permissions,
        sandbox_profile="artifact_write",
    )
