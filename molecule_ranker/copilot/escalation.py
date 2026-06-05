from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from molecule_ranker.copilot.schemas import (
    CoPilotAction,
    CoPilotEscalation,
    CoPilotTrigger,
    EscalationStatus,
    EscalationType,
    Priority,
)

_DEFAULT_ROLE_ROUTING: dict[EscalationType, str] = {
    "human_approval_required": "campaign_owner",
    "safety_review_required": "safety_reviewer",
    "scientific_disagreement": "scientific_reviewer",
    "policy_block": "policy_owner",
    "repeated_failure": "operations_owner",
    "budget_exceeded": "budget_owner",
    "external_system_issue": "integration_owner",
    "guardrail_failure": "safety_reviewer",
    "campaign_blocked": "program_lead",
    "missing_input": "campaign_owner",
}

_HUMAN_ROLE_REQUIRED_TYPES = {
    "human_approval_required",
    "safety_review_required",
    "guardrail_failure",
    "campaign_blocked",
}


class EscalationManager:
    def __init__(
        self,
        *,
        role_routing: dict[str, str] | None = None,
        reminder_timeout: timedelta | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.role_routing = {**_DEFAULT_ROLE_ROUTING, **(role_routing or {})}
        self.reminder_timeout = reminder_timeout
        self._now = now or (lambda: datetime.now(UTC))
        self.escalations: dict[str, CoPilotEscalation] = {}
        self.notifications: list[dict[str, str]] = []
        self.audit_log: list[dict[str, Any]] = []

    def from_trigger(
        self,
        trigger: CoPilotTrigger,
        *,
        action: CoPilotAction | None = None,
    ) -> CoPilotEscalation | None:
        if not trigger.requires_human_attention and not (
            action is not None and action.requires_approval
        ):
            return None
        escalation_type = self._type_from_trigger(trigger, action=action)
        return self.create_escalation(
            campaign_id=trigger.campaign_id,
            escalation_type=escalation_type,
            priority=trigger.priority,
            message=trigger.rationale,
            trigger_id=trigger.trigger_id,
            action_id=action.copilot_action_id if action is not None else None,
            artifact_ids=self._string_list(trigger.metadata.get("artifact_ids")),
            assigned_role=self._assigned_role(escalation_type),
            metadata={
                "trigger_type": trigger.trigger_type,
                "requires_human_role": self._requires_human_role(
                    escalation_type,
                    trigger_metadata=trigger.metadata,
                ),
            },
        )

    def create_escalation(
        self,
        *,
        campaign_id: str,
        escalation_type: EscalationType,
        priority: Priority,
        message: str,
        trigger_id: str | None = None,
        action_id: str | None = None,
        artifact_ids: list[str] | None = None,
        assigned_role: str | None = None,
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CoPilotEscalation:
        role = assigned_role or self._assigned_role(escalation_type)
        created = created_at or self._now()
        escalation = CoPilotEscalation(
            escalation_id=self._escalation_id(
                campaign_id=campaign_id,
                trigger_id=trigger_id,
                action_id=action_id,
                escalation_type=escalation_type,
            ),
            campaign_id=campaign_id,
            trigger_id=trigger_id,
            action_id=action_id,
            escalation_type=escalation_type,
            priority=priority,
            assigned_role=role,
            message=message,
            artifact_ids=artifact_ids or [],
            status="open",
            created_at=created,
            resolved_at=None,
            metadata={
                **(metadata or {}),
                "last_transition_at": created.isoformat(),
                "reminder_count": 0,
            },
        )
        self.escalations[escalation.escalation_id] = escalation
        self._audit(escalation, "created", actor_id="copilot")
        if role is not None:
            self._audit(escalation, "assigned", actor_id="copilot")
            self._notify(escalation, message=message)
        return escalation

    def assign_escalation(
        self,
        escalation_id: str,
        *,
        assigned_role: str,
        actor_id: str = "copilot",
    ) -> CoPilotEscalation:
        escalation = self.escalations[escalation_id]
        updated = self._copy(
            escalation,
            assigned_role=assigned_role,
            metadata={
                **escalation.metadata,
                "assigned_by": actor_id,
                "last_transition_at": self._now().isoformat(),
            },
        )
        self.escalations[escalation_id] = updated
        self._audit(updated, "assigned", actor_id=actor_id)
        self._notify(updated, message=updated.message)
        return updated

    def acknowledge_escalation(
        self,
        escalation_id: str,
        *,
        actor_id: str,
        actor_role: str,
    ) -> CoPilotEscalation:
        escalation = self.escalations[escalation_id]
        updated = self._copy(
            escalation,
            status="acknowledged",
            metadata={
                **escalation.metadata,
                "acknowledged_by": actor_id,
                "acknowledged_role": actor_role,
                "acknowledged_at": self._now().isoformat(),
                "last_transition_at": self._now().isoformat(),
            },
        )
        self.escalations[escalation_id] = updated
        self._audit(updated, "acknowledged", actor_id=actor_id)
        return updated

    def resolve_escalation(
        self,
        escalation_id: str,
        *,
        actor_id: str,
        actor_role: str,
        resolution_note: str,
    ) -> CoPilotEscalation:
        escalation = self.escalations[escalation_id]
        if self._resolution_blocked(
            escalation,
            actor_id=actor_id,
            actor_role=actor_role,
        ):
            self._audit(escalation, "resolution_denied", actor_id=actor_id)
            return escalation
        updated = self._copy(
            escalation,
            status="resolved",
            resolved_at=self._now(),
            metadata={
                **escalation.metadata,
                "resolved_by": actor_id,
                "resolved_role": actor_role,
                "resolution_note": resolution_note,
                "last_transition_at": self._now().isoformat(),
            },
        )
        self.escalations[escalation_id] = updated
        self._audit(updated, "resolved", actor_id=actor_id)
        return updated

    def auto_remind_due(self) -> list[str]:
        if self.reminder_timeout is None:
            return []
        reminded: list[str] = []
        for escalation in list(self.escalations.values()):
            if escalation.status not in {"open", "acknowledged"}:
                continue
            if self._now() - escalation.created_at < self.reminder_timeout:
                continue
            last_reminded_at = escalation.metadata.get("last_reminded_at")
            if isinstance(last_reminded_at, str):
                continue
            count = self._int_metadata(escalation.metadata, "reminder_count") + 1
            updated = self._copy(
                escalation,
                metadata={
                    **escalation.metadata,
                    "reminder_count": count,
                    "last_reminded_at": self._now().isoformat(),
                },
            )
            self.escalations[updated.escalation_id] = updated
            if updated.assigned_role is not None:
                self.notifications.append(
                    {
                        "escalation_id": updated.escalation_id,
                        "assigned_role": updated.assigned_role,
                        "priority": updated.priority,
                        "message": f"Escalation reminder: {updated.escalation_type}",
                    }
                )
            self._audit(updated, "reminded", actor_id="copilot")
            reminded.append(updated.escalation_id)
        return reminded

    def _type_from_trigger(
        self,
        trigger: CoPilotTrigger,
        *,
        action: CoPilotAction | None,
    ) -> EscalationType:
        detector_type = str(trigger.metadata.get("detector_event_type", ""))
        if detector_type == "guardrail_failure" or trigger.trigger_type == "blocker_detected":
            return "guardrail_failure"
        if trigger.trigger_type == "safety_review_needed":
            return "safety_review_required"
        if trigger.trigger_type == "budget_review_needed":
            return "budget_exceeded"
        if trigger.trigger_type == "repair_needed":
            return "repeated_failure"
        if trigger.metadata.get("missing_input"):
            return "missing_input"
        if trigger.metadata.get("campaign_blocked"):
            return "campaign_blocked"
        if action is not None and action.requires_approval:
            return "human_approval_required"
        return "policy_block"

    def _requires_human_role(
        self,
        escalation_type: EscalationType,
        *,
        trigger_metadata: dict[str, Any],
    ) -> bool:
        return (
            escalation_type in _HUMAN_ROLE_REQUIRED_TYPES
            or trigger_metadata.get("decision_type") == "stage_gate"
            or trigger_metadata.get("campaign_approval") is True
        )

    def _resolution_blocked(
        self,
        escalation: CoPilotEscalation,
        *,
        actor_id: str,
        actor_role: str,
    ) -> bool:
        actor_is_codex = actor_id.lower() in {"codex", "copilot"} or actor_role == "copilot"
        if escalation.escalation_type == "guardrail_failure" and actor_is_codex:
            return True
        requires_human_role = bool(escalation.metadata.get("requires_human_role"))
        return requires_human_role and actor_role in {"copilot", "service_account"}

    def _assigned_role(self, escalation_type: EscalationType) -> str | None:
        return self.role_routing.get(escalation_type)

    def _notify(self, escalation: CoPilotEscalation, *, message: str) -> None:
        if escalation.assigned_role is None:
            return
        self.notifications.append(
            {
                "escalation_id": escalation.escalation_id,
                "assigned_role": escalation.assigned_role,
                "priority": escalation.priority,
                "message": message,
            }
        )
        self._audit(escalation, "notified", actor_id="copilot")

    def _audit(
        self,
        escalation: CoPilotEscalation,
        transition: str,
        *,
        actor_id: str,
    ) -> None:
        self.audit_log.append(
            {
                "escalation_id": escalation.escalation_id,
                "campaign_id": escalation.campaign_id,
                "transition": transition,
                "actor_id": actor_id,
                "status": escalation.status,
                "created_at": self._now(),
            }
        )

    def _copy(
        self,
        escalation: CoPilotEscalation,
        *,
        status: EscalationStatus | None = None,
        assigned_role: str | None = None,
        resolved_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CoPilotEscalation:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if assigned_role is not None:
            updates["assigned_role"] = assigned_role
        if resolved_at is not None:
            updates["resolved_at"] = resolved_at
        if metadata is not None:
            updates["metadata"] = metadata
        return escalation.model_copy(update=updates, deep=True)

    def _escalation_id(
        self,
        *,
        campaign_id: str,
        trigger_id: str | None,
        action_id: str | None,
        escalation_type: EscalationType,
    ) -> str:
        source = trigger_id or action_id or escalation_type
        return f"escalation-{campaign_id}-{source}-{escalation_type}"

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _int_metadata(self, metadata: dict[str, Any], key: str) -> int:
        value = metadata.get(key, 0)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0
