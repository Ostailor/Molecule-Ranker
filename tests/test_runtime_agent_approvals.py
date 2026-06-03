from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from molecule_ranker.runtime_agents.approvals import (
    ApprovalPolicyError,
    RuntimeApprovalController,
)
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_autonomy_levels_are_enforced() -> None:
    registry = RuntimeToolRegistry.default()
    controller = RuntimeApprovalController()

    observe_summary = controller.check_tool_allowed(
        "observe_only",
        registry.require("summarize_artifacts"),
    )
    observe_write = controller.check_tool_allowed("observe_only", registry.require("run_ranking"))
    suggest = controller.check_tool_allowed("suggest_only", registry.require("summarize_ranking"))
    safe = controller.check_tool_allowed("execute_safe_tools", registry.require("run_ranking"))
    db_write = controller.check_tool_allowed(
        "execute_safe_tools",
        registry.require("create_project"),
    )
    external_write = controller.check_tool_allowed(
        "execute_safe_tools",
        registry.require("run_sync_write_enabled"),
    )
    with_approval = controller.check_tool_allowed(
        "execute_with_approval",
        registry.require("run_sync_write_enabled"),
    )

    assert observe_summary.allowed is True
    assert observe_write.allowed is False
    assert suggest.allowed is False
    assert safe.allowed is True
    assert db_write.allowed is False
    assert external_write.allowed is False
    assert with_approval.allowed is False
    assert with_approval.requires_approval is True
    assert with_approval.approval_type == "integration_sync"


def test_approval_request_created() -> None:
    controller = RuntimeApprovalController()
    requested_at = datetime(2026, 6, 3, 12, tzinfo=UTC)

    request = controller.create_approval_request(
        session_id="session-1",
        plan_id="plan-1",
        step_id="step-1",
        requested_by="codex",
        approval_type="external_write",
        reason="Write sync to ELN.",
        risk_summary="External integration write.",
        requested_at=requested_at,
        ttl_minutes=30,
    )

    assert request.status == "pending"
    assert request.metadata["expires_at"] == (
        requested_at + timedelta(minutes=30)
    ).isoformat()


def test_approval_decision_is_audited() -> None:
    controller = RuntimeApprovalController()
    request = controller.create_approval_request(
        session_id="session-1",
        plan_id="plan-1",
        step_id="step-1",
        requested_by="codex",
        approval_type="high_cost_job",
        reason="Run benchmark.",
        risk_summary="High compute cost.",
        requested_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
    )

    decision = controller.decide(
        request,
        decided_by="user-1",
        approved=True,
        rationale="Approved within budget.",
        decided_at=datetime(2026, 6, 3, 12, 5, tzinfo=UTC),
    )

    assert decision.request.status == "approved"
    assert decision.request.decided_by == "user-1"
    assert decision.audit_event.event_type == "runtime_approval_approved"
    assert decision.audit_event.object_id == request.approval_id


def test_approval_expiration_works() -> None:
    controller = RuntimeApprovalController()
    requested_at = datetime(2026, 6, 3, 12, tzinfo=UTC)
    request = controller.create_approval_request(
        session_id="session-1",
        plan_id="plan-1",
        step_id=None,
        requested_by="codex",
        approval_type="policy_override",
        reason="Override policy.",
        risk_summary="Policy override.",
        requested_at=requested_at,
        ttl_minutes=1,
    )

    expired = controller.expire_if_needed(
        request,
        now=requested_at + timedelta(minutes=2),
    )

    assert expired.request.status == "expired"
    assert expired.audit_event.event_type == "runtime_approval_expired"


def test_codex_cannot_approve_its_own_approval() -> None:
    controller = RuntimeApprovalController()
    request = controller.create_approval_request(
        session_id="session-1",
        plan_id="plan-1",
        step_id="step-1",
        requested_by="codex",
        approval_type="stage_gate",
        reason="Advance gate.",
        risk_summary="Stage gate decision.",
        requested_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
    )

    with pytest.raises(ApprovalPolicyError, match="Codex cannot approve"):
        controller.decide(
            request,
            decided_by="codex",
            approved=True,
            rationale="Looks good.",
            decided_at=datetime(2026, 6, 3, 12, 1, tzinfo=UTC),
        )


def test_full_auto_restricted_still_requires_approval_for_stage_gate() -> None:
    controller = RuntimeApprovalController()
    check = controller.check_tool_allowed("full_auto_restricted", _stage_gate_tool())

    assert check.allowed is False
    assert check.requires_approval is True
    assert check.approval_type == "stage_gate"


def _stage_gate_tool() -> RuntimeToolSpec:
    return RuntimeToolSpec.model_validate(
        {
            "tool_name": "approve_stage_gate",
            "category": "campaign",
            "description": "Approve a campaign stage gate.",
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {"type": "object", "additionalProperties": True},
            "required_permissions": ["campaign:approve"],
            "policy_tags": ["stage_gate"],
            "side_effect_level": "db_write",
            "requires_approval_by_default": True,
            "idempotent": False,
        }
    )
