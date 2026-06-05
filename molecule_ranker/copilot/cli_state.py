from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.copilot.schemas import (
    CampaignCoPilotSession,
    CampaignEvent,
    CoPilotAction,
    CoPilotStatusUpdate,
    CoPilotTrigger,
)

DEFAULT_COPILOT_STATE_PATH = Path(".molecule-ranker/copilot_state.json")


class CoPilotCLIStateStore:
    def __init__(self, path: Path | str = DEFAULT_COPILOT_STATE_PATH) -> None:
        self.path = Path(path)

    def list_sessions(self) -> list[CampaignCoPilotSession]:
        return [
            CampaignCoPilotSession(**item)
            for item in self._load().get("sessions", [])
            if isinstance(item, dict)
        ]

    def get_session(self, session_id: str) -> CampaignCoPilotSession | None:
        return self._first(
            session for session in self.list_sessions() if session.copilot_session_id == session_id
        )

    def session_for_campaign(self, campaign_id: str) -> CampaignCoPilotSession | None:
        sessions = [
            session for session in self.list_sessions() if session.campaign_id == campaign_id
        ]
        if not sessions:
            return None
        active = [session for session in sessions if session.status == "active"]
        return (active or sessions)[-1]

    def upsert_session(self, session: CampaignCoPilotSession) -> CampaignCoPilotSession:
        state = self._load()
        sessions = [
            item
            for item in state.get("sessions", [])
            if item.get("copilot_session_id") != session.copilot_session_id
        ]
        sessions.append(session.model_dump(mode="json"))
        state["sessions"] = sessions
        self._save(state)
        return session

    def list_events(self, *, campaign_id: str | None = None) -> list[CampaignEvent]:
        events = [
            CampaignEvent(**item)
            for item in self._load().get("events", [])
            if isinstance(item, dict)
        ]
        if campaign_id is None:
            return events
        return [event for event in events if event.campaign_id == campaign_id]

    def append_events(self, events: list[CampaignEvent]) -> None:
        state = self._load()
        existing = {
            str(item.get("event_id")): item
            for item in state.get("events", [])
            if isinstance(item, dict)
        }
        for event in events:
            existing[event.event_id] = event.model_dump(mode="json")
        state["events"] = list(existing.values())
        self._save(state)

    def list_triggers(self, *, campaign_id: str | None = None) -> list[CoPilotTrigger]:
        triggers = [
            CoPilotTrigger(**item)
            for item in self._load().get("triggers", [])
            if isinstance(item, dict)
        ]
        if campaign_id is None:
            return triggers
        return [trigger for trigger in triggers if trigger.campaign_id == campaign_id]

    def append_triggers(self, triggers: list[CoPilotTrigger]) -> None:
        state = self._load()
        existing = {
            str(item.get("trigger_id")): item
            for item in state.get("triggers", [])
            if isinstance(item, dict)
        }
        for trigger in triggers:
            existing[trigger.trigger_id] = trigger.model_dump(mode="json")
        state["triggers"] = list(existing.values())
        self._save(state)

    def list_actions(self, *, campaign_id: str | None = None) -> list[CoPilotAction]:
        actions = [
            CoPilotAction(**item)
            for item in self._load().get("actions", [])
            if isinstance(item, dict)
        ]
        if campaign_id is None:
            return actions
        return [action for action in actions if action.campaign_id == campaign_id]

    def get_action(self, action_id: str) -> CoPilotAction | None:
        return self._first(
            action for action in self.list_actions() if action.copilot_action_id == action_id
        )

    def upsert_action(self, action: CoPilotAction) -> CoPilotAction:
        state = self._load()
        actions = [
            item
            for item in state.get("actions", [])
            if item.get("copilot_action_id") != action.copilot_action_id
        ]
        actions.append(action.model_dump(mode="json"))
        state["actions"] = actions
        self._save(state)
        return action

    def append_status_update(self, update: CoPilotStatusUpdate) -> None:
        state = self._load()
        updates = [
            item
            for item in state.get("status_updates", [])
            if item.get("status_update_id") != update.status_update_id
        ]
        updates.append(update.model_dump(mode="json"))
        state["status_updates"] = updates
        self._save(state)

    def next_session_id(self, campaign_id: str) -> str:
        count = len(
            [session for session in self.list_sessions() if session.campaign_id == campaign_id]
        )
        return f"copilot-session-{campaign_id}-{count + 1}"

    def next_event_id(self, campaign_id: str, event_type: str) -> str:
        count = len(self.list_events(campaign_id=campaign_id))
        return f"{campaign_id}:{event_type}:{count + 1}"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_state()
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict):
            return self._empty_state()
        state = self._empty_state()
        state.update(data)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _empty_state(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "sessions": [],
            "events": [],
            "triggers": [],
            "actions": [],
            "status_updates": [],
        }

    def _first(self, values: Any) -> Any | None:
        for value in values:
            return value
        return None


def utc_now() -> datetime:
    return datetime.now(UTC)
