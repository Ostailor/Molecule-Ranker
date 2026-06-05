from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.run_control import AgentRunControlManager
from molecule_ranker.agent_governance.schemas import (
    AgentGovernanceSchema,
    AgentIncident,
    AgentIncidentSeverity,
    AgentIncidentStatus,
    AgentIncidentType,
    AgentPolicyViolation,
    AgentRunControl,
)
from molecule_ranker.codex_backbone.guardrails import redact_secrets

IncidentTriggerType = Literal[
    "critical_guardrail_failure",
    "repeated_guardrail_failures",
    "approval_bypass_attempt",
    "unauthorized_tool_attempt",
    "external_write_violation",
    "secret_exposure_attempt",
    "hallucinated_artifact_attempt",
    "generated_molecule_overclaim",
    "lab_protocol_output",
    "synthesis_output",
    "dosing_output",
    "policy_override_attempt",
    "repeated_failed_repairs",
]
IncidentAuditAction = Literal[
    "created",
    "assigned",
    "triaged",
    "investigating",
    "mitigated",
    "resolved",
    "false_positive",
    "mitigation_added",
    "run_control_created",
    "recertification_required",
    "exported",
]

DEFAULT_INCIDENT_STORE_PATH = Path(".molecule-ranker/agent-governance/incidents.json")
REPEATED_FAILURE_THRESHOLD = 3

TRIGGER_INCIDENT_MAPPING: dict[
    IncidentTriggerType,
    tuple[AgentIncidentSeverity, AgentIncidentType],
] = {
    "critical_guardrail_failure": ("critical", "guardrail_failure"),
    "repeated_guardrail_failures": ("high", "guardrail_failure"),
    "approval_bypass_attempt": ("high", "approval_bypass_attempt"),
    "unauthorized_tool_attempt": ("medium", "unauthorized_tool_attempt"),
    "external_write_violation": ("high", "external_write_violation"),
    "secret_exposure_attempt": ("critical", "secret_exposure_attempt"),
    "hallucinated_artifact_attempt": ("high", "hallucinated_artifact"),
    "generated_molecule_overclaim": ("high", "unsupported_scientific_claim"),
    "lab_protocol_output": ("critical", "guardrail_failure"),
    "synthesis_output": ("critical", "guardrail_failure"),
    "dosing_output": ("critical", "guardrail_failure"),
    "policy_override_attempt": ("high", "policy_violation"),
    "repeated_failed_repairs": ("medium", "repeated_failure"),
}


class AgentIncidentError(ValueError):
    """Raised when an incident operation violates workflow rules."""


class IncidentTriggerEvent(BaseModel):
    trigger_type: IncidentTriggerType
    agent_id: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    summary: str
    count: int = Field(default=1, ge=1)
    severity: AgentIncidentSeverity | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    tool_usage_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentMitigationAction(AgentGovernanceSchema):
    mitigation_id: str
    incident_id: str
    action_type: str
    summary: str
    created_by: str
    created_at: datetime
    run_control_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentAuditEvent(AgentGovernanceSchema):
    audit_event_id: str
    incident_id: str | None
    action: IncidentAuditAction
    actor_id: str
    occurred_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentReport(BaseModel):
    report_id: str
    incident: dict[str, Any]
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    mitigation_actions: list[dict[str, Any]] = Field(default_factory=list)
    requires_recertification: bool
    redacted: bool = True
    exported_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentStore:
    def __init__(self, path: Path | str = DEFAULT_INCIDENT_STORE_PATH) -> None:
        self.path = Path(path)

    def list_incidents(self) -> list[AgentIncident]:
        return [
            AgentIncident.model_validate(item)
            for item in self._load().get("incidents", [])
            if isinstance(item, dict)
        ]

    def list_audit_events(self) -> list[IncidentAuditEvent]:
        return [
            IncidentAuditEvent.model_validate(item)
            for item in self._load().get("audit_events", [])
            if isinstance(item, dict)
        ]

    def list_mitigation_actions(self) -> list[IncidentMitigationAction]:
        return [
            IncidentMitigationAction.model_validate(item)
            for item in self._load().get("mitigation_actions", [])
            if isinstance(item, dict)
        ]

    def save_incidents(
        self,
        incidents: list[AgentIncident],
        audit_events: list[IncidentAuditEvent],
        mitigation_actions: list[IncidentMitigationAction],
    ) -> None:
        self._save(
            {
                "incidents": [incident.model_dump(mode="json") for incident in incidents],
                "audit_events": [event.model_dump(mode="json") for event in audit_events],
                "mitigation_actions": [
                    action.model_dump(mode="json") for action in mitigation_actions
                ],
            }
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"incidents": [], "audit_events": [], "mitigation_actions": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"incidents": [], "audit_events": [], "mitigation_actions": []}
        raw.setdefault("incidents", [])
        raw.setdefault("audit_events", [])
        raw.setdefault("mitigation_actions", [])
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class AgentIncidentManager:
    """Create, triage, mitigate, resolve, and export V2.6 governance incidents."""

    def __init__(
        self,
        *,
        incidents: list[AgentIncident] | None = None,
        audit_events: list[IncidentAuditEvent] | None = None,
        mitigation_actions: list[IncidentMitigationAction] | None = None,
        store: IncidentStore | None = None,
    ) -> None:
        self.store = store
        if store is not None:
            self.incidents = store.list_incidents()
            self.audit_events = store.list_audit_events()
            self.mitigation_actions = store.list_mitigation_actions()
        else:
            self.incidents = list(incidents or [])
            self.audit_events = list(audit_events or [])
            self.mitigation_actions = list(mitigation_actions or [])

    def create_incident_from_trigger(
        self,
        trigger: IncidentTriggerEvent,
        *,
        opened_at: datetime | None = None,
        incident_id: str | None = None,
    ) -> AgentIncident:
        severity, incident_type = _incident_classification(trigger)
        session_ids = list(trigger.session_ids)
        if trigger.session_id is not None:
            session_ids.append(trigger.session_id)
        session_ids = list(dict.fromkeys(session_ids))
        incident = AgentIncident(
            incident_id=incident_id or f"agent-incident-{uuid4().hex[:12]}",
            org_id=trigger.org_id,
            project_id=trigger.project_id,
            agent_id=trigger.agent_id,
            session_id=trigger.session_id,
            severity=trigger.severity or severity,
            incident_type=incident_type,
            summary=trigger.summary,
            artifact_ids=trigger.artifact_ids,
            tool_usage_ids=trigger.tool_usage_ids,
            session_ids=session_ids,
            status="open",
            opened_at=opened_at or datetime.now(UTC),
            resolved_at=None,
            assigned_to=None,
            metadata={
                **trigger.metadata,
                "trigger_type": trigger.trigger_type,
                "trigger_count": trigger.count,
                "requires_recertification": _requires_recertification(
                    trigger.trigger_type,
                    trigger.severity or severity,
                ),
            },
        )
        self.incidents.append(incident)
        self._audit(
            incident_id=incident.incident_id,
            action="created",
            actor_id="system",
            summary=f"Opened incident from trigger {trigger.trigger_type}.",
            metadata=incident.model_dump(mode="json"),
        )
        self._persist()
        return incident

    def list_incidents(
        self,
        *,
        status: AgentIncidentStatus | None = None,
        agent_id: str | None = None,
        include_resolved: bool = True,
    ) -> list[AgentIncident]:
        incidents = self.incidents
        if status is not None:
            incidents = [incident for incident in incidents if incident.status == status]
        if agent_id is not None:
            incidents = [incident for incident in incidents if incident.agent_id == agent_id]
        if not include_resolved:
            incidents = [
                incident
                for incident in incidents
                if incident.status not in {"resolved", "false_positive"}
            ]
        return sorted(incidents, key=lambda incident: (incident.opened_at, incident.incident_id))

    def get_incident(self, incident_id: str) -> AgentIncident:
        return self._require_incident(incident_id)

    def assign_owner(
        self,
        incident_id: str,
        *,
        assigned_to: str,
        assigned_by: str,
    ) -> AgentIncident:
        incident = self._require_incident(incident_id)
        updated = incident.model_copy(update={"assigned_to": assigned_to})
        self._replace_incident(updated)
        self._audit(
            incident_id=incident_id,
            action="assigned",
            actor_id=assigned_by,
            summary=f"Assigned incident to {assigned_to}.",
            metadata={"assigned_to": assigned_to},
        )
        self._persist()
        return updated

    def transition_incident(
        self,
        incident_id: str,
        *,
        status: Literal["triaged", "investigating", "mitigated"],
        actor_id: str,
        rationale: str,
    ) -> AgentIncident:
        if not rationale.strip():
            raise AgentIncidentError("Incident workflow transitions require rationale.")
        incident = self._require_incident(incident_id)
        updated = incident.model_copy(
            update={
                "status": status,
                "metadata": {
                    **incident.metadata,
                    f"{status}_rationale": rationale,
                },
            }
        )
        self._replace_incident(updated)
        self._audit(
            incident_id=incident_id,
            action=cast(IncidentAuditAction, status),
            actor_id=actor_id,
            summary=rationale,
        )
        self._persist()
        return updated

    def add_mitigation_action(
        self,
        incident_id: str,
        *,
        action_type: str,
        summary: str,
        created_by: str,
        created_at: datetime | None = None,
        run_control_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IncidentMitigationAction:
        self._require_incident(incident_id)
        mitigation = IncidentMitigationAction(
            mitigation_id=f"incident-mitigation-{uuid4().hex[:12]}",
            incident_id=incident_id,
            action_type=action_type,
            summary=summary,
            created_by=created_by,
            created_at=created_at or datetime.now(UTC),
            run_control_id=run_control_id,
            metadata=metadata or {},
        )
        self.mitigation_actions.append(mitigation)
        self._audit(
            incident_id=incident_id,
            action="mitigation_added",
            actor_id=created_by,
            summary=summary,
            metadata=mitigation.model_dump(mode="json"),
        )
        self._persist()
        return mitigation

    def create_run_control_mitigation(
        self,
        incident_id: str,
        run_control_manager: AgentRunControlManager,
        *,
        applied_by: str,
        control_type: Literal["pause", "disable", "kill_switch"] = "pause",
        session_action: Literal["pause", "cancel"] = "pause",
        reason: str | None = None,
        applied_at: datetime | None = None,
    ) -> AgentRunControl:
        incident = self._require_incident(incident_id)
        control = run_control_manager.apply_control(
            control_type=control_type,
            reason=reason or f"Mitigation for incident {incident_id}.",
            applied_by=applied_by,
            org_id=incident.org_id,
            project_id=incident.project_id,
            agent_id=incident.agent_id,
            applied_at=applied_at,
            metadata={
                "incident_id": incident_id,
                "session_action": session_action,
                "incident_severity": incident.severity,
                "incident_type": incident.incident_type,
            },
        )
        self.add_mitigation_action(
            incident_id,
            action_type="run_control",
            summary=f"Applied run control {control.control_type}.",
            created_by=applied_by,
            created_at=applied_at,
            run_control_id=control.control_id,
        )
        self._audit(
            incident_id=incident_id,
            action="run_control_created",
            actor_id=applied_by,
            summary=f"Created run control {control.control_id}.",
            metadata=control.model_dump(mode="json"),
        )
        self._persist()
        return control

    def require_recertification(
        self,
        incident_id: str,
        *,
        required_by: str,
        reason: str,
    ) -> AgentIncident:
        incident = self._require_incident(incident_id)
        updated = incident.model_copy(
            update={
                "metadata": {
                    **incident.metadata,
                    "requires_recertification": True,
                    "recertification_reason": reason,
                }
            }
        )
        self._replace_incident(updated)
        self._audit(
            incident_id=incident_id,
            action="recertification_required",
            actor_id=required_by,
            summary=reason,
        )
        self._persist()
        return updated

    def resolve_incident(
        self,
        incident_id: str,
        *,
        resolved_by: str,
        rationale: str,
        false_positive: bool = False,
        resolved_at: datetime | None = None,
    ) -> AgentIncident:
        if not rationale.strip():
            raise AgentIncidentError("Resolve requires rationale.")
        incident = self._require_incident(incident_id)
        status: AgentIncidentStatus = "false_positive" if false_positive else "resolved"
        updated = incident.model_copy(
            update={
                "status": status,
                "resolved_at": resolved_at or datetime.now(UTC),
                "metadata": {
                    **incident.metadata,
                    "resolution_rationale": rationale,
                    "resolved_by": resolved_by,
                },
            }
        )
        self._replace_incident(updated)
        self._audit(
            incident_id=incident_id,
            action="false_positive" if false_positive else "resolved",
            actor_id=resolved_by,
            summary=rationale,
            metadata=updated.model_dump(mode="json"),
        )
        self._persist()
        return updated

    def export_incident_report(
        self,
        incident_id: str,
        *,
        exported_at: datetime | None = None,
    ) -> IncidentReport:
        incident = self._require_incident(incident_id)
        events = [
            event
            for event in self.audit_events
            if event.incident_id == incident_id
        ]
        mitigations = [
            action
            for action in self.mitigation_actions
            if action.incident_id == incident_id
        ]
        report = IncidentReport(
            report_id=f"agent-incident-report-{uuid4().hex[:12]}",
            incident=_redact_json(incident.model_dump(mode="json")),
            audit_events=[
                _redact_json(event.model_dump(mode="json")) for event in events
            ],
            mitigation_actions=[
                _redact_json(action.model_dump(mode="json")) for action in mitigations
            ],
            requires_recertification=bool(
                incident.metadata.get("requires_recertification")
            ),
            exported_at=exported_at or datetime.now(UTC),
            metadata={"redaction": "secrets redacted from all report fields"},
        )
        self._audit(
            incident_id=incident_id,
            action="exported",
            actor_id="system",
            summary="Exported redacted incident report.",
            metadata={"report_id": report.report_id},
        )
        self._persist()
        return report

    def _require_incident(self, incident_id: str) -> AgentIncident:
        for incident in self.incidents:
            if incident.incident_id == incident_id:
                return incident
        raise AgentIncidentError(f"Unknown incident: {incident_id}")

    def _replace_incident(self, updated: AgentIncident) -> None:
        self.incidents = [
            updated if incident.incident_id == updated.incident_id else incident
            for incident in self.incidents
        ]

    def _audit(
        self,
        *,
        incident_id: str | None,
        action: IncidentAuditAction,
        actor_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> IncidentAuditEvent:
        event = IncidentAuditEvent(
            audit_event_id=f"incident-audit-{uuid4().hex[:12]}",
            incident_id=incident_id,
            action=action,
            actor_id=actor_id,
            occurred_at=datetime.now(UTC),
            summary=summary,
            metadata=metadata or {},
        )
        self.audit_events.append(event)
        return event

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save_incidents(
                self.incidents,
                self.audit_events,
                self.mitigation_actions,
            )


def _incident_classification(
    trigger: IncidentTriggerEvent,
) -> tuple[AgentIncidentSeverity, AgentIncidentType]:
    severity, incident_type = TRIGGER_INCIDENT_MAPPING[trigger.trigger_type]
    if trigger.trigger_type in {
        "repeated_guardrail_failures",
        "repeated_failed_repairs",
    } and trigger.count >= REPEATED_FAILURE_THRESHOLD:
        severity = "high"
    return severity, incident_type


def _requires_recertification(
    trigger_type: IncidentTriggerType,
    severity: AgentIncidentSeverity,
) -> bool:
    return severity in {"high", "critical"} or trigger_type in {
        "policy_override_attempt",
        "critical_guardrail_failure",
        "secret_exposure_attempt",
        "lab_protocol_output",
        "synthesis_output",
        "dosing_output",
    }


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


__all__ = [
    "AgentIncident",
    "AgentIncidentError",
    "AgentIncidentManager",
    "AgentPolicyViolation",
    "IncidentAuditEvent",
    "IncidentMitigationAction",
    "IncidentReport",
    "IncidentStore",
    "IncidentTriggerEvent",
]
