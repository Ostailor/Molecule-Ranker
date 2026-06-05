from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.agent_governance import (
    AgentRunControlManager,
    RunControlRequest,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_kill_switch_blocks_action_and_pauses_session() -> None:
    manager = AgentRunControlManager()
    manager.apply_control(
        control_type="kill_switch",
        org_id="org-1",
        project_id="project-1",
        reason="Incident response.",
        applied_by="admin-1",
        applied_at=NOW,
        metadata={"session_action": "pause"},
    )

    decision = manager.evaluate(
        _request(org_id="org-1", project_id="project-1"),
        now=NOW,
    )

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert decision.session_action == "pause"
    assert decision.active_controls[0].control_type == "kill_switch"
    assert [event.action for event in manager.audit_events] == ["applied", "checked"]


def test_autonomy_restriction_applied() -> None:
    manager = AgentRunControlManager()
    manager.apply_control(
        control_type="restrict_autonomy",
        agent_id="agent-1",
        reason="Elevated risk.",
        applied_by="admin-1",
        applied_at=NOW,
        metadata={"max_autonomy_level": "suggest_only"},
    )

    decision = manager.evaluate(
        _request(autonomy_level="execute_safe_tools"),
        now=NOW,
    )

    assert decision.status == "autonomy_restricted"
    assert decision.allowed is False
    assert decision.effective_autonomy_cap == "suggest_only"


def test_approval_all_mode_requires_approval() -> None:
    manager = AgentRunControlManager()
    manager.apply_control(
        control_type="require_approval_all_actions",
        org_id="org-1",
        reason="Sponsor review window.",
        applied_by="admin-1",
        applied_at=NOW,
    )

    decision = manager.evaluate(_request(org_id="org-1"), now=NOW)

    assert decision.status == "approval_required"
    assert decision.requires_approval is True
    assert decision.allowed is False


def test_control_expiration_stops_blocking_action() -> None:
    manager = AgentRunControlManager()
    manager.apply_control(
        control_type="pause",
        agent_id="agent-1",
        reason="Temporary pause.",
        applied_by="admin-1",
        applied_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )

    decision = manager.evaluate(_request(), now=NOW)
    active_dashboard_controls = manager.active_dashboard_controls(now=NOW)

    assert decision.status == "allowed"
    assert active_dashboard_controls == []
    assert "expired" in [event.action for event in manager.audit_events]


def test_specialized_generated_molecule_kill_switch_matches_workflow() -> None:
    manager = AgentRunControlManager()
    manager.apply_control(
        control_type="kill_switch",
        org_id="org-1",
        reason="Generated molecule incident.",
        applied_by="admin-1",
        applied_at=NOW,
        metadata={
            "kill_switch_target": "generated_molecule_workflow",
            "session_action": "cancel",
        },
    )

    decision = manager.evaluate(
        _request(
            org_id="org-1",
            action="advance_generated_molecule_to_assay",
            workflow_type="generated_molecule",
        ),
        now=NOW,
    )

    assert decision.status == "blocked"
    assert decision.session_action == "cancel"


def _request(
    *,
    agent_id: str = "agent-1",
    agent_type: str = "runtime_agent",
    org_id: str | None = None,
    project_id: str | None = None,
    action: str = "run_ranking",
    autonomy_level: str = "execute_safe_tools",
    workflow_type: str | None = None,
) -> RunControlRequest:
    return RunControlRequest.model_validate(
        {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "org_id": org_id,
            "project_id": project_id,
            "action": action,
            "autonomy_level": autonomy_level,
            "workflow_type": workflow_type,
        }
    )
