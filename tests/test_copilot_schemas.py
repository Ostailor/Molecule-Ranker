from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.copilot.schemas import (
    CampaignCoPilotSession,
    CampaignEvent,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotEscalation,
    CoPilotMemoryRecord,
    CoPilotStatusUpdate,
    CoPilotTrigger,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _valid_action(**overrides: Any) -> CoPilotAction:
    payload: dict[str, Any] = {
        "copilot_action_id": "action-1",
        "campaign_id": "campaign-1",
        "trigger_id": "trigger-1",
        "action_type": "summarize_status",
        "tool_name": None,
        "tool_args": {"artifact_id": "artifact-1"},
        "side_effect_level": "none",
        "risk_level": "low",
        "requires_approval": False,
        "approval_reason": None,
        "status": "proposed",
        "created_at": NOW,
        "completed_at": None,
        "metadata": {},
    }
    payload.update(overrides)
    return CoPilotAction(**payload)


def test_campaign_copilot_session_schema_accepts_allowed_values():
    session = CampaignCoPilotSession(
        copilot_session_id="session-1",
        campaign_id="campaign-1",
        project_id="project-1",
        program_id=None,
        status="active",
        autonomy_level="execute_safe_actions",
        started_at=NOW,
        stopped_at=None,
        last_check_at=NOW,
        metadata={"owner": "translational-research"},
    )

    assert session.status == "active"
    assert session.autonomy_level == "execute_safe_actions"


def test_campaign_event_schema_accepts_full_event_contract():
    event = CampaignEvent(
        event_id="event-1",
        campaign_id="campaign-1",
        event_type="graph_contradiction_detected",
        source_object_type="knowledge_graph_edge",
        source_object_id="edge-1",
        severity="high",
        summary="Contradictory source-backed graph facts need review.",
        artifact_ids=["artifact-1"],
        detected_at=NOW,
        metadata={"source": "graph-check"},
    )

    assert event.event_type == "graph_contradiction_detected"
    assert event.severity == "high"


def test_trigger_action_result_escalation_status_and_memory_schemas():
    trigger = CoPilotTrigger(
        trigger_id="trigger-1",
        campaign_id="campaign-1",
        event_ids=["event-1"],
        trigger_type="replan_needed",
        priority="high",
        rationale="New source-backed event changed the campaign planning state.",
        recommended_action_types=["create_replan_draft"],
        requires_human_attention=True,
        metadata={},
    )
    action = _valid_action(
        action_type="create_replan_draft",
        side_effect_level="artifact_write",
        risk_level="medium",
        requires_approval=True,
        approval_reason="Draft replan may influence campaign direction.",
    )
    result = CoPilotActionResult(
        result_id="result-1",
        copilot_action_id=action.copilot_action_id,
        status="approval_required",
        artifact_ids=[],
        job_ids=[],
        summary="Human approval required before proceeding.",
        warnings=["Campaign advancement remains human-gated."],
        created_at=NOW,
        metadata={},
    )
    escalation = CoPilotEscalation(
        escalation_id="esc-1",
        campaign_id="campaign-1",
        trigger_id=trigger.trigger_id,
        action_id=action.copilot_action_id,
        escalation_type="human_approval_required",
        priority="high",
        assigned_role="campaign_owner",
        message="Approval is required for a risky planning action.",
        artifact_ids=[],
        status="open",
        created_at=NOW,
        resolved_at=None,
        metadata={},
    )
    status = CoPilotStatusUpdate(
        status_update_id="status-1",
        campaign_id="campaign-1",
        period_start=NOW,
        period_end=NOW,
        executive_summary="Planning update only; no scientific claims are inferred.",
        key_events=["event-1"],
        actions_taken=["action-1"],
        approvals_needed=["campaign_owner"],
        blockers=[],
        risks=["Contradiction requires human review."],
        next_recommended_actions=["Review source-backed contradiction."],
        limitations=["Planning aid only."],
        created_at=NOW,
        metadata={},
    )
    memory = CoPilotMemoryRecord(
        memory_id="memory-1",
        campaign_id="campaign-1",
        trigger_signature="graph_contradiction_detected:high",
        recommended_action_type="create_replan_draft",
        success_rate=0.75,
        occurrence_count=4,
        last_seen_at=NOW,
        notes="Worked when routed to campaign owner.",
        metadata={},
    )

    assert result.status == "approval_required"
    assert escalation.status == "open"
    assert status.limitations == ["Planning aid only."]
    assert memory.success_rate == 0.75


def _session_from_payload(payload: dict[str, Any]) -> CampaignCoPilotSession:
    return CampaignCoPilotSession(**payload)


def _event_from_payload(payload: dict[str, Any]) -> CampaignEvent:
    return CampaignEvent(**payload)


@pytest.mark.parametrize(
    ("model_factory", "expected"),
    [
        (
            lambda: _session_from_payload(
                {
                    "copilot_session_id": "session-1",
                    "campaign_id": "campaign-1",
                    "project_id": None,
                    "program_id": None,
                    "status": "running",
                    "autonomy_level": "observe_only",
                    "started_at": NOW,
                    "stopped_at": None,
                    "last_check_at": None,
                    "metadata": {},
                }
            ),
            "status",
        ),
        (
            lambda: _event_from_payload(
                {
                    "event_id": "event-1",
                    "campaign_id": "campaign-1",
                    "event_type": "unknown_event",
                    "source_object_type": "review",
                    "source_object_id": "review-1",
                    "severity": "info",
                    "summary": "Unknown event.",
                    "artifact_ids": [],
                    "detected_at": NOW,
                    "metadata": {},
                }
            ),
            "event_type",
        ),
        (
            lambda: CoPilotMemoryRecord(
                memory_id="memory-1",
                campaign_id=None,
                trigger_signature="sig",
                recommended_action_type="summarize_status",
                success_rate=1.1,
                occurrence_count=1,
                last_seen_at=NOW,
                notes="Out of range.",
                metadata={},
            ),
            "success_rate",
        ),
    ],
)
def test_schemas_reject_invalid_allowed_values_and_scores(model_factory, expected):
    with pytest.raises(ValidationError) as error:
        model_factory()

    assert expected in str(error.value)


def test_all_timestamps_must_be_timezone_aware():
    naive = datetime(2026, 6, 4, 12, 0)

    with pytest.raises(ValidationError) as error:
        CampaignCoPilotSession(
            copilot_session_id="session-1",
            campaign_id="campaign-1",
            project_id=None,
            program_id=None,
            status="active",
            autonomy_level="observe_only",
            started_at=naive,
            stopped_at=None,
            last_check_at=None,
            metadata={},
        )

    assert "timezone-aware" in str(error.value)


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"tool_args": {"instructions": "run this synthesis protocol"}},
        {"tool_args": {"guidance": "use this dosing schedule"}},
        {"metadata": {"details": "procedural wet-lab steps"}},
        {"approval_reason": "contains patient dosing details"},
    ],
)
def test_copilot_action_rejects_protocol_synthesis_and_dosing_details(
    forbidden_payload,
):
    with pytest.raises(ValidationError) as error:
        _valid_action(**forbidden_payload)

    assert "research planning actions must not contain" in str(error.value)
