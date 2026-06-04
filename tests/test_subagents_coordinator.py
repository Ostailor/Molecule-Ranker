from __future__ import annotations

import json

import pytest

from molecule_ranker.subagents.coordinator import (
    MultiAgentCoordinator,
    SubagentPolicyError,
)


def test_coordinator_delegates_simple_goal_and_writes_session(tmp_path) -> None:  # type: ignore[no-untyped-def]
    coordinator = MultiAgentCoordinator()

    session = coordinator.coordinate(
        user_goal="Review ranking evidence for a program update.",
        visible_artifact_ids=["ranking-artifact-1"],
        scoped_artifact_ids=["ranking-artifact-1"],
        output_dir=tmp_path,
    )

    assert session.supervisor_subagent_id == "program-manager"
    assert session.status == "succeeded"
    assert [task.assigned_subagent_id for task in session.tasks] == ["evidence-reviewer"]
    assert session.tasks[0].input_artifact_ids == ["ranking-artifact-1"]
    assert session.tasks[0].allowed_tool_names
    assert session.results[0].subagent_id == "evidence-reviewer"
    assert session.consensus[0].consensus_status == "agreed"
    written = json.loads((tmp_path / "multi_agent_session.json").read_text())
    assert written["multi_agent_session_id"] == session.multi_agent_session_id
    assert written["metadata"]["audit_events"][0]["event_type"] == "multi_agent_session_started"


def test_parallel_independent_tasks_have_no_dependencies() -> None:
    session = MultiAgentCoordinator().coordinate(
        user_goal="Review evidence and developability in parallel.",
        mode="parallel_independent",
        visible_artifact_ids=["run-1"],
        scoped_artifact_ids=["run-1"],
    )

    assert {task.assigned_subagent_id for task in session.tasks} == {
        "evidence-reviewer",
        "developability-safety",
    }
    assert all(task.metadata["dependencies"] == [] for task in session.tasks)
    assert len(session.results) == 2


def test_high_risk_result_requires_guardrail_sentinel_critique() -> None:
    session = MultiAgentCoordinator().coordinate(
        user_goal="Run integration external write for approved mapping.",
        mode="supervisor_delegated",
        visible_artifact_ids=["integration-artifact-1"],
        scoped_artifact_ids=["integration-artifact-1"],
    )

    assert session.status == "awaiting_human_review"
    assert session.tasks[0].risk_level == "high"
    assert session.tasks[0].requires_human_approval is True
    assert session.critiques
    assert session.critiques[0].critic_subagent_id == "guardrail-sentinel"
    assert session.critiques[0].metadata["required_for_high_risk"] is True
    assert "external_write" in session.consensus[0].metadata["human_review_triggers"]


def test_disagreement_creates_human_review_escalation() -> None:
    session = MultiAgentCoordinator().coordinate(
        user_goal="Review evidence summary.",
        mode="critique_and_revise",
        visible_artifact_ids=["evidence-artifact-1"],
        scoped_artifact_ids=["evidence-artifact-1"],
        force_disagreement=True,
    )

    assert session.consensus[0].consensus_status == "disagreement"
    assert session.consensus[0].human_review_required is True
    assert session.status == "awaiting_human_review"
    assert any(
        event["event_type"] == "human_review_escalated"
        for event in session.metadata["audit_events"]
    )


def test_unauthorized_tool_is_blocked() -> None:
    with pytest.raises(SubagentPolicyError, match="unauthorized tool"):
        MultiAgentCoordinator().coordinate(
            user_goal="Review ranking evidence.",
            visible_artifact_ids=["ranking-artifact-1"],
            scoped_artifact_ids=["ranking-artifact-1"],
            requested_tool_names=["run_sync_write_enabled"],
        )


def test_unauthorized_artifact_is_blocked() -> None:
    with pytest.raises(SubagentPolicyError, match="unauthorized artifacts"):
        MultiAgentCoordinator().coordinate(
            user_goal="Review ranking evidence.",
            visible_artifact_ids=["visible-artifact"],
            scoped_artifact_ids=["hidden-artifact"],
        )
