from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PluginSkill(BaseModel):
    name: str
    description: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def define_skill(
    name: str,
    *,
    description: str,
    steps: list[dict[str, Any]] | None = None,
    required_tools: list[str] | None = None,
    guardrails: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PluginSkill:
    return PluginSkill(
        name=name,
        description=description,
        steps=steps or [],
        required_tools=required_tools or [],
        guardrails=guardrails or [],
        metadata=metadata or {},
    )


__all__ = ["PluginSkill", "define_skill"]
