from __future__ import annotations

from typing import Any

from molecule_ranker.agent_graph.graph import AgentGraph, AgentGraphValidationError
from molecule_ranker.agent_graph.schemas import AgentGraphRun


class AgentGraphPlanner:
    """Validate graph plans before they are executable runtime objects."""

    def validate_plan(self, payload: AgentGraphRun | dict[str, Any]) -> AgentGraphRun:
        run = payload if isinstance(payload, AgentGraphRun) else AgentGraphRun(**payload)
        AgentGraph(run).validate()
        return run

    def validate_codex_plan(self, payload: AgentGraphRun | dict[str, Any]) -> AgentGraphRun:
        run = self.validate_plan(payload)
        run.metadata = {
            **run.metadata,
            "codex_plan_validated": True,
            "validation_policy": "no arbitrary shell/tool execution nodes",
        }
        return run


__all__ = ["AgentGraphPlanner", "AgentGraphValidationError"]
