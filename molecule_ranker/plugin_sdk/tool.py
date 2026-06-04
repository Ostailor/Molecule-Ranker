from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec

PluginToolKind = Literal[
    "analysis",
    "summary",
    "connector",
    "evidence_importer",
    "assay_importer",
    "generation_pipeline",
]
PluginToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class PluginTool(BaseModel):
    name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: PluginToolHandler = Field(exclude=True)
    description: str = "Plugin tool."
    kind: PluginToolKind = "analysis"
    required_permissions: list[str] = Field(default_factory=list)
    policy_tags: list[str] = Field(default_factory=list)
    side_effect_level: str = "none"
    filesystem_access: list[dict[str, Any]] = Field(default_factory=list)
    network_access: list[dict[str, Any]] = Field(default_factory=list)
    environment_variables: list[str] = Field(default_factory=list)
    external_domains: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def require_object_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("plugin tool schemas must be JSON object schemas")
        return value

    def to_runtime_tool_spec(self, *, package_name: str) -> RuntimeToolSpec:
        permission = self.required_permissions or [f"plugin:{package_name}:run"]
        return RuntimeToolSpec(
            tool_name=f"plugin.{package_name}.{self.name}",
            category="plugin",
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            required_permissions=permission,
            policy_tags=list(dict.fromkeys([*self.policy_tags, f"plugin_kind:{self.kind}"])),
            side_effect_level=self.side_effect_level,  # type: ignore[arg-type]
            requires_approval_by_default=self.side_effect_level == "external_write",
            idempotent=self.side_effect_level in {"none", "external_read"},
            metadata={
                **self.metadata,
                "plugin_tool": {
                    "name": self.name,
                    "kind": self.kind,
                    "filesystem_access": self.filesystem_access,
                    "network_access": self.network_access,
                    "environment_variables": self.environment_variables,
                    "external_domains": self.external_domains,
                },
                "deterministic_entrypoint": f"plugin.{package_name}.{self.name}",
            },
        )


def define_tool(
    name: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    handler: PluginToolHandler,
    *,
    description: str = "Plugin tool.",
    kind: PluginToolKind = "analysis",
    required_permissions: list[str] | None = None,
    policy_tags: list[str] | None = None,
    side_effect_level: str = "none",
    filesystem_access: list[dict[str, Any]] | None = None,
    network_access: list[dict[str, Any]] | None = None,
    environment_variables: list[str] | None = None,
    external_domains: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PluginTool:
    return PluginTool(
        name=name,
        input_schema=input_schema,
        output_schema=output_schema,
        handler=handler,
        description=description,
        kind=kind,
        required_permissions=required_permissions or [],
        policy_tags=policy_tags or [],
        side_effect_level=side_effect_level,
        filesystem_access=filesystem_access or [],
        network_access=network_access or [],
        environment_variables=environment_variables or [],
        external_domains=external_domains or [],
        metadata=metadata or {},
    )


__all__ = ["PluginTool", "PluginToolHandler", "PluginToolKind", "define_tool"]
