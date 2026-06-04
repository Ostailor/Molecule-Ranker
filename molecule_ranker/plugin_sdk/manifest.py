from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from molecule_ranker.plugin_sdk.skill import PluginSkill
from molecule_ranker.plugin_sdk.tool import PluginTool
from molecule_ranker.plugin_sdk.validators import PluginValidationResult, validate_package
from molecule_ranker.plugin_sdk.workflow import PluginWorkflow
from molecule_ranker.tool_ecosystem.registry import hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage


class PluginPackageBundle(BaseModel):
    package: ToolPackage
    manifest: ToolManifest
    tools: list[PluginTool]
    skills: list[PluginSkill]
    workflows: list[PluginWorkflow]

    def validate_package(self) -> PluginValidationResult:
        return validate_package(self.package, self.manifest, tools=self.tools)


def build_manifest(
    *,
    package_id: str,
    name: str,
    display_name: str | None = None,
    description: str,
    version: str,
    publisher: str,
    tools: list[PluginTool] | None = None,
    skills: list[PluginSkill] | None = None,
    workflows: list[PluginWorkflow] | None = None,
    package_type: str = "plugin",
    source: str = "local",
    license: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PluginPackageBundle:
    plugin_tools = tools or []
    plugin_skills = skills or []
    plugin_workflows = workflows or []
    runtime_specs = [tool.to_runtime_tool_spec(package_name=name) for tool in plugin_tools]
    manifest = ToolManifest(
        manifest_id=f"{package_id}-manifest",
        package_id=package_id,
        package_name=name,
        package_version=version,
        tools=runtime_specs,
        skills=[skill.model_dump(mode="json") for skill in plugin_skills],
        workflows=[workflow.model_dump(mode="json") for workflow in plugin_workflows],
        required_permissions=sorted(
            {permission for spec in runtime_specs for permission in spec.required_permissions}
        ),
        requested_filesystem_access=[
            access for tool in plugin_tools for access in tool.filesystem_access
        ],
        requested_network_access=[
            access for tool in plugin_tools for access in tool.network_access
        ],
        requested_environment_variables=sorted(
            {env for tool in plugin_tools for env in tool.environment_variables}
        ),
        external_domains=sorted(
            {domain for tool in plugin_tools for domain in tool.external_domains}
        ),
        side_effect_summary=_side_effect_summary(plugin_tools),
        scientific_guardrail_tags=sorted(
            {tag for tool in plugin_tools for tag in tool.policy_tags}
        ),
        license=license,
        metadata=metadata or {},
    )
    package = ToolPackage(
        package_id=package_id,
        name=name,
        display_name=display_name or name,
        description=description,
        package_type=package_type,  # type: ignore[arg-type]
        version=version,
        publisher=publisher,
        source=source,  # type: ignore[arg-type]
        status="discovered",
        tool_count=len(plugin_tools),
        skill_count=len(plugin_skills),
        workflow_count=len(plugin_workflows),
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={},
    )
    return PluginPackageBundle(
        package=package,
        manifest=manifest,
        tools=plugin_tools,
        skills=plugin_skills,
        workflows=plugin_workflows,
    )


def _side_effect_summary(tools: list[PluginTool]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for tool in tools:
        summary[tool.side_effect_level] = summary.get(tool.side_effect_level, 0) + 1
    return summary


__all__ = ["PluginPackageBundle", "build_manifest"]
