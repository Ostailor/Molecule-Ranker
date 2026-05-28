from __future__ import annotations

from typing import Any

from molecule_ranker.agent_graph.schemas import AgentGraphRun


class AgentGraphState:
    """Small helper for graph state and artifact lookup/update."""

    def __init__(self, run: AgentGraphRun) -> None:
        self.run = run

    def has(self, key: str) -> bool:
        return key in self.run.state or key in self.run.artifacts

    def missing(self, keys: list[str]) -> list[str]:
        return [key for key in keys if not self.has(key)]

    def update_outputs(self, outputs: dict[str, Any], artifacts: dict[str, str]) -> None:
        self.run.state.update(outputs)
        self.run.artifacts.update(artifacts)
