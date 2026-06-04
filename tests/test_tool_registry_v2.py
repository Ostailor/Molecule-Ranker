from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.registry import (
    ToolRegistryV2,
    ToolRegistryV2Error,
    hash_manifest,
    hash_schema,
)
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage, ToolVersion

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_register_builtin_tools_uses_namespace_qualified_names_and_keeps_legacy_aliases() -> None:
    registry = ToolRegistryV2.default()

    ranked = registry.resolve_tool("builtins.ranking.run_ranking")
    legacy = registry.resolve_tool("run_ranking")
    visible = registry.list_tools_visible_to_user(user_permissions={"run:create", "graph:build"})
    runtime_registry = registry.to_runtime_tool_registry()

    assert ranked.tool_name == "builtins.ranking.run_ranking"
    assert legacy.tool_name == "builtins.ranking.run_ranking"
    assert registry.resolve_tool("builtins.graph.build_graph").tool_name == (
        "builtins.graph.build_graph"
    )
    assert "builtins.ranking.run_ranking" in {tool.tool_name for tool in visible}
    assert runtime_registry.require("builtins.ranking.run_ranking").required_permissions == [
        "run:create"
    ]


def test_register_package_in_quarantine_does_not_activate_tools() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _external_tool("mcp.benchling.search_entities")
    manifest = _manifest([spec])
    package = _package(status="quarantined", manifest=manifest)

    registry.register_tool_package(package, manifest)

    assert (package.package_id, package.version) in registry.quarantined_packages
    assert registry.list_tools_visible_to_user(user_permissions={"benchling:read"}) == []
    with pytest.raises(KeyError, match="not active"):
        registry.resolve_tool("mcp.benchling.search_entities")


def test_approved_package_activates_tools_and_tracks_usage() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _external_tool("mcp.benchling.search_entities")
    manifest = _manifest([spec])
    package = _package(status="approved", manifest=manifest)

    registry.register_tool_package(package, manifest)
    resolved = registry.resolve_tool("mcp.benchling.search_entities", version="1.0.0")
    usage = registry.track_usage(
        package_id=package.package_id,
        tool_name=resolved.tool_name,
        tool_version="1.0.0",
        invoked_by="codex",
        status="succeeded",
        artifact_ids=["artifact-1"],
    )

    assert resolved.metadata["tool_package"]["approval_status"] == "approved"
    assert resolved.metadata["tool_version"]["input_schema_hash"] == hash_schema(
        spec.input_schema
    )
    assert usage.usage_id.startswith("tool-usage-")
    assert registry.usage_records == [usage]


def test_disabled_tool_cannot_execute_or_be_listed() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _external_tool("plugin.example.tool_name")
    manifest = _manifest([spec], package_id="plugin-example", package_name="plugin-example")
    package = _package(
        status="approved",
        manifest=manifest,
        package_id="plugin-example",
        name="plugin-example",
    )
    registry.register_tool_package(package, manifest)

    registry.disable_tool("plugin.example.tool_name")

    assert registry.list_tools_visible_to_user(user_permissions={"plugin:run"}) == []
    with pytest.raises(KeyError, match="not active"):
        registry.resolve_tool("plugin.example.tool_name")
    with pytest.raises(ToolRegistryV2Error, match="disabled tool"):
        registry.track_usage(
            package_id=package.package_id,
            tool_name="plugin.example.tool_name",
            tool_version="1.0.0",
            invoked_by="workflow",
            status="succeeded",
        )


def test_schema_hash_mismatch_detected() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _external_tool("mcp.benchling.search_entities")
    manifest = _manifest([spec])
    package = _package(status="approved", manifest=manifest)
    wrong_version = ToolVersion(
        tool_version_id="wrong-version",
        package_id=package.package_id,
        tool_name=spec.tool_name,
        version=package.version,
        input_schema_hash="sha256:wrong",
        output_schema_hash=hash_schema(spec.output_schema),
        implementation_hash=None,
        status="active",
        created_at=NOW,
        metadata={},
    )

    with pytest.raises(ToolRegistryV2Error, match="input schema hash mismatch"):
        registry.register_tool_package(package, manifest, versions=[wrong_version])


def test_tool_schema_changes_require_new_version() -> None:
    registry = ToolRegistryV2(register_builtins=False)
    spec = _external_tool("mcp.benchling.search_entities")
    manifest = _manifest([spec])
    package = _package(status="approved", manifest=manifest)
    registry.register_tool_package(package, manifest)
    changed_spec = spec.model_copy(
        update={
            "input_schema": {
                "type": "object",
                "additionalProperties": True,
                "required": ["query"],
            }
        }
    )
    changed_manifest = _manifest([changed_spec], package_id="benchling-tools-v2")
    changed_package = _package(
        status="approved",
        manifest=changed_manifest,
        package_id="benchling-tools-v2",
    )

    with pytest.raises(ToolRegistryV2Error, match="schema hash mismatch"):
        registry.register_tool_package(changed_package, changed_manifest)

    new_version_package = _package(
        status="approved",
        manifest=changed_manifest,
        package_id="benchling-tools-v2",
        version="1.1.0",
    )
    new_version_manifest = changed_manifest.model_copy(update={"package_version": "1.1.0"})
    new_version_package = new_version_package.model_copy(
        update={"manifest_hash": hash_manifest(new_version_manifest)}
    )
    registry.register_tool_package(new_version_package, new_version_manifest)

    assert registry.resolve_tool("mcp.benchling.search_entities").metadata["tool_version"][
        "version"
    ] == "1.1.0"


def _external_tool(tool_name: str) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="integration",
        description="Search external entities.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=["benchling:read"]
        if tool_name.startswith("mcp.")
        else ["plugin:run"],
        policy_tags=["external_read"],
        side_effect_level="external_read",
        requires_approval_by_default=False,
        idempotent=True,
        metadata={"deterministic_entrypoint": "example.search"},
    )


def _manifest(
    tools: list[RuntimeToolSpec],
    *,
    package_id: str = "benchling-tools",
    package_name: str = "benchling-tools",
    version: str = "1.0.0",
) -> ToolManifest:
    return ToolManifest(
        manifest_id=f"{package_id}-manifest",
        package_id=package_id,
        package_name=package_name,
        package_version=version,
        tools=tools,
        skills=[],
        workflows=[],
        required_permissions=sorted(
            {permission for tool in tools for permission in tool.required_permissions}
        ),
        requested_filesystem_access=[],
        requested_network_access=[{"domain": "benchling.internal"}],
        requested_environment_variables=[],
        external_domains=["benchling.internal"],
        side_effect_summary={"external_read": len(tools)},
        scientific_guardrail_tags=["no_evidence_creation"],
        license=None,
        metadata={},
    )


def _package(
    *,
    status: str,
    manifest: ToolManifest,
    package_id: str = "benchling-tools",
    name: str = "benchling-tools",
    version: str = "1.0.0",
) -> ToolPackage:
    metadata = (
        {"security_scan_status": "passed", "approval_status": "approved"}
        if status == "approved"
        else {}
    )
    return ToolPackage(
        package_id=package_id,
        name=name,
        display_name=name,
        description="Example governed tool pack.",
        package_type="mcp_server" if package_id.startswith("benchling") else "plugin",
        version=version,
        publisher="example",
        source="internal_registry",
        status=status,  # type: ignore[arg-type]
        tool_count=len(manifest.tools),
        skill_count=len(manifest.skills),
        workflow_count=len(manifest.workflows),
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata=metadata,
    )
