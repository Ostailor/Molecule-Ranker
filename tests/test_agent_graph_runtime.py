from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.agent_graph import (
    AgentEdge,
    AgentGraphExecutor,
    AgentGraphPlanner,
    AgentGraphRun,
    AgentNode,
)
from molecule_ranker.agent_graph.executor import AgentExecutionResult, AgentGraphExecutionError


class RecordingAgent:
    def __init__(self, *, outputs: dict[str, Any], fail: bool = False) -> None:
        self.outputs = outputs
        self.fail = fail

    def execute(self, node: AgentNode, run: AgentGraphRun) -> AgentExecutionResult:
        if self.fail:
            raise RuntimeError(f"{node.agent_name} failed")
        return AgentExecutionResult(outputs=self.outputs)


def _node(
    node_id: str,
    agent_name: str,
    *,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    required: bool = True,
) -> AgentNode:
    return AgentNode(
        node_id=node_id,
        agent_name=agent_name,
        agent_type="deterministic",
        inputs=inputs or [],
        outputs=outputs or [],
        required_artifacts=[],
        optional_artifacts=[],
        status="pending",
        started_at=None,
        completed_at=None,
        metadata={"required": required},
    )


def _run(nodes: list[AgentNode], edges: list[AgentEdge] | None = None) -> AgentGraphRun:
    return AgentGraphRun(
        graph_run_id="graph-run-1",
        project_id=None,
        run_id="run-1",
        graph_version="1.1",
        nodes=nodes,
        edges=edges or [],
        state={},
        artifacts={},
        audit_events=[],
        status="pending",
        metadata={},
    )


def test_graph_execution_records_declared_outputs_and_audit_events() -> None:
    run = _run(
        [
            _node("plan", "PlannerAgent", outputs=["design_plan"]),
            _node("score", "ScoringAgent", inputs=["design_plan"], outputs=["scores"]),
        ],
        [
            AgentEdge(
                from_node_id="plan",
                to_node_id="score",
                artifact_key="design_plan",
                required=True,
                metadata={},
            )
        ],
    )

    result = AgentGraphExecutor(
        {
            "PlannerAgent": RecordingAgent(outputs={"design_plan": {"target": "SYN1"}}),
            "ScoringAgent": RecordingAgent(outputs={"scores": {"readiness": 0.7}}),
        }
    ).execute(run)

    assert result.status == "succeeded"
    assert result.state["design_plan"] == {"target": "SYN1"}
    assert result.state["scores"] == {"readiness": 0.7}
    assert [node.status for node in result.nodes] == ["succeeded", "succeeded"]
    event_names = [event["event_type"] for event in result.audit_events]
    assert event_names == [
        "graph_started",
        "node_started",
        "agent_decision",
        "node_succeeded",
        "node_started",
        "agent_decision",
        "node_succeeded",
        "graph_succeeded",
    ]


def test_optional_node_failure_is_audited_and_does_not_break_graph() -> None:
    run = _run(
        [
            _node("required", "RequiredAgent", outputs=["required_output"]),
            _node("optional", "OptionalCriticAgent", outputs=["critique"], required=False),
            _node("final", "FinalAgent", inputs=["required_output"], outputs=["final"]),
        ]
    )

    result = AgentGraphExecutor(
        {
            "RequiredAgent": RecordingAgent(outputs={"required_output": "ok"}),
            "OptionalCriticAgent": RecordingAgent(outputs={}, fail=True),
            "FinalAgent": RecordingAgent(outputs={"final": "done"}),
        }
    ).execute(run)

    assert result.status == "succeeded"
    assert [node.status for node in result.nodes] == ["succeeded", "failed", "succeeded"]
    assert result.state["final"] == "done"
    assert any(
        event["event_type"] == "node_failed" and event["metadata"]["required"] is False
        for event in result.audit_events
    )


def test_required_node_failure_stops_graph() -> None:
    run = _run(
        [
            _node("required", "RequiredAgent", outputs=["required_output"]),
            _node("after", "AfterAgent", inputs=["required_output"], outputs=["after"]),
        ]
    )

    with pytest.raises(AgentGraphExecutionError):
        AgentGraphExecutor(
            {
                "RequiredAgent": RecordingAgent(outputs={}, fail=True),
                "AfterAgent": RecordingAgent(outputs={"after": "should-not-run"}),
            }
        ).execute(run)


def test_codex_planned_graph_is_validated_and_rejects_shell_nodes() -> None:
    plan = {
        "graph_run_id": "codex-plan-1",
        "project_id": None,
        "run_id": "run-1",
        "graph_version": "1.1",
        "nodes": [
            {
                "node_id": "shell",
                "agent_name": "ShellAgent",
                "agent_type": "shell",
                "inputs": [],
                "outputs": ["untrusted"],
                "required_artifacts": [],
                "optional_artifacts": [],
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "metadata": {"command": "rm -rf /tmp/example"},
            }
        ],
        "edges": [],
        "state": {},
        "artifacts": {},
        "audit_events": [],
        "status": "pending",
        "metadata": {"planned_by": "codex"},
    }

    with pytest.raises(ValueError, match="shell"):
        AgentGraphPlanner().validate_codex_plan(plan)


def test_agent_node_status_is_validated() -> None:
    payload: dict[str, Any] = {
        "node_id": "bad",
        "agent_name": "BadAgent",
        "agent_type": "deterministic",
        "inputs": [],
        "outputs": [],
        "required_artifacts": [],
        "optional_artifacts": [],
        "status": "unknown",
        "started_at": None,
        "completed_at": None,
        "metadata": {},
    }
    with pytest.raises(ValidationError):
        AgentNode(**payload)
