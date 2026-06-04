from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentMessage,
    SubagentProfile,
    SubagentResult,
    SubagentTask,
)


def test_subagent_profile_accepts_declared_roles_and_rejects_tool_overlap() -> None:
    profile = _profile()

    assert profile.role == "evidence_reviewer"
    assert profile.max_context_bytes == 4096

    with pytest.raises(ValidationError, match="allowed and denied tool categories overlap"):
        _profile(
            allowed_tool_categories=["literature"],
            denied_tool_categories=["literature"],
        )

    with pytest.raises(ValidationError):
        _profile(role="unsupported")  # type: ignore[arg-type]


def test_subagent_task_requires_scoped_artifacts_tools_and_object_schema() -> None:
    task = _task()

    assert task.status == "queued"
    assert task.expected_output_schema["type"] == "object"

    with pytest.raises(ValidationError, match="scoped input artifacts"):
        _task(input_artifact_ids=[])

    with pytest.raises(ValidationError, match="scoped allowed tools"):
        _task(allowed_tool_names=[])

    with pytest.raises(ValidationError, match="allowed and forbidden tool names overlap"):
        _task(
            allowed_tool_names=["summarize_literature"],
            forbidden_tool_names=["summarize_literature"],
        )

    with pytest.raises(ValidationError, match="JSON object schema"):
        _task(expected_output_schema={"type": "array"})

    with pytest.raises(ValidationError):
        _task(risk_level="severe")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _task(status="pending")  # type: ignore[arg-type]


def test_subagent_result_and_critique_confidence_are_bounded() -> None:
    result = _result(confidence=1.0)
    critique = _critique(confidence=0.0)

    assert result.confidence == 1.0
    assert critique.confidence == 0.0

    with pytest.raises(ValidationError):
        _result(confidence=1.01)

    with pytest.raises(ValidationError):
        _critique(confidence=-0.01)


def test_subagent_message_critique_and_consensus_literals_are_enforced() -> None:
    message = _message()
    critique = _critique()
    consensus = _consensus()

    assert message.message_type == "task_request"
    assert critique.critique_type == "evidence_grounding"
    assert consensus.consensus_status == "agreed"

    with pytest.raises(ValidationError):
        _message(message_type="chat")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _critique(critique_type="style")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _consensus(consensus_status="approved")  # type: ignore[arg-type]


def test_multi_agent_session_nests_all_subagent_schema_records() -> None:
    session = MultiAgentSession(
        multi_agent_session_id="multi-1",
        runtime_session_id="runtime-1",
        user_goal="Review source-backed ranking evidence.",
        supervisor_subagent_id="supervisor-1",
        subagent_ids=["evidence-reviewer"],
        tasks=[_task()],
        messages=[_message()],
        results=[_result()],
        critiques=[_critique()],
        consensus=[_consensus()],
        status="running",
        started_at=_now(),
        completed_at=None,
        metadata={"schema_version": "subagents.v1"},
    )

    assert session.tasks[0].assigned_subagent_id == "evidence-reviewer"
    assert session.results[0].status == "succeeded"


def test_all_timestamps_must_be_timezone_aware() -> None:
    naive = datetime(2026, 6, 4, 12)

    with pytest.raises(ValidationError, match="timezone-aware"):
        _task(created_at=naive)

    with pytest.raises(ValidationError, match="timezone-aware"):
        _result(created_at=naive)

    with pytest.raises(ValidationError, match="timezone-aware"):
        _message(created_at=naive)

    with pytest.raises(ValidationError, match="timezone-aware"):
        MultiAgentSession(
            multi_agent_session_id="multi-1",
            runtime_session_id=None,
            user_goal="Goal",
            supervisor_subagent_id="supervisor-1",
            subagent_ids=["evidence-reviewer"],
            tasks=[],
            messages=[],
            results=[],
            critiques=[],
            consensus=[],
            status="running",
            started_at=naive,
            completed_at=None,
            metadata={},
        )


def _now() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)


def _profile(**overrides) -> SubagentProfile:  # type: ignore[no-untyped-def]
    payload = {
        "subagent_id": "evidence-reviewer",
        "name": "Evidence Reviewer",
        "role": "evidence_reviewer",
        "description": "Reviews source-backed evidence artifacts.",
        "allowed_tool_categories": ["literature", "ranking"],
        "denied_tool_categories": ["external_write"],
        "required_permissions": ["literature:read", "run:read"],
        "default_autonomy_level": "suggest_only",
        "max_context_bytes": 4096,
        "can_delegate": False,
        "can_request_approval": True,
        "can_execute_tools": True,
        "can_write_artifacts": False,
        "guardrail_profile": "evidence_grounding",
        "metadata": {},
    }
    return SubagentProfile(**{**payload, **overrides})


def _task(**overrides) -> SubagentTask:  # type: ignore[no-untyped-def]
    payload = {
        "task_id": "task-1",
        "parent_session_id": "multi-1",
        "assigned_subagent_id": "evidence-reviewer",
        "task_type": "evidence_review",
        "objective": "Review source-backed ranking evidence.",
        "input_artifact_ids": ["ranking-artifact-1"],
        "allowed_tool_names": ["summarize_literature"],
        "forbidden_tool_names": ["run_sync_write_enabled"],
        "expected_output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        "required_outputs": ["summary"],
        "risk_level": "low",
        "requires_human_approval": False,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "metadata": {},
    }
    return SubagentTask(**{**payload, **overrides})


def _result(**overrides) -> SubagentResult:  # type: ignore[no-untyped-def]
    payload = {
        "result_id": "result-1",
        "task_id": "task-1",
        "subagent_id": "evidence-reviewer",
        "status": "succeeded",
        "output_json": {"summary": "Grounded summary."},
        "output_text": "Grounded summary.",
        "artifact_ids": ["review-artifact-1"],
        "tool_usage_ids": ["tool-usage-1"],
        "confidence": 0.8,
        "warnings": [],
        "guardrail_findings": [],
        "created_at": _now(),
        "metadata": {},
    }
    return SubagentResult(**{**payload, **overrides})


def _message(**overrides) -> SubagentMessage:  # type: ignore[no-untyped-def]
    payload = {
        "message_id": "message-1",
        "parent_session_id": "multi-1",
        "from_subagent_id": "program-manager",
        "to_subagent_id": "evidence-reviewer",
        "message_type": "task_request",
        "content": "Review this artifact.",
        "referenced_artifact_ids": ["ranking-artifact-1"],
        "referenced_entity_ids": [],
        "referenced_tool_names": ["summarize_literature"],
        "created_at": _now(),
        "metadata": {},
    }
    return SubagentMessage(**{**payload, **overrides})


def _critique(**overrides) -> SubagentCritique:  # type: ignore[no-untyped-def]
    payload = {
        "critique_id": "critique-1",
        "critic_subagent_id": "guardrail-sentinel",
        "target_result_id": "result-1",
        "critique_type": "evidence_grounding",
        "passed": True,
        "findings": [],
        "required_fixes": [],
        "confidence": 0.9,
        "metadata": {},
    }
    return SubagentCritique(**{**payload, **overrides})


def _consensus(**overrides) -> SubagentConsensus:  # type: ignore[no-untyped-def]
    payload = {
        "consensus_id": "consensus-1",
        "parent_session_id": "multi-1",
        "task_ids": ["task-1"],
        "participating_subagent_ids": ["evidence-reviewer", "guardrail-sentinel"],
        "consensus_status": "agreed",
        "summary": "Evidence review passed guardrail critique.",
        "agreements": ["Output is artifact-grounded."],
        "disagreements": [],
        "recommended_next_actions": ["Send to human reviewer."],
        "human_review_required": True,
        "metadata": {},
    }
    return SubagentConsensus(**{**payload, **overrides})
