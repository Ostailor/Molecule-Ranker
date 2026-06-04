from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.subagents.governance import (
    MultiAgentGovernance,
    RetentionItem,
    SubagentGovernancePolicy,
    SubagentGovernanceUsage,
)
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentMessage,
    SubagentResult,
    SubagentTask,
)


def test_governance_budget_limit_enforced() -> None:
    governance = MultiAgentGovernance(
        policy=SubagentGovernancePolicy.default().model_copy(
            update={"budget_limits": {"tool_cost_usd": 10.0}}
        )
    )

    decision = governance.evaluate_session(
        _session(),
        usage=SubagentGovernanceUsage(budget_spend={"tool_cost_usd": 11.0}),
    )

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert "budget limit exceeded: tool_cost_usd" in decision.reasons
    assert "budget_exceeded" in decision.incident_flags


def test_repeated_guardrail_failures_pause_session() -> None:
    session = _session(
        critiques=[
            _critique("critique-1"),
            _critique("critique-2"),
            _critique("critique-3"),
        ]
    )
    governance = MultiAgentGovernance(
        policy=SubagentGovernancePolicy.default().model_copy(
            update={"repeated_guardrail_failure_threshold": 3}
        )
    )

    decision = governance.evaluate_session(session)
    updated = governance.apply_session_decision(session, decision)

    assert decision.status == "paused"
    assert decision.allowed is False
    assert "repeated_guardrail_failures" in decision.incident_flags
    assert updated.status == "paused"
    assert updated.metadata["governance"]["status"] == "paused"


def test_human_escalation_required_on_disagreement() -> None:
    session = _session(
        consensus=[
            _consensus(
                consensus_status="disagreement",
                human_review_required=True,
                disagreements=["Evidence reviewer and graph reasoner disagree."],
            )
        ]
    )

    decision = MultiAgentGovernance().evaluate_session(session)

    assert decision.status == "requires_human_review"
    assert "human_review_unresolved_disagreement" in decision.required_approvals


def test_retention_policy_applied() -> None:
    now = datetime(2026, 6, 4, 12, tzinfo=UTC)
    governance = MultiAgentGovernance(
        policy=SubagentGovernancePolicy.default().model_copy(
            update={"session_retention_days": 10, "transcript_retention_days": 3}
        )
    )

    report = governance.apply_retention(
        [
            RetentionItem(
                item_id="session-old",
                item_type="session",
                created_at=now - timedelta(days=11),
            ),
            RetentionItem(
                item_id="session-new",
                item_type="session",
                created_at=now - timedelta(days=9),
            ),
            RetentionItem(
                item_id="transcript-old",
                item_type="transcript",
                created_at=now - timedelta(days=4),
            ),
        ],
        now=now,
    )

    assert report.expired_item_ids == ["session-old", "transcript-old"]
    assert report.retained_item_ids == ["session-new"]
    assert report.expired_counts == {"session": 1, "transcript": 1}


def _now() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)


def _session(
    *,
    critiques: list[SubagentCritique] | None = None,
    consensus: list[SubagentConsensus] | None = None,
) -> MultiAgentSession:
    return MultiAgentSession(
        multi_agent_session_id="session-1",
        runtime_session_id=None,
        user_goal="Review ranking evidence.",
        supervisor_subagent_id="program-manager",
        subagent_ids=["program-manager", "evidence-reviewer"],
        tasks=[_task()],
        messages=[_message()],
        results=[_result()],
        critiques=critiques or [],
        consensus=consensus or [_consensus()],
        status="succeeded",
        started_at=_now(),
        completed_at=_now(),
        metadata={},
    )


def _task() -> SubagentTask:
    return SubagentTask(
        task_id="task-1",
        parent_session_id="session-1",
        assigned_subagent_id="evidence-reviewer",
        task_type="evidence_review",
        objective="Review ranking evidence.",
        input_artifact_ids=["artifact-1"],
        allowed_tool_names=["summarize_literature"],
        forbidden_tool_names=[],
        expected_output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        required_outputs=["summary"],
        risk_level="low",
        requires_human_approval=False,
        status="succeeded",
        created_at=_now(),
        started_at=_now(),
        completed_at=_now(),
        metadata={"required_permissions": ["literature:read"]},
    )


def _result() -> SubagentResult:
    return SubagentResult(
        result_id="result-1",
        task_id="task-1",
        subagent_id="evidence-reviewer",
        status="succeeded",
        output_json={"summary": "Grounded summary."},
        output_text="Grounded summary.",
        artifact_ids=["artifact-1"],
        tool_usage_ids=["tool-usage-1"],
        confidence=0.8,
        warnings=[],
        guardrail_findings=[],
        created_at=_now(),
        metadata={"artifact_provenance": {"artifact-1": "source-1"}},
    )


def _message() -> SubagentMessage:
    return SubagentMessage(
        message_id="message-1",
        parent_session_id="session-1",
        from_subagent_id="program-manager",
        to_subagent_id="evidence-reviewer",
        message_type="task_request",
        content="Review artifact-1.",
        referenced_artifact_ids=["artifact-1"],
        referenced_entity_ids=[],
        referenced_tool_names=["summarize_literature"],
        created_at=_now(),
        metadata={},
    )


def _critique(critique_id: str) -> SubagentCritique:
    return SubagentCritique(
        critique_id=critique_id,
        critic_subagent_id="guardrail-sentinel",
        target_result_id="result-1",
        critique_type="scientific_guardrail",
        passed=False,
        findings=["Guardrail failure."],
        required_fixes=["Escalate to human review."],
        confidence=0.9,
        metadata={"non_overridable": True},
    )


def _consensus(
    *,
    consensus_status: str = "agreed",
    human_review_required: bool = False,
    disagreements: list[str] | None = None,
) -> SubagentConsensus:
    return SubagentConsensus(
        consensus_id="consensus-1",
        parent_session_id="session-1",
        task_ids=["task-1"],
        participating_subagent_ids=["evidence-reviewer"],
        consensus_status=consensus_status,  # type: ignore[arg-type]
        summary="Consensus summary.",
        agreements=[] if disagreements else ["No disagreement."],
        disagreements=disagreements or [],
        recommended_next_actions=["Review output."],
        human_review_required=human_review_required,
        metadata={},
    )
