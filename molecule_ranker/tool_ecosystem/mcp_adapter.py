from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.schemas import RuntimeActionStep, RuntimeToolSpec
from molecule_ranker.tool_ecosystem.mcp_gateway import (
    InternalMCPGateway,
    MCPGatewayContext,
    MCPGatewayResult,
)
from molecule_ranker.tool_ecosystem.policy import ToolPolicyContext, ToolPolicyEngine
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2, hash_manifest
from molecule_ranker.tool_ecosystem.schemas import (
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
)
from molecule_ranker.tool_ecosystem.security import has_blocking_findings, scan_tool_package

MCPServerTransport = Literal["stdio", "http", "sse", "in_memory"]
MCPServerStatus = Literal["registered", "disabled", "quarantined", "scanned", "approved"]


class MCPAdapterError(ValueError):
    """Raised when MCP adapter governance prevents an operation."""


class MCPServerConfig(BaseModel):
    server_id: str
    name: str
    description: str = ""
    transport: MCPServerTransport = "in_memory"
    command: list[str] = Field(default_factory=list)
    endpoint_url: str | None = None
    enabled: bool = False
    approved: bool = False
    package_version: str = "1.0.0"
    required_permissions: list[str] = Field(default_factory=lambda: ["tool:read"])
    allowed_network_domains: list[str] = Field(default_factory=list)
    sandbox_profile: str = "tool_read_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPToolDescriptor(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    required_permissions: list[str] = Field(default_factory=lambda: ["tool:read"])
    side_effect_level: str = "external_read"
    requires_approval: bool = False
    policy_tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPResourceDescriptor(BaseModel):
    uri: str
    name: str
    project_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=lambda: ["artifact:read"])
    approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPPromptDescriptor(BaseModel):
    name: str
    description: str = ""
    body: str = ""
    required_permissions: list[str] = Field(default_factory=lambda: ["tool:read"])
    approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPIntrospectionResult(BaseModel):
    server_id: str
    tools: list[MCPToolDescriptor] = Field(default_factory=list)
    resources: list[MCPResourceDescriptor] = Field(default_factory=list)
    prompts: list[MCPPromptDescriptor] = Field(default_factory=list)
    manifest: ToolManifest
    package: ToolPackage


class MCPServerClient(Protocol):
    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def list_resources(self) -> list[dict[str, Any]]:
        ...

    def list_prompts(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...


class MCPServerAdapter:
    """Governed adapter for importing MCP server tools into molecule-ranker."""

    def __init__(
        self,
        *,
        registry: ToolRegistryV2 | None = None,
        policy_engine: ToolPolicyEngine | None = None,
        clients: dict[str, MCPServerClient] | None = None,
    ) -> None:
        self.registry = registry or ToolRegistryV2(register_builtins=False)
        self.policy_engine = policy_engine or ToolPolicyEngine.default()
        self.clients = clients or {}
        self.server_configs: dict[str, MCPServerConfig] = {}
        self.packages: dict[str, ToolPackage] = {}
        self.manifests: dict[str, ToolManifest] = {}
        self.scans: dict[str, ToolSecurityScan] = {}
        self.approvals: dict[str, ToolApproval] = {}
        self.resources: dict[str, list[MCPResourceDescriptor]] = {}
        self.prompts: dict[str, list[MCPPromptDescriptor]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.approved_tool_names: dict[str, set[str]] = {}

    def register_server_config(
        self,
        config: MCPServerConfig,
        *,
        client: MCPServerClient | None = None,
    ) -> MCPServerConfig:
        if config.server_id in self.server_configs:
            raise MCPAdapterError(f"MCP server already registered: {config.server_id}")
        if client is not None:
            self.clients[config.server_id] = client
        self.server_configs[config.server_id] = config
        self._audit("mcp_server_registered", config.server_id, "MCP server config registered.")
        return config

    def introspect_server(self, server_id: str) -> MCPIntrospectionResult:
        config = self._config(server_id)
        client = self._client(server_id)
        tools = [MCPToolDescriptor.model_validate(tool) for tool in client.list_tools()]
        resources = [
            MCPResourceDescriptor.model_validate(resource)
            for resource in client.list_resources()
        ]
        prompts = [
            MCPPromptDescriptor.model_validate(prompt) for prompt in client.list_prompts()
        ]
        manifest = self._manifest_from_introspection(config, tools, resources, prompts)
        package = ToolPackage(
            package_id=_package_id(config.server_id),
            name=config.name,
            display_name=config.name,
            description=config.description or f"MCP server {config.server_id}.",
            package_type="mcp_server",
            version=config.package_version,
            publisher=str(config.metadata.get("publisher") or "mcp"),
            source="internal_registry",
            status="quarantined",
            tool_count=len(manifest.tools),
            skill_count=0,
            workflow_count=0,
            manifest_hash=hash_manifest(manifest),
            package_hash=None,
            created_at=_now(),
            updated_at=_now(),
            metadata={
                "mcp_server_id": config.server_id,
                "mcp_status": "quarantined",
                "security_scan_status": "pending",
            },
        )
        manifest = manifest.model_copy(update={"package_id": package.package_id})
        package = package.model_copy(update={"manifest_hash": hash_manifest(manifest)})
        self.packages[server_id] = package
        self.manifests[server_id] = manifest
        self.resources[server_id] = resources
        self.prompts[server_id] = prompts
        self.registry.register_tool_package(package, manifest)
        self._audit("mcp_server_introspected", server_id, "MCP server tools quarantined.")
        return MCPIntrospectionResult(
            server_id=server_id,
            tools=tools,
            resources=resources,
            prompts=prompts,
            manifest=manifest,
            package=package,
        )

    def scan_server_manifest(self, server_id: str) -> ToolSecurityScan:
        package, manifest = self._package_manifest(server_id)
        scan = scan_tool_package(package, manifest)
        self.scans[server_id] = scan
        updated = package.model_copy(
            update={
                "status": "scanned" if scan.status in {"passed", "warning"} else "quarantined",
                "updated_at": _now(),
                "metadata": {
                    **package.metadata,
                    "security_scan_status": scan.status,
                    "mcp_status": "scanned"
                    if scan.status in {"passed", "warning"}
                    else "quarantined",
                },
            }
        )
        self.packages[server_id] = updated
        key = (updated.package_id, updated.version)
        self.registry.packages[key] = updated
        self._audit("mcp_server_scanned", server_id, f"MCP server scan {scan.status}.")
        return scan

    def approve_selected_tools(
        self,
        server_id: str,
        tool_names: list[str],
        *,
        approved_by: str,
        approver_roles: set[str],
        rationale: str = "Approved selected MCP tools.",
    ) -> ToolApproval:
        config = self._config(server_id)
        if not config.enabled:
            raise MCPAdapterError("MCP server is disabled by default; enable before approval.")
        if not config.approved:
            raise MCPAdapterError("Only approved MCP servers can be used.")
        package, manifest = self._package_manifest(server_id)
        scan = self.scans.get(server_id)
        if scan is None:
            raise MCPAdapterError("MCP manifest must be scanned before approval.")
        if has_blocking_findings(scan):
            raise MCPAdapterError("MCP manifest has critical security findings.")
        requested = {_qualified_tool_name(server_id, name) for name in tool_names}
        selected_tools = [tool for tool in manifest.tools if tool.tool_name in requested]
        if not selected_tools:
            raise MCPAdapterError("No selected MCP tools match introspected manifest.")
        selected_manifest = manifest.model_copy(
            update={
                "tools": selected_tools,
                "required_permissions": sorted(
                    {
                        permission
                        for tool in selected_tools
                        for permission in tool.required_permissions
                    }
                ),
                "side_effect_summary": _side_effect_summary(selected_tools),
            }
        )
        approved_package = package.model_copy(
            update={
                "status": "approved",
                "tool_count": len(selected_tools),
                "manifest_hash": hash_manifest(selected_manifest),
                "updated_at": _now(),
                "metadata": {
                    **package.metadata,
                    "approval_status": "approved",
                    "security_scan_status": scan.status,
                    "mcp_status": "approved",
                },
            }
        )
        policy_decision = self.policy_engine.can_approve_package(
            approved_package,
            scan=scan,
            context=ToolPolicyContext(
                user_id=approved_by,
                approval_actor_user_id=approved_by,
                approval_actor_roles=approver_roles,
            ),
        )
        if not policy_decision.allowed:
            raise MCPAdapterError("; ".join(policy_decision.reasons))
        approval = ToolApproval(
            approval_id=f"mcp-approval-{uuid4().hex[:12]}",
            package_id=approved_package.package_id,
            package_version=approved_package.version,
            approved_by=approved_by,
            approval_status="approved",
            rationale=rationale,
            approved_permissions=list(selected_manifest.required_permissions),
            approved_filesystem_profile=config.sandbox_profile,
            approved_network_domains=list(config.allowed_network_domains),
            approved_at=_now(),
            expires_at=None,
            metadata={"mcp_server_id": server_id, "approved_tools": sorted(requested)},
        )
        key = (approved_package.package_id, approved_package.version)
        self.packages[server_id] = approved_package
        self.manifests[server_id] = selected_manifest
        self.approvals[server_id] = approval
        self.approved_tool_names[server_id] = requested
        self.registry.packages[key] = approved_package
        self.registry.manifests[key] = selected_manifest
        self.registry.quarantined_packages.discard(key)
        self.registry.activate_approved_package(
            approved_package.package_id,
            approved_package.version,
        )
        self._audit("mcp_tools_approved", server_id, "Selected MCP tools approved.")
        return approval

    def list_approved_tools(self, server_id: str) -> list[RuntimeToolSpec]:
        package, manifest = self._package_manifest(server_id)
        if package.status != "approved":
            return []
        approved = self.approved_tool_names.get(server_id, set())
        return [tool for tool in manifest.tools if tool.tool_name in approved]

    def list_resources(self, server_id: str, context: MCPGatewayContext) -> list[dict[str, Any]]:
        self._require_server_usable(server_id)
        visible: list[dict[str, Any]] = []
        for resource in self.resources.get(server_id, []):
            if not resource.approved:
                continue
            if resource.project_ids and context.project_id not in set(resource.project_ids):
                continue
            if not set(resource.required_permissions).issubset(context.user_permissions):
                continue
            visible.append(resource.model_dump(mode="json"))
        return visible

    def list_prompts(self, server_id: str, context: MCPGatewayContext) -> list[dict[str, Any]]:
        self._require_server_usable(server_id)
        visible: list[dict[str, Any]] = []
        for prompt in self.prompts.get(server_id, []):
            if not prompt.approved:
                continue
            if not set(prompt.required_permissions).issubset(context.user_permissions):
                continue
            visible.append(prompt.model_dump(mode="json"))
        return visible

    def execute_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: MCPGatewayContext,
    ) -> MCPGatewayResult:
        self._require_server_usable(server_id)
        qualified_name = (
            tool_name
            if tool_name.startswith("mcp.")
            else _qualified_tool_name(server_id, tool_name)
        )
        if qualified_name not in self.approved_tool_names.get(server_id, set()):
            raise MCPAdapterError("Only approved MCP tools can be used.")
        config = self._config(server_id)
        if not _network_allowed(config):
            raise MCPAdapterError("MCP external network access must be allowlisted.")
        gateway = InternalMCPGateway(
            registry=self.registry,
            tool_handlers={qualified_name: self._handler(server_id, qualified_name)},
        )
        result = gateway.tools_call(qualified_name, arguments, context)
        self.audit_events.extend(
            event.model_dump(mode="json") for event in result.audit_events
        )
        self._audit("mcp_tool_executed", server_id, f"MCP tool call {result.status}.")
        return result

    def _manifest_from_introspection(
        self,
        config: MCPServerConfig,
        tools: list[MCPToolDescriptor],
        resources: list[MCPResourceDescriptor],
        prompts: list[MCPPromptDescriptor],
    ) -> ToolManifest:
        runtime_tools = [mcp_tool_to_runtime_spec(config, tool) for tool in tools]
        return ToolManifest(
            manifest_id=f"mcp-manifest-{config.server_id}",
            package_id=_package_id(config.server_id),
            package_name=config.name,
            package_version=config.package_version,
            tools=runtime_tools,
            skills=[],
            workflows=[],
            required_permissions=sorted(
                {
                    permission
                    for tool in runtime_tools
                    for permission in tool.required_permissions
                }
            ),
            requested_filesystem_access=[],
            requested_network_access=[
                {"mode": "read", "domain": domain}
                for domain in config.allowed_network_domains
            ],
            requested_environment_variables=[],
            external_domains=list(config.allowed_network_domains),
            side_effect_summary=_side_effect_summary(runtime_tools),
            scientific_guardrail_tags=["mcp_tools_require_gateway_validation"],
            license=None,
            metadata={
                "mcp_server_id": config.server_id,
                "mcp_resources": [resource.model_dump(mode="json") for resource in resources],
                "mcp_prompts": [prompt.model_dump(mode="json") for prompt in prompts],
                "mcp_servers_disabled_by_default": True,
            },
        )

    def _handler(self, server_id: str, qualified_name: str):
        raw_tool_name = _raw_tool_name(server_id, qualified_name)

        def call(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
            return self._client(server_id).call_tool(raw_tool_name, dict(step.tool_args))

        return call

    def _require_server_usable(self, server_id: str) -> None:
        config = self._config(server_id)
        if not config.enabled:
            raise MCPAdapterError("MCP servers are disabled by default.")
        if not config.approved:
            raise MCPAdapterError("Only approved MCP servers can be used.")
        package, _manifest = self._package_manifest(server_id)
        if package.status != "approved":
            raise MCPAdapterError("Only approved MCP tools can be used.")

    def _config(self, server_id: str) -> MCPServerConfig:
        try:
            return self.server_configs[server_id]
        except KeyError as exc:
            raise MCPAdapterError(f"Unknown MCP server: {server_id}") from exc

    def _client(self, server_id: str) -> MCPServerClient:
        try:
            return self.clients[server_id]
        except KeyError as exc:
            raise MCPAdapterError(f"No MCP client registered for server: {server_id}") from exc

    def _package_manifest(self, server_id: str) -> tuple[ToolPackage, ToolManifest]:
        try:
            return self.packages[server_id], self.manifests[server_id]
        except KeyError as exc:
            raise MCPAdapterError("MCP server must be introspected first.") from exc

    def _audit(self, event_type: str, server_id: str, summary: str) -> None:
        self.audit_events.append(
            {
                "event_type": event_type,
                "server_id": server_id,
                "summary": summary,
                "timestamp": _now().isoformat(),
            }
        )


def mcp_tool_to_runtime_spec(
    config: MCPServerConfig,
    tool: MCPToolDescriptor | dict[str, Any],
) -> RuntimeToolSpec:
    parsed = tool if isinstance(tool, MCPToolDescriptor) else MCPToolDescriptor.model_validate(tool)
    side_effect = parsed.side_effect_level
    return RuntimeToolSpec(
        tool_name=_qualified_tool_name(config.server_id, parsed.name),
        category="mcp",
        description=parsed.description or f"MCP tool {parsed.name}.",
        input_schema=_json_object_schema(parsed.input_schema),
        output_schema=_json_object_schema(parsed.output_schema),
        required_permissions=parsed.required_permissions or config.required_permissions,
        policy_tags=list(parsed.policy_tags),
        side_effect_level=side_effect,  # type: ignore[arg-type]
        requires_approval_by_default=parsed.requires_approval
        or side_effect == "external_write",
        idempotent=side_effect in {"none", "external_read"},
        metadata={
            **parsed.metadata,
            "mcp_server_id": config.server_id,
            "mcp_tool_name": parsed.name,
            "tool_policy": {
                "required_permissions": parsed.required_permissions
                or config.required_permissions,
                "sandbox_profile": config.sandbox_profile,
                "network_allowlist": list(config.allowed_network_domains),
            },
        },
    )


def _json_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "object":
        return schema
    return {"type": "object", "additionalProperties": True, "metadata": {"wrapped": schema}}


def _qualified_tool_name(server_id: str, tool_name: str) -> str:
    return f"mcp.{_safe_identifier(server_id)}.{_safe_identifier(tool_name)}"


def _raw_tool_name(server_id: str, qualified_name: str) -> str:
    prefix = f"mcp.{_safe_identifier(server_id)}."
    return qualified_name.removeprefix(prefix)


def _package_id(server_id: str) -> str:
    return f"mcp-server-{_safe_identifier(server_id)}"


def _safe_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "unnamed"


def _side_effect_summary(tools: list[RuntimeToolSpec]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for tool in tools:
        summary[tool.side_effect_level] = summary.get(tool.side_effect_level, 0) + 1
    return summary


def _network_allowed(config: MCPServerConfig) -> bool:
    if config.transport == "in_memory":
        return True
    if config.endpoint_url and not config.allowed_network_domains:
        return False
    return "*" not in set(config.allowed_network_domains)


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "MCPAdapterError",
    "MCPIntrospectionResult",
    "MCPPromptDescriptor",
    "MCPResourceDescriptor",
    "MCPServerAdapter",
    "MCPServerClient",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPServerTransport",
    "MCPToolDescriptor",
    "mcp_tool_to_runtime_spec",
]
