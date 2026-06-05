from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from molecule_ranker.copilot.action_queue import CoPilotActionQueue
from molecule_ranker.copilot.dashboard import render_dashboard_html, render_dashboard_payload
from molecule_ranker.copilot.escalation import EscalationManager
from molecule_ranker.copilot.memory import CoPilotMemory
from molecule_ranker.copilot.schemas import (
    AutonomyLevel,
    CampaignCoPilotSession,
    CampaignEvent,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotEscalation,
    CoPilotStatusUpdate,
    CoPilotTrigger,
)

PermissionSet = set[str]


@dataclass(frozen=True)
class CoPilotAPIResponse:
    status_code: int
    json: Any | None = None
    text: str = ""


class CoPilotAPIRepository:
    def __init__(
        self,
        *,
        sessions: list[CampaignCoPilotSession] | None = None,
        events: list[CampaignEvent] | None = None,
        triggers: list[CoPilotTrigger] | None = None,
        actions: list[CoPilotAction] | None = None,
        escalations: list[CoPilotEscalation] | None = None,
        status_updates: list[CoPilotStatusUpdate] | None = None,
        action_queue: CoPilotActionQueue | None = None,
        escalation_manager: EscalationManager | None = None,
        memory: CoPilotMemory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self.sessions: dict[str, CampaignCoPilotSession] = {
            session.copilot_session_id: session for session in sessions or []
        }
        self.events = events or []
        self.triggers = triggers or []
        self.action_queue = action_queue or CoPilotActionQueue(now=self._now)
        for action in actions or []:
            self.action_queue.queue_action(action)
        self.escalation_manager = escalation_manager or EscalationManager(now=self._now)
        for escalation in escalations or []:
            self.escalation_manager.escalations[escalation.escalation_id] = escalation
        self.status_updates = status_updates or []
        self.memory = memory or CoPilotMemory(now=self._now)

    @property
    def actions(self) -> list[CoPilotAction]:
        return list(self.action_queue.actions.values())

    @property
    def escalations(self) -> list[CoPilotEscalation]:
        return list(self.escalation_manager.escalations.values())

    def create_session(self, payload: dict[str, Any]) -> CampaignCoPilotSession:
        campaign_id = str(payload.get("campaign_id", "campaign-default"))
        session_id = str(
            payload.get(
                "copilot_session_id",
                f"copilot-session-{campaign_id}-{len(self.sessions) + 1}",
            )
        )
        autonomy_level = str(payload.get("autonomy_level", "observe_only"))
        if autonomy_level not in {
            "observe_only",
            "suggest_only",
            "execute_safe_actions",
            "execute_with_approval",
            "supervised_auto",
        }:
            autonomy_level = "observe_only"
        session = CampaignCoPilotSession(
            copilot_session_id=session_id,
            campaign_id=campaign_id,
            project_id=self._optional_str(payload.get("project_id")),
            program_id=self._optional_str(payload.get("program_id")),
            status="active",
            autonomy_level=cast(AutonomyLevel, autonomy_level),
            started_at=self._now(),
            stopped_at=None,
            last_check_at=None,
            metadata=dict(payload.get("metadata", {}))
            if isinstance(payload.get("metadata", {}), dict)
            else {},
        )
        self.sessions[session_id] = session
        return session

    def _optional_str(self, value: Any) -> str | None:
        return value if isinstance(value, str) else None


class CoPilotAPIApp:
    def __init__(self, repository: CoPilotAPIRepository | None = None) -> None:
        self.repository = repository or CoPilotAPIRepository()
        self.routes = {
            "POST /api/v2/copilot/sessions",
            "GET /api/v2/copilot/sessions",
            "GET /api/v2/copilot/sessions/{id}",
            "POST /api/v2/copilot/sessions/{id}/pause",
            "POST /api/v2/copilot/sessions/{id}/resume",
            "GET /api/v2/copilot/events",
            "GET /api/v2/copilot/triggers",
            "GET /api/v2/copilot/actions",
            "POST /api/v2/copilot/actions/{id}/approve",
            "POST /api/v2/copilot/actions/{id}/reject",
            "GET /api/v2/copilot/escalations",
            "POST /api/v2/copilot/escalations/{id}/acknowledge",
            "POST /api/v2/copilot/escalations/{id}/resolve",
            "GET /copilot",
        }

    def handle(
        self,
        method: str,
        path: str,
        *,
        permissions: PermissionSet | None = None,
        actor_id: str = "user",
        actor_type: str = "human",
        json_body: dict[str, Any] | None = None,
    ) -> CoPilotAPIResponse:
        permissions = permissions or set()
        json_body = json_body or {}
        method = method.upper()
        parts = [part for part in path.strip("/").split("/") if part]

        if path == "/copilot" and method == "GET":
            if not self._allowed(permissions, "copilot:read"):
                return self._forbidden()
            return CoPilotAPIResponse(status_code=200, text=self._dashboard_html())

        if parts[:3] != ["api", "v2", "copilot"]:
            return self._not_found()
        resource = parts[3] if len(parts) > 3 else ""

        if resource == "sessions":
            return self._sessions_route(
                method,
                parts[4:],
                permissions=permissions,
                json_body=json_body,
            )
        if resource == "events" and method == "GET":
            return self._read_collection(permissions, self.repository.events)
        if resource == "triggers" and method == "GET":
            return self._read_collection(permissions, self.repository.triggers)
        if resource == "actions":
            return self._actions_route(
                method,
                parts[4:],
                permissions=permissions,
                actor_id=actor_id,
                actor_type=actor_type,
                json_body=json_body,
            )
        if resource == "escalations":
            return self._escalations_route(
                method,
                parts[4:],
                permissions=permissions,
                actor_id=actor_id,
                json_body=json_body,
            )
        return self._not_found()

    def _sessions_route(
        self,
        method: str,
        tail: list[str],
        *,
        permissions: PermissionSet,
        json_body: dict[str, Any],
    ) -> CoPilotAPIResponse:
        if not tail and method == "POST":
            if not self._allowed(permissions, "copilot:start"):
                return self._forbidden()
            return self._json(201, self.repository.create_session(json_body))
        if not tail and method == "GET":
            return self._read_collection(permissions, list(self.repository.sessions.values()))
        if len(tail) == 1 and method == "GET":
            if not self._allowed(permissions, "copilot:read"):
                return self._forbidden()
            session = self.repository.sessions.get(tail[0])
            return self._json(200, session) if session is not None else self._not_found()
        if len(tail) == 2 and method == "POST" and tail[1] in {"pause", "resume"}:
            required = "copilot:pause" if tail[1] == "pause" else "copilot:start"
            if not self._allowed(permissions, required):
                return self._forbidden()
            session = self.repository.sessions.get(tail[0])
            if session is None:
                return self._not_found()
            session.status = "paused" if tail[1] == "pause" else "active"
            if tail[1] == "pause":
                session.stopped_at = self.repository._now()
            return self._json(200, session)
        return self._not_found()

    def _actions_route(
        self,
        method: str,
        tail: list[str],
        *,
        permissions: PermissionSet,
        actor_id: str,
        actor_type: str,
        json_body: dict[str, Any],
    ) -> CoPilotAPIResponse:
        if not tail and method == "GET":
            return self._read_collection(permissions, self.repository.actions)
        if len(tail) != 2 or method != "POST":
            return self._not_found()
        action_id, operation = tail
        if operation not in {"approve", "reject"}:
            return self._not_found()
        if not self._allowed(permissions, "copilot:approve_action"):
            return self._forbidden()
        if actor_id.lower() in {"codex", "copilot"}:
            return self._forbidden("Codex cannot approve or reject co-pilot actions.")
        try:
            if operation == "approve":
                result = self.repository.action_queue.approve_action(
                    action_id,
                    approver_id=actor_id,
                    approver_type=actor_type,
                )
            else:
                result = self.repository.action_queue.reject_action(
                    action_id,
                    approver_id=actor_id,
                    approver_type=actor_type,
                    reason=str(json_body.get("reason", "No reason provided.")),
                )
        except KeyError:
            return self._not_found()
        if isinstance(result, CoPilotActionResult):
            return self._forbidden(result.summary)
        return self._json(200, result)

    def _escalations_route(
        self,
        method: str,
        tail: list[str],
        *,
        permissions: PermissionSet,
        actor_id: str,
        json_body: dict[str, Any],
    ) -> CoPilotAPIResponse:
        if not tail and method == "GET":
            return self._read_collection(permissions, self.repository.escalations)
        if len(tail) != 2 or method != "POST":
            return self._not_found()
        escalation_id, operation = tail
        if operation not in {"acknowledge", "resolve"}:
            return self._not_found()
        if not self._allowed(permissions, "copilot:approve_action"):
            return self._forbidden()
        try:
            if operation == "acknowledge":
                escalation = self.repository.escalation_manager.acknowledge_escalation(
                    escalation_id,
                    actor_id=actor_id,
                    actor_role=str(json_body.get("actor_role", "human")),
                )
            else:
                escalation = self.repository.escalation_manager.resolve_escalation(
                    escalation_id,
                    actor_id=actor_id,
                    actor_role=str(json_body.get("actor_role", "human")),
                    resolution_note=str(json_body.get("resolution_note", "")),
                )
        except KeyError:
            return self._not_found()
        return self._json(200, escalation)

    def _read_collection(
        self,
        permissions: PermissionSet,
        values: list[Any],
    ) -> CoPilotAPIResponse:
        if not self._allowed(permissions, "copilot:read"):
            return self._forbidden()
        return self._json(200, values)

    def _dashboard_html(self) -> str:
        payload = render_dashboard_payload(
            sessions=list(self.repository.sessions.values()),
            events=self.repository.events,
            triggers=self.repository.triggers,
            actions=self.repository.actions,
            escalations=self.repository.escalations,
            status_updates=self.repository.status_updates,
            memory_records=self.repository.memory.records,
        )
        return render_dashboard_html(payload)

    def _allowed(self, permissions: PermissionSet, permission: str) -> bool:
        return "copilot:admin" in permissions or permission in permissions

    def _json(self, status_code: int, value: Any) -> CoPilotAPIResponse:
        return CoPilotAPIResponse(status_code=status_code, json=self._dump(value))

    def _dump(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [self._dump(item) for item in value]
        return value

    def _forbidden(self, message: str = "Permission denied.") -> CoPilotAPIResponse:
        return CoPilotAPIResponse(status_code=403, json={"error": message})

    def _not_found(self) -> CoPilotAPIResponse:
        return CoPilotAPIResponse(status_code=404, json={"error": "Not found."})


def create_copilot_api_app(
    repository: CoPilotAPIRepository | None = None,
) -> CoPilotAPIApp:
    return CoPilotAPIApp(repository=repository)
