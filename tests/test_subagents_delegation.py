from __future__ import annotations

import pytest

from molecule_ranker.subagents.delegation import (
    COMPLEX_CAMPAIGN_STALL_TASKS,
    DelegationEngine,
    DelegationPolicy,
    DelegationPolicyError,
    DelegationRequest,
)


def test_delegation_assigns_correct_role_from_task_type() -> None:
    decision = DelegationEngine().delegate(
        DelegationRequest(
            requester_subagent_id="user",
            task_type="evidence_review",
            objective="Review source evidence.",
            required_artifact_ids=["artifact-1"],
            required_tool_names=["summarize_literature"],
        ),
        policy=_policy(),
    )

    assert decision.assigned_profile.role == "evidence_reviewer"
    assert decision.task.assigned_subagent_id == "evidence-reviewer"
    assert decision.task.allowed_tool_names == ["summarize_literature"]
    assert decision.task.metadata["inherited_policy"]["parent_session_id"] == "session-1"


def test_unauthorized_delegation_requester_is_rejected() -> None:
    with pytest.raises(DelegationPolicyError, match="cannot request delegation"):
        DelegationEngine().delegate(
            DelegationRequest(
                requester_subagent_id="evidence-reviewer",
                task_type="graph_reasoning",
                objective="Check graph contradictions.",
                required_artifact_ids=["artifact-1"],
                required_tool_names=["query_graph"],
            ),
            policy=_policy(),
        )


def test_inherited_policy_blocks_tools_and_artifacts() -> None:
    engine = DelegationEngine()

    with pytest.raises(DelegationPolicyError, match="required tools"):
        engine.delegate(
            DelegationRequest(
                requester_subagent_id="program-manager",
                task_type="integration_operation",
                objective="Run an integration write.",
                required_artifact_ids=["artifact-1"],
                required_tool_names=["run_sync_write_enabled"],
            ),
            policy=_policy(allowed_tool_names=["dry_run_sync"]),
        )

    with pytest.raises(DelegationPolicyError, match="required artifacts"):
        engine.delegate(
            DelegationRequest(
                requester_subagent_id="program-manager",
                task_type="evidence_review",
                objective="Review hidden evidence.",
                required_artifact_ids=["hidden-artifact"],
                required_tool_names=["summarize_literature"],
            ),
            policy=_policy(visible_artifact_ids=["artifact-1"]),
        )


def test_cyclic_delegation_is_blocked() -> None:
    with pytest.raises(DelegationPolicyError, match="cyclic delegation"):
        DelegationEngine().delegate(
            DelegationRequest(
                requester_subagent_id="program-manager",
                task_type="campaign_planning",
                objective="Plan campaign follow-up.",
                required_artifact_ids=["artifact-1"],
                required_tool_names=["plan_campaign"],
                delegation_chain=["campaign-planner"],
            ),
            policy=_policy(),
        )


def test_complex_campaign_stall_goal_decomposes_into_expected_subtasks() -> None:
    decisions = DelegationEngine().decompose_complex_task(
        user_goal="Find why the campaign stalled and propose next steps.",
        requester_subagent_id="program-manager",
        policy=_policy(
            allowed_tool_names=[
                "run_readiness",
                "ops_health",
                "plan_campaign",
                "replan_campaign",
                "summarize_assay_results",
                "query_graph",
                "detect_contradictions",
                "run_guardrail_benchmark",
                "draft_report",
            ]
        ),
        root_artifact_ids=["artifact-1"],
    )

    assert len(decisions) == len(COMPLEX_CAMPAIGN_STALL_TASKS)
    assert [decision.task.assigned_subagent_id for decision in decisions] == [
        "platform-operator",
        "campaign-planner",
        "experiment-analyst",
        "graph-reasoner",
        "guardrail-sentinel",
        "program-manager",
    ]
    assert decisions[-2].requires_guardrail_critique is True
    assert decisions[-2].requires_human_approval is True


def _policy(
    *,
    visible_artifact_ids: list[str] | None = None,
    allowed_tool_names: list[str] | None = None,
) -> DelegationPolicy:
    return DelegationPolicy(
        parent_session_id="session-1",
        visible_artifact_ids=visible_artifact_ids or ["artifact-1"],
        allowed_tool_names=allowed_tool_names,
        forbidden_tool_names=[],
        default_risk_level="low",
        metadata={"policy": "test"},
    )
