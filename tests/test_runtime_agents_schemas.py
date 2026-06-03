from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
    RuntimeAgentSession,
    RuntimeApprovalRequest,
    RuntimeToolResult,
    RuntimeToolSpec,
)


def test_runtime_agent_session_accepts_allowed_values_and_defaults_metadata() -> None:
    session = RuntimeAgentSession(
        session_id="session-1",
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        user_goal="Rank candidates and export a report.",
        autonomy_level="execute_with_approval",
        status="planning",
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=None,
    )

    assert session.metadata == {}
    assert session.started_at.tzinfo is not None


def test_runtime_agent_schemas_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RuntimeAgentSession(
            session_id="session-1",
            project_id=None,
            org_id=None,
            user_id=None,
            user_goal="Rank candidates.",
            autonomy_level="suggest_only",
            status="created",
            started_at=datetime(2026, 6, 3, 12),
            completed_at=None,
        )


def test_runtime_tool_spec_requires_permissions_and_allowed_side_effect_level() -> None:
    spec = RuntimeToolSpec(
        tool_name="run_ranking",
        category="ranking",
        description="Run deterministic ranking.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_permissions=["ranking:run"],
        policy_tags=["source_backed"],
        side_effect_level="artifact_write",
        requires_approval_by_default=False,
        idempotent=True,
    )

    assert spec.required_permissions == ["ranking:run"]
    assert spec.metadata == {}

    with pytest.raises(ValidationError, match="required_permissions"):
        RuntimeToolSpec(
            tool_name="run_ranking",
            category="ranking",
            description="Run deterministic ranking.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=[],
            policy_tags=[],
            side_effect_level="artifact_write",
            requires_approval_by_default=False,
            idempotent=True,
        )

    invalid_side_effect_payload = {
        "tool_name": "run_ranking",
        "category": "ranking",
        "description": "Run deterministic ranking.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_permissions": ["ranking:run"],
        "policy_tags": [],
        "side_effect_level": "unsafe_shell",
        "requires_approval_by_default": False,
        "idempotent": True,
    }
    with pytest.raises(ValidationError):
        RuntimeToolSpec.model_validate(invalid_side_effect_payload)


def test_validated_runtime_action_plan_requires_tool_specs_for_all_steps() -> None:
    step = _step()

    with pytest.raises(ValidationError, match="tool specs"):
        RuntimeActionPlan(
            plan_id="plan-1",
            session_id="session-1",
            user_goal="Rank candidates.",
            plan_summary="Run ranking.",
            steps=[step],
            required_approvals=[],
            expected_artifacts=["ranking_run"],
            risk_level="low",
            guardrail_warnings=[],
            created_by="codex",
            validated=True,
            validation_errors=[],
            metadata={},
        )

    plan = RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Rank candidates.",
        plan_summary="Run ranking.",
        steps=[step],
        required_approvals=[],
        expected_artifacts=["ranking_run"],
        risk_level="low",
        guardrail_warnings=[],
        created_by="codex",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                "run_ranking": {
                    "required_permissions": ["ranking:run"],
                    "side_effect_level": "artifact_write",
                }
            }
        },
    )

    assert plan.validated is True


def test_runtime_result_approval_and_audit_event_models() -> None:
    started_at = datetime(2026, 6, 3, 12, tzinfo=UTC)
    result = RuntimeToolResult(
        result_id="result-1",
        step_id="step-1",
        tool_name="run_ranking",
        status="succeeded",
        output={"summary": "Ranking completed."},
        artifact_ids=["ranking-run-1"],
        job_ids=[],
        error_summary=None,
        warnings=[],
        started_at=started_at,
        completed_at=started_at,
    )
    approval = RuntimeApprovalRequest(
        approval_id="approval-1",
        session_id="session-1",
        plan_id="plan-1",
        step_id="step-1",
        requested_by="codex",
        approval_type="execute_plan",
        reason="Export will write a report artifact.",
        risk_summary="Low risk artifact write.",
        requested_at=started_at,
        status="pending",
        decided_by=None,
        decided_at=None,
        decision_rationale=None,
    )
    event = RuntimeAgentAuditEvent(
        event_id="event-1",
        session_id="session-1",
        event_type="tool_result",
        actor="codex",
        timestamp=started_at,
        summary="Tool succeeded.",
        object_type="RuntimeToolResult",
        object_id="result-1",
        before=None,
        after=result.model_dump(mode="json"),
    )

    assert result.metadata == {}
    assert approval.metadata == {}
    assert event.metadata == {}


def _step() -> RuntimeActionStep:
    return RuntimeActionStep(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        action_type="run_ranking",
        tool_name="run_ranking",
        tool_args={"project_id": "project-1"},
        requires_approval=False,
        approval_reason=None,
        expected_outputs=["ranking_run"],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={},
    )
