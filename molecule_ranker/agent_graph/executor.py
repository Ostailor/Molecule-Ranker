from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from molecule_ranker.agent_graph.audit import append_audit_event
from molecule_ranker.agent_graph.graph import AgentGraph
from molecule_ranker.agent_graph.schemas import AgentGraphRun, AgentNode
from molecule_ranker.agent_graph.state import AgentGraphState


class AgentGraphExecutionError(RuntimeError):
    """Raised when a required graph node fails."""


class AgentExecutionResult(BaseModel):
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutableAgent(Protocol):
    def execute(self, node: AgentNode, run: AgentGraphRun) -> AgentExecutionResult:
        """Execute one graph node."""
        ...


class AgentGraphExecutor:
    """Execute a validated AgentGraphRun with auditable node decisions."""

    def __init__(self, agents: dict[str, ExecutableAgent]) -> None:
        self._agents = dict(agents)

    def execute(self, run: AgentGraphRun) -> AgentGraphRun:
        graph = AgentGraph(run)
        working = run.model_copy(deep=True, update={"status": "running"})
        state = AgentGraphState(working)
        append_audit_event(working, event_type="graph_started", message="Agent graph started.")

        for node in graph.execution_order():
            working_node = self._node_by_id(working, node.node_id)
            if self._should_skip_for_failed_dependency(working, graph, working_node):
                self._skip_node(working, working_node, "Required dependency did not succeed.")
                continue
            missing = state.missing([*working_node.inputs, *working_node.required_artifacts])
            if missing:
                if self._is_required(working_node):
                    self._fail_node(
                        working,
                        working_node,
                        f"Required inputs/artifacts are missing: {', '.join(missing)}.",
                    )
                    self._fail_graph(working)
                    raise AgentGraphExecutionError(working_node.metadata["failure_reason"])
                self._skip_node(
                    working,
                    working_node,
                    "Optional node skipped because inputs/artifacts are missing: "
                    f"{', '.join(missing)}.",
                )
                continue
            try:
                self._start_node(working, working_node)
                agent = self._agent_for(working_node)
                result = self._coerce_result(agent.execute(working_node, working))
                outputs, artifacts = self._declared_outputs(working_node, result)
                state.update_outputs(outputs, artifacts)
                append_audit_event(
                    working,
                    event_type="agent_decision",
                    message=f"{working_node.agent_name} produced declared outputs.",
                    node=working_node,
                    metadata={
                        "outputs": sorted(outputs),
                        "artifacts": sorted(artifacts),
                        **result.metadata,
                    },
                )
                self._succeed_node(working, working_node)
            except Exception as exc:
                self._fail_node(working, working_node, str(exc))
                if self._is_required(working_node):
                    self._fail_graph(working)
                    raise AgentGraphExecutionError(str(exc)) from exc

        if working.status != "failed":
            working.status = "succeeded"
            append_audit_event(
                working,
                event_type="graph_succeeded",
                message="Agent graph succeeded.",
            )
        return working

    def _agent_for(self, node: AgentNode) -> ExecutableAgent:
        agent = self._agents.get(node.agent_name) or self._agents.get(node.node_id)
        if agent is None:
            raise AgentGraphExecutionError(f"No executable agent registered for {node.agent_name}.")
        return agent

    def _coerce_result(self, value: Any) -> AgentExecutionResult:
        if isinstance(value, AgentExecutionResult):
            return value
        if isinstance(value, dict):
            return AgentExecutionResult(outputs=value)
        raise AgentGraphExecutionError("Agent returned an unsupported result type.")

    def _declared_outputs(
        self,
        node: AgentNode,
        result: AgentExecutionResult,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        declared = set(node.outputs)
        output_keys = set(result.outputs)
        artifact_keys = set(result.artifacts)
        undeclared = sorted((output_keys | artifact_keys) - declared)
        if undeclared:
            raise AgentGraphExecutionError(
                f"{node.agent_name} returned undeclared output(s): {', '.join(undeclared)}."
            )
        return dict(result.outputs), dict(result.artifacts)

    def _start_node(self, run: AgentGraphRun, node: AgentNode) -> None:
        node.status = "running"
        node.started_at = datetime.now(UTC)
        append_audit_event(
            run,
            event_type="node_started",
            message=f"{node.agent_name} started.",
            node=node,
            metadata={"inputs": list(node.inputs), "outputs": list(node.outputs)},
        )

    def _succeed_node(self, run: AgentGraphRun, node: AgentNode) -> None:
        node.status = "succeeded"
        node.completed_at = datetime.now(UTC)
        append_audit_event(
            run,
            event_type="node_succeeded",
            message=f"{node.agent_name} succeeded.",
            node=node,
        )

    def _fail_node(self, run: AgentGraphRun, node: AgentNode, reason: str) -> None:
        node.status = "failed"
        node.completed_at = datetime.now(UTC)
        node.metadata = {**node.metadata, "failure_reason": reason}
        append_audit_event(
            run,
            event_type="node_failed",
            message=f"{node.agent_name} failed.",
            node=node,
            metadata={"required": self._is_required(node), "reason": reason},
        )

    def _skip_node(self, run: AgentGraphRun, node: AgentNode, reason: str) -> None:
        node.status = "skipped"
        node.completed_at = datetime.now(UTC)
        node.metadata = {**node.metadata, "skip_reason": reason}
        append_audit_event(
            run,
            event_type="node_skipped",
            message=f"{node.agent_name} skipped.",
            node=node,
            metadata={"reason": reason},
        )

    def _fail_graph(self, run: AgentGraphRun) -> None:
        run.status = "failed"
        append_audit_event(run, event_type="graph_failed", message="Agent graph failed.")

    def _should_skip_for_failed_dependency(
        self,
        run: AgentGraphRun,
        graph: AgentGraph,
        node: AgentNode,
    ) -> bool:
        nodes_by_id = {item.node_id: item for item in run.nodes}
        for edge in graph.incoming_edges(node.node_id):
            upstream = nodes_by_id[edge.from_node_id]
            if edge.required and upstream.status in {"failed", "skipped"}:
                return True
        return False

    def _node_by_id(self, run: AgentGraphRun, node_id: str) -> AgentNode:
        for node in run.nodes:
            if node.node_id == node_id:
                return node
        raise AgentGraphExecutionError(f"Unknown node: {node_id}.")

    def _is_required(self, node: AgentNode) -> bool:
        return bool(node.metadata.get("required", True))
