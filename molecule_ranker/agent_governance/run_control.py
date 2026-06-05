from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.policies import AUTONOMY_ORDER
from molecule_ranker.agent_governance.schemas import (
    AgentGovernanceAutonomyLevel,
    AgentGovernanceSchema,
    AgentRunControl,
    AgentRunControlType,
)

RunControlDecisionStatus = Literal[
    "allowed",
    "blocked",
    "approval_required",
    "paused",
    "disabled",
    "autonomy_restricted",
]
RunControlAuditAction = Literal["applied", "cleared", "checked", "expired"]

DEFAULT_RUN_CONTROL_STORE_PATH = Path(".molecule-ranker/agent-governance/run-controls.json")
SPECIAL_KILL_SWITCH_TARGETS = {
    "codex_worker",
    "external_integration_agent",
    "generated_molecule_workflow",
}


class RunControlRequest(BaseModel):
    agent_id: str | None = None
    agent_type: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    action: str | None = None
    autonomy_level: AgentGovernanceAutonomyLevel = "observe_only"
    side_effect_level: str | None = None
    workflow_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunControlAuditEvent(AgentGovernanceSchema):
    audit_event_id: str
    control_id: str | None
    action: RunControlAuditAction
    actor_id: str
    occurred_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunControlDecision(BaseModel):
    status: RunControlDecisionStatus
    allowed: bool
    requires_approval: bool
    reasons: list[str] = Field(default_factory=list)
    active_controls: list[AgentRunControl] = Field(default_factory=list)
    effective_autonomy_cap: AgentGovernanceAutonomyLevel | None = None
    session_action: Literal["none", "pause", "cancel"] = "none"
    audit_event: RunControlAuditEvent | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunControlStore:
    def __init__(self, path: Path | str = DEFAULT_RUN_CONTROL_STORE_PATH) -> None:
        self.path = Path(path)

    def list_controls(self) -> list[AgentRunControl]:
        return [
            AgentRunControl.model_validate(item)
            for item in self._load().get("controls", [])
            if isinstance(item, dict)
        ]

    def list_audit_events(self) -> list[RunControlAuditEvent]:
        return [
            RunControlAuditEvent.model_validate(item)
            for item in self._load().get("audit_events", [])
            if isinstance(item, dict)
        ]

    def save_controls(
        self,
        controls: list[AgentRunControl],
        audit_events: list[RunControlAuditEvent],
    ) -> None:
        self._save(
            {
                "controls": [control.model_dump(mode="json") for control in controls],
                "audit_events": [event.model_dump(mode="json") for event in audit_events],
            }
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"controls": [], "audit_events": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"controls": [], "audit_events": []}
        raw.setdefault("controls", [])
        raw.setdefault("audit_events", [])
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class AgentRunControlManager:
    """Apply and evaluate V2.6 emergency and administrative run controls."""

    def __init__(
        self,
        *,
        controls: list[AgentRunControl] | None = None,
        audit_events: list[RunControlAuditEvent] | None = None,
        store: RunControlStore | None = None,
    ) -> None:
        self.store = store
        if store is not None:
            self.controls = store.list_controls()
            self.audit_events = store.list_audit_events()
        else:
            self.controls = list(controls or [])
            self.audit_events = list(audit_events or [])

    def apply_control(
        self,
        *,
        control_type: AgentRunControlType,
        reason: str,
        applied_by: str,
        org_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        applied_at: datetime | None = None,
        expires_at: datetime | None = None,
        control_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunControl:
        now = applied_at or datetime.now(UTC)
        control = AgentRunControl(
            control_id=control_id or f"agent-run-control-{uuid4().hex[:12]}",
            org_id=org_id,
            project_id=project_id,
            agent_id=agent_id,
            control_type=control_type,
            reason=reason,
            applied_by=applied_by,
            applied_at=now,
            expires_at=expires_at,
            active=True,
            metadata=metadata or {},
        )
        if control_type == "resume":
            self._clear_matching_control_type(
                control,
                cleared_type="pause",
                cleared_by=applied_by,
                cleared_at=now,
                reason=reason,
            )
        elif control_type == "enable":
            self._clear_matching_control_type(
                control,
                cleared_type="disable",
                cleared_by=applied_by,
                cleared_at=now,
                reason=reason,
            )
        self.controls = [
            item for item in self.controls if item.control_id != control.control_id
        ]
        self.controls.append(control)
        self._audit(
            control_id=control.control_id,
            action="applied",
            actor_id=applied_by,
            summary=f"Applied run control {control.control_type}.",
            metadata=control.model_dump(mode="json"),
        )
        self._persist()
        return control

    def clear_control(
        self,
        control_id: str,
        *,
        cleared_by: str,
        cleared_at: datetime | None = None,
        reason: str = "Run control cleared.",
    ) -> AgentRunControl:
        control = self._require_control(control_id)
        now = cleared_at or datetime.now(UTC)
        updated = control.model_copy(
            update={
                "active": False,
                "metadata": {
                    **control.metadata,
                    "cleared_by": cleared_by,
                    "cleared_at": now.isoformat(),
                    "clear_reason": reason,
                },
            }
        )
        self._replace_control(updated)
        self._audit(
            control_id=control_id,
            action="cleared",
            actor_id=cleared_by,
            summary=reason,
            metadata=updated.model_dump(mode="json"),
        )
        self._persist()
        return updated

    def list_controls(
        self,
        *,
        active_only: bool = False,
        org_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> list[AgentRunControl]:
        current_time = now or datetime.now(UTC)
        self.expire_controls(now=current_time)
        controls = self.controls
        if active_only:
            controls = [
                control
                for control in controls
                if _control_active(control, now=current_time)
            ]
        if org_id is not None:
            controls = [
                control for control in controls if control.org_id in {None, org_id}
            ]
        if project_id is not None:
            controls = [
                control
                for control in controls
                if control.project_id in {None, project_id}
            ]
        if agent_id is not None:
            controls = [
                control for control in controls if control.agent_id in {None, agent_id}
            ]
        return sorted(controls, key=lambda control: (control.applied_at, control.control_id))

    def active_dashboard_controls(
        self,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return [
            control.model_dump(mode="json")
            for control in self.list_controls(active_only=True, now=now)
        ]

    def evaluate(
        self,
        request: RunControlRequest | None = None,
        *,
        now: datetime | None = None,
        **kwargs: Any,
    ) -> RunControlDecision:
        current_time = now or datetime.now(UTC)
        active_request = request or RunControlRequest.model_validate(kwargs)
        matching = [
            control
            for control in self.list_controls(active_only=True, now=current_time)
            if _control_matches_request(control, active_request)
        ]
        reasons: list[str] = []
        required_approval = False
        effective_cap: AgentGovernanceAutonomyLevel | None = None

        kill_switch = next(
            (control for control in matching if control.control_type == "kill_switch"),
            None,
        )
        if kill_switch is not None:
            session_action = _kill_switch_session_action(kill_switch)
            event = self._audit(
                control_id=kill_switch.control_id,
                action="checked",
                actor_id=active_request.agent_id or "system",
                summary="Run control kill switch blocked action.",
                metadata={
                    "request": active_request.model_dump(mode="json"),
                    "session_action": session_action,
                },
            )
            self._persist()
            return RunControlDecision(
                status="blocked",
                allowed=False,
                requires_approval=False,
                reasons=[f"Kill switch active: {kill_switch.reason}"],
                active_controls=matching,
                session_action=session_action,
                audit_event=event,
            )

        disable = next(
            (control for control in matching if control.control_type == "disable"),
            None,
        )
        if disable is not None:
            event = self._audit(
                control_id=disable.control_id,
                action="checked",
                actor_id=active_request.agent_id or "system",
                summary="Run control disabled agent action.",
                metadata={"request": active_request.model_dump(mode="json")},
            )
            self._persist()
            return RunControlDecision(
                status="disabled",
                allowed=False,
                requires_approval=False,
                reasons=[f"Agent disabled: {disable.reason}"],
                active_controls=matching,
                session_action="cancel",
                audit_event=event,
            )

        pause = next((control for control in matching if control.control_type == "pause"), None)
        if pause is not None:
            event = self._audit(
                control_id=pause.control_id,
                action="checked",
                actor_id=active_request.agent_id or "system",
                summary="Run control paused agent action.",
                metadata={"request": active_request.model_dump(mode="json")},
            )
            self._persist()
            return RunControlDecision(
                status="paused",
                allowed=False,
                requires_approval=False,
                reasons=[f"Agent paused: {pause.reason}"],
                active_controls=matching,
                session_action="pause",
                audit_event=event,
            )

        for control in matching:
            if control.control_type == "require_approval_all_actions":
                required_approval = True
                reasons.append(f"Approval required by run control {control.control_id}.")
            elif control.control_type == "restrict_autonomy":
                control_cap = _control_autonomy_cap(control)
                if control_cap is not None:
                    effective_cap = _stricter_autonomy_cap(effective_cap, control_cap)

        if effective_cap is not None and _autonomy_gt(
            active_request.autonomy_level,
            effective_cap,
        ):
            event = self._audit(
                control_id=None,
                action="checked",
                actor_id=active_request.agent_id or "system",
                summary="Run control restricted autonomy.",
                metadata={
                    "request": active_request.model_dump(mode="json"),
                    "effective_autonomy_cap": effective_cap,
                },
            )
            self._persist()
            return RunControlDecision(
                status="autonomy_restricted",
                allowed=False,
                requires_approval=False,
                reasons=[f"Autonomy restricted to {effective_cap}."],
                active_controls=matching,
                effective_autonomy_cap=effective_cap,
                audit_event=event,
            )

        if required_approval:
            event = self._audit(
                control_id=None,
                action="checked",
                actor_id=active_request.agent_id or "system",
                summary="Run control requires approval for action.",
                metadata={"request": active_request.model_dump(mode="json")},
            )
            self._persist()
            return RunControlDecision(
                status="approval_required",
                allowed=False,
                requires_approval=True,
                reasons=reasons,
                active_controls=matching,
                effective_autonomy_cap=effective_cap,
                audit_event=event,
            )

        event = self._audit(
            control_id=None,
            action="checked",
            actor_id=active_request.agent_id or "system",
            summary="No active run control blocked action.",
            metadata={"request": active_request.model_dump(mode="json")},
        )
        self._persist()
        return RunControlDecision(
            status="allowed",
            allowed=True,
            requires_approval=False,
            reasons=["No active run control blocks this action."],
            active_controls=matching,
            effective_autonomy_cap=effective_cap,
            audit_event=event,
        )

    def expire_controls(self, *, now: datetime | None = None) -> list[AgentRunControl]:
        current_time = now or datetime.now(UTC)
        expired: list[AgentRunControl] = []
        for control in list(self.controls):
            if (
                control.active
                and control.expires_at is not None
                and control.expires_at <= current_time
            ):
                updated = control.model_copy(
                    update={
                        "active": False,
                        "metadata": {
                            **control.metadata,
                            "expired_at": current_time.isoformat(),
                        },
                    }
                )
                self._replace_control(updated)
                expired.append(updated)
                self._audit(
                    control_id=control.control_id,
                    action="expired",
                    actor_id="system",
                    summary=f"Expired run control {control.control_id}.",
                    metadata=updated.model_dump(mode="json"),
                )
        if expired:
            self._persist()
        return expired

    def _clear_matching_control_type(
        self,
        resume_or_enable: AgentRunControl,
        *,
        cleared_type: AgentRunControlType,
        cleared_by: str,
        cleared_at: datetime,
        reason: str,
    ) -> None:
        for control in list(self.controls):
            if control.control_type != cleared_type or not control.active:
                continue
            if not _same_control_scope(control, resume_or_enable):
                continue
            updated = control.model_copy(
                update={
                    "active": False,
                    "metadata": {
                        **control.metadata,
                        "cleared_by": cleared_by,
                        "cleared_at": cleared_at.isoformat(),
                        "clear_reason": reason,
                    },
                }
            )
            self._replace_control(updated)
            self._audit(
                control_id=control.control_id,
                action="cleared",
                actor_id=cleared_by,
                summary=reason,
                metadata=updated.model_dump(mode="json"),
            )

    def _require_control(self, control_id: str) -> AgentRunControl:
        for control in self.controls:
            if control.control_id == control_id:
                return control
        raise ValueError(f"Unknown run control: {control_id}")

    def _replace_control(self, updated: AgentRunControl) -> None:
        self.controls = [
            updated if control.control_id == updated.control_id else control
            for control in self.controls
        ]

    def _audit(
        self,
        *,
        control_id: str | None,
        action: RunControlAuditAction,
        actor_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunControlAuditEvent:
        event = RunControlAuditEvent(
            audit_event_id=f"agent-run-control-audit-{uuid4().hex[:12]}",
            control_id=control_id,
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
            self.store.save_controls(self.controls, self.audit_events)


def _control_active(control: AgentRunControl, *, now: datetime) -> bool:
    return control.active and (control.expires_at is None or control.expires_at > now)


def _control_matches_request(control: AgentRunControl, request: RunControlRequest) -> bool:
    if control.org_id is not None and control.org_id != request.org_id:
        return False
    if control.project_id is not None and control.project_id != request.project_id:
        return False
    if control.agent_id is not None and control.agent_id != request.agent_id:
        return False
    if control.control_type == "kill_switch":
        target = str(control.metadata.get("kill_switch_target", "")).strip()
        if target in SPECIAL_KILL_SWITCH_TARGETS:
            return _special_kill_switch_matches(target, request)
    campaign_id = control.metadata.get("campaign_id")
    if campaign_id is not None and campaign_id != request.campaign_id:
        return False
    return True


def _special_kill_switch_matches(target: str, request: RunControlRequest) -> bool:
    if target == "codex_worker":
        return request.agent_type == "codex_worker"
    if target == "external_integration_agent":
        return (
            request.agent_type == "external_integration_agent"
            or request.side_effect_level == "external_write"
            or request.metadata.get("integration_agent") is True
        )
    if target == "generated_molecule_workflow":
        return (
            request.workflow_type == "generated_molecule"
            or request.metadata.get("generated_molecule_workflow") is True
            or (
                request.action is not None
                and "generated_molecule" in request.action
            )
        )
    return False


def _control_autonomy_cap(
    control: AgentRunControl,
) -> AgentGovernanceAutonomyLevel | None:
    cap = control.metadata.get("max_autonomy_level") or control.metadata.get(
        "restricted_autonomy_level"
    )
    if isinstance(cap, str) and cap in AUTONOMY_ORDER:
        return cast(AgentGovernanceAutonomyLevel, cap)
    return None


def _autonomy_gt(
    requested: AgentGovernanceAutonomyLevel,
    cap: AgentGovernanceAutonomyLevel,
) -> bool:
    return AUTONOMY_ORDER[requested] > AUTONOMY_ORDER[cap]


def _stricter_autonomy_cap(
    current: AgentGovernanceAutonomyLevel | None,
    candidate: AgentGovernanceAutonomyLevel,
) -> AgentGovernanceAutonomyLevel:
    if current is None:
        return candidate
    return candidate if AUTONOMY_ORDER[candidate] < AUTONOMY_ORDER[current] else current


def _kill_switch_session_action(control: AgentRunControl) -> Literal["pause", "cancel"]:
    action = str(control.metadata.get("session_action", "pause")).strip().lower()
    return "cancel" if action == "cancel" else "pause"


def _same_control_scope(left: AgentRunControl, right: AgentRunControl) -> bool:
    return (
        left.org_id == right.org_id
        and left.project_id == right.project_id
        and left.agent_id == right.agent_id
        and left.metadata.get("campaign_id") == right.metadata.get("campaign_id")
    )


__all__ = [
    "AgentRunControl",
    "AgentRunControlManager",
    "RunControlAuditEvent",
    "RunControlDecision",
    "RunControlRequest",
    "RunControlStore",
]
