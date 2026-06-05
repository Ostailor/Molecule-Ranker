from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.agent_governance import (
    AgentIncidentError,
    AgentIncidentManager,
    AgentRunControlManager,
    IncidentTriggerEvent,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_incident_created_on_critical_violation() -> None:
    manager = AgentIncidentManager()

    incident = manager.create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="critical_guardrail_failure",
            agent_id="agent-1",
            org_id="org-1",
            project_id="project-1",
            session_id="session-1",
            summary="Critical guardrail failure.",
            artifact_ids=["artifact-1"],
            tool_usage_ids=["tool-usage-1"],
        ),
        opened_at=NOW,
    )

    assert incident.status == "open"
    assert incident.severity == "critical"
    assert incident.incident_type == "guardrail_failure"
    assert incident.metadata["requires_recertification"] is True
    assert manager.audit_events[0].action == "created"


def test_mitigation_applies_run_control() -> None:
    incident_manager = AgentIncidentManager()
    control_manager = AgentRunControlManager()
    incident = incident_manager.create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="external_write_violation",
            agent_id="agent-1",
            org_id="org-1",
            project_id="project-1",
            summary="External write violation.",
        ),
        opened_at=NOW,
    )

    control = incident_manager.create_run_control_mitigation(
        incident.incident_id,
        control_manager,
        applied_by="admin-1",
        control_type="pause",
        applied_at=NOW,
    )

    assert control.control_type == "pause"
    assert control.agent_id == "agent-1"
    assert control.metadata["incident_id"] == incident.incident_id
    assert incident_manager.mitigation_actions[0].run_control_id == control.control_id
    assert "run_control_created" in [event.action for event in incident_manager.audit_events]


def test_resolve_requires_rationale() -> None:
    manager = AgentIncidentManager()
    incident = manager.create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="approval_bypass_attempt",
            agent_id="agent-1",
            summary="Approval bypass attempt.",
        ),
        opened_at=NOW,
    )

    with pytest.raises(AgentIncidentError, match="Resolve requires rationale"):
        manager.resolve_incident(
            incident.incident_id,
            resolved_by="admin-1",
            rationale="",
            resolved_at=NOW,
        )

    resolved = manager.resolve_incident(
        incident.incident_id,
        resolved_by="admin-1",
        rationale="Blocked and reviewed with sponsor.",
        resolved_at=NOW,
    )
    assert resolved.status == "resolved"
    assert resolved.metadata["resolution_rationale"] == "Blocked and reviewed with sponsor."


def test_incident_export_redacts_secrets() -> None:
    manager = AgentIncidentManager()
    incident = manager.create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="secret_exposure_attempt",
            agent_id="agent-1",
            summary="Agent exposed api_key=supersecretvalue and sk-1234567890abcdef.",
            metadata={
                "raw_output": "token: abcdefghijklmnop",
                "nested": {"password": "password: leakedpassword"},
            },
        ),
        opened_at=NOW,
    )
    manager.add_mitigation_action(
        incident.incident_id,
        action_type="redaction",
        summary="Removed secret=anothersecretvalue from transcript.",
        created_by="admin-1",
        created_at=NOW,
    )

    report = manager.export_incident_report(incident.incident_id, exported_at=NOW)
    serialized = report.model_dump_json()

    assert report.redacted is True
    assert "supersecretvalue" not in serialized
    assert "sk-1234567890abcdef" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "anothersecretvalue" not in serialized
    assert "[REDACTED" in serialized
