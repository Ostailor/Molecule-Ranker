from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from molecule_ranker.copilot.escalation import EscalationManager
from molecule_ranker.copilot.schemas import (
    CoPilotAction,
    CoPilotEscalation,
    CoPilotTrigger,
    Priority,
    TriggerType,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _trigger(
    *,
    trigger_type: TriggerType = "approval_needed",
    priority: Priority = "high",
    metadata: dict[str, Any] | None = None,
) -> CoPilotTrigger:
    return CoPilotTrigger(
        trigger_id="trigger-1",
        campaign_id="camp-1",
        event_ids=["event-1"],
        trigger_signature="camp-1:approval_needed:review:event",
        trigger_type=trigger_type,
        priority=priority,
        rationale="Human attention required for campaign planning.",
        recommended_action_types=["request_approval"],
        requires_human_attention=True,
        metadata=metadata or {},
    )


def _action(*, requires_approval: bool = True) -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id="action-1",
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type="request_approval",
        tool_name=None,
        tool_args={},
        side_effect_level="db_write",
        risk_level="medium",
        requires_approval=requires_approval,
        approval_reason="Human approval required.",
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata={},
    )


def test_escalation_created_with_artifacts_notification_and_audit():
    manager = EscalationManager(now=lambda: NOW)

    escalation = manager.create_escalation(
        campaign_id="camp-1",
        escalation_type="missing_input",
        priority="medium",
        message="Campaign owner must provide missing planning input.",
        trigger_id="trigger-1",
        action_id="action-1",
        artifact_ids=["artifact-1"],
        assigned_role="campaign_owner",
    )

    assert escalation.escalation_id in manager.escalations
    assert escalation.escalation_type == "missing_input"
    assert escalation.artifact_ids == ["artifact-1"]
    assert manager.notifications == [
        {
            "escalation_id": escalation.escalation_id,
            "assigned_role": "campaign_owner",
            "priority": "medium",
            "message": "Campaign owner must provide missing planning input.",
        }
    ]
    assert [record["transition"] for record in manager.audit_log] == [
        "created",
        "assigned",
        "notified",
    ]


def test_from_trigger_assigns_stage_gate_approval_to_human_role():
    manager = EscalationManager(
        role_routing={"human_approval_required": "campaign_owner"},
        now=lambda: NOW,
    )
    trigger = _trigger(metadata={"decision_type": "stage_gate"})

    escalation = manager.from_trigger(trigger, action=_action())

    assert escalation is not None
    assert escalation.escalation_type == "human_approval_required"
    assert escalation.assigned_role == "campaign_owner"
    assert escalation.metadata["requires_human_role"] is True
    assert manager.notifications[0]["assigned_role"] == "campaign_owner"


def test_acknowledge_and_resolve_tracks_audit_transitions():
    manager = EscalationManager(now=lambda: NOW)
    escalation = manager.create_escalation(
        campaign_id="camp-1",
        escalation_type="campaign_blocked",
        priority="high",
        message="Campaign work package is blocked.",
        assigned_role="program_lead",
    )

    acknowledged = manager.acknowledge_escalation(
        escalation.escalation_id,
        actor_id="user-1",
        actor_role="program_lead",
    )
    resolved = manager.resolve_escalation(
        escalation.escalation_id,
        actor_id="user-1",
        actor_role="program_lead",
        resolution_note="Input received and campaign plan updated.",
    )

    assert acknowledged.status == "acknowledged"
    assert resolved.status == "resolved"
    assert resolved.resolved_at == NOW
    assert resolved.metadata["resolved_by"] == "user-1"
    assert [record["transition"] for record in manager.audit_log][-2:] == [
        "acknowledged",
        "resolved",
    ]


def test_codex_cannot_resolve_guardrail_escalation_and_timeout_reminds_role():
    manager = EscalationManager(
        reminder_timeout=timedelta(minutes=30),
        now=lambda: NOW,
    )
    escalation = manager.create_escalation(
        campaign_id="camp-1",
        escalation_type="guardrail_failure",
        priority="critical",
        message="Guardrail failure requires human review.",
        assigned_role="safety_reviewer",
        created_at=NOW - timedelta(hours=1),
    )

    blocked = manager.resolve_escalation(
        escalation.escalation_id,
        actor_id="codex",
        actor_role="copilot",
        resolution_note="Auto-resolve after repair.",
    )
    reminders = manager.auto_remind_due()

    assert blocked.status == "open"
    assert manager.audit_log[-2]["transition"] == "resolution_denied"
    assert reminders == [escalation.escalation_id]
    assert manager.notifications[-1]["message"] == "Escalation reminder: guardrail_failure"


def test_new_escalation_types_are_schema_allowed():
    for escalation_type in ("campaign_blocked", "missing_input"):
        escalation = CoPilotEscalation(
            escalation_id=f"esc-{escalation_type}",
            campaign_id="camp-1",
            trigger_id=None,
            action_id=None,
            escalation_type=escalation_type,
            priority="medium",
            assigned_role=None,
            message="Allowed escalation type.",
            artifact_ids=[],
            status="open",
            created_at=NOW,
            resolved_at=None,
            metadata={},
        )

        assert escalation.escalation_type == escalation_type
