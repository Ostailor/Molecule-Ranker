from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PluginWorkflow(BaseModel):
    name: str
    description: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    approval_requirements: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def define_workflow(
    name: str,
    *,
    description: str,
    steps: list[dict[str, Any]] | None = None,
    required_tools: list[str] | None = None,
    required_permissions: list[str] | None = None,
    approval_requirements: list[str] | None = None,
    expected_artifacts: list[str] | None = None,
    forbidden_outputs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PluginWorkflow:
    return PluginWorkflow(
        name=name,
        description=description,
        steps=steps or [],
        required_tools=required_tools or [],
        required_permissions=required_permissions or [],
        approval_requirements=approval_requirements or [],
        expected_artifacts=expected_artifacts or [],
        forbidden_outputs=forbidden_outputs or [],
        metadata=metadata or {},
    )


__all__ = ["PluginWorkflow", "define_workflow"]
