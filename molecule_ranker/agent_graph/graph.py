from __future__ import annotations

from collections import defaultdict, deque

from molecule_ranker.agent_graph.schemas import AgentEdge, AgentGraphRun, AgentNode


class AgentGraphValidationError(ValueError):
    """Raised when a graph contract is invalid or unsafe to execute."""


DISALLOWED_AGENT_TYPES = {
    "shell",
    "tool",
    "arbitrary_shell",
    "codex_shell",
    "command",
    "subprocess",
}


class AgentGraph:
    """Validated executable graph description."""

    def __init__(self, run: AgentGraphRun) -> None:
        self.run = run
        self.validate()

    def validate(self) -> None:
        node_ids = [node.node_id for node in self.run.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise AgentGraphValidationError("Agent graph contains duplicate node_id values.")
        known_ids = set(node_ids)
        for node in self.run.nodes:
            self._validate_node(node)
        for edge in self.run.edges:
            self._validate_edge(edge, known_ids)
        self.execution_order()

    def execution_order(self) -> list[AgentNode]:
        nodes_by_id = {node.node_id: node for node in self.run.nodes}
        indegree = {node.node_id: 0 for node in self.run.nodes}
        outgoing: dict[str, list[AgentEdge]] = defaultdict(list)
        for edge in self.run.edges:
            outgoing[edge.from_node_id].append(edge)
            indegree[edge.to_node_id] += 1

        queue = deque(node.node_id for node in self.run.nodes if indegree[node.node_id] == 0)
        ordered_ids: list[str] = []
        while queue:
            node_id = queue.popleft()
            ordered_ids.append(node_id)
            for edge in outgoing.get(node_id, []):
                indegree[edge.to_node_id] -= 1
                if indegree[edge.to_node_id] == 0:
                    queue.append(edge.to_node_id)
        if len(ordered_ids) != len(self.run.nodes):
            raise AgentGraphValidationError("Agent graph contains a cycle.")
        return [nodes_by_id[node_id] for node_id in ordered_ids]

    def incoming_edges(self, node_id: str) -> list[AgentEdge]:
        return [edge for edge in self.run.edges if edge.to_node_id == node_id]

    def _validate_node(self, node: AgentNode) -> None:
        if not node.inputs and node.metadata.get("requires_inputs") is True:
            raise AgentGraphValidationError(f"{node.node_id} requires declared inputs.")
        if not node.outputs and node.metadata.get("requires_outputs") is True:
            raise AgentGraphValidationError(f"{node.node_id} requires declared outputs.")
        if node.agent_type.lower() in DISALLOWED_AGENT_TYPES:
            raise AgentGraphValidationError(
                f"Agent node {node.node_id!r} uses disallowed agent_type {node.agent_type!r}."
            )
        forbidden_keys = {"command", "shell", "subprocess", "tool_name", "tool_call"}
        if forbidden_keys & {key.lower() for key in node.metadata}:
            raise AgentGraphValidationError(
                f"Agent node {node.node_id!r} contains arbitrary shell/tool metadata."
            )

    def _validate_edge(self, edge: AgentEdge, known_ids: set[str]) -> None:
        if edge.from_node_id not in known_ids:
            raise AgentGraphValidationError(f"Unknown edge source node: {edge.from_node_id}.")
        if edge.to_node_id not in known_ids:
            raise AgentGraphValidationError(f"Unknown edge target node: {edge.to_node_id}.")
