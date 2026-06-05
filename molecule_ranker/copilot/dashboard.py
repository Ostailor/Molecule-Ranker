from __future__ import annotations

from typing import Any

from molecule_ranker.copilot.policy import HUMAN_APPROVAL_REQUIRED_FOR
from molecule_ranker.copilot.schemas import (
    CampaignCoPilotSession,
    CampaignEvent,
    CoPilotAction,
    CoPilotEscalation,
    CoPilotMemoryRecord,
    CoPilotStatusUpdate,
    CoPilotTrigger,
)

_DASHBOARD_PAGES = [
    "Co-pilot overview",
    "Active campaigns",
    "Event feed",
    "Trigger queue",
    "Action queue",
    "Approval queue",
    "Escalations",
    "Status updates",
    "Memory insights",
    "Policy settings",
]


def render_dashboard_payload(
    *,
    sessions: list[CampaignCoPilotSession],
    events: list[CampaignEvent],
    triggers: list[CoPilotTrigger],
    actions: list[CoPilotAction],
    escalations: list[CoPilotEscalation],
    status_updates: list[CoPilotStatusUpdate],
    memory_records: list[CoPilotMemoryRecord] | None = None,
) -> dict[str, Any]:
    return {
        "title": "Campaign Co-Pilot Dashboard",
        "pages": _DASHBOARD_PAGES,
        "sessions": [session.model_dump(mode="json") for session in sessions],
        "events": [event.model_dump(mode="json") for event in events],
        "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
        "actions": [action.model_dump(mode="json") for action in actions],
        "escalations": [escalation.model_dump(mode="json") for escalation in escalations],
        "status_updates": [status.model_dump(mode="json") for status in status_updates],
        "memory_records": [
            record.model_dump(mode="json") for record in memory_records or []
        ],
        "policy": {
            "scope": "research_workflow_planning_only",
            "human_approval_required_for": HUMAN_APPROVAL_REQUIRED_FOR,
            "permissions": [
                "copilot:read",
                "copilot:start",
                "copilot:pause",
                "copilot:approve_action",
                "copilot:admin",
            ],
        },
    }


def render_dashboard_html(payload: dict[str, Any]) -> str:
    page_nav = "\n".join(
        f"<li><a href=\"#{_anchor(page)}\">{page}</a></li>" for page in payload["pages"]
    )
    session_rows = "\n".join(
        "<tr>"
        f"<td>{session['copilot_session_id']}</td>"
        f"<td>{session['campaign_id']}</td>"
        f"<td>{session['status']}</td>"
        f"<td>{session['autonomy_level']}</td>"
        "</tr>"
        for session in payload["sessions"]
    ) or "<tr><td colspan=\"4\">No active co-pilot sessions.</td></tr>"
    event_items = _list_items(payload["events"], "event_id", "summary")
    trigger_items = _list_items(payload["triggers"], "trigger_id", "rationale")
    action_items = _list_items(payload["actions"], "copilot_action_id", "action_type")
    approval_items = _list_items(
        [action for action in payload["actions"] if action["requires_approval"]],
        "copilot_action_id",
        "approval_reason",
    )
    escalation_items = _list_items(payload["escalations"], "escalation_id", "message")
    status_items = _list_items(
        payload["status_updates"],
        "status_update_id",
        "executive_summary",
    )
    memory_items = _list_items(
        payload["memory_records"],
        "memory_id",
        "recommended_action_type",
    )
    policy_items = "\n".join(
        f"<li>{permission}</li>" for permission in payload["policy"]["permissions"]
    )
    overview_counts = (
        f"{len(payload['sessions'])} sessions, "
        f"{len(payload['events'])} events, "
        f"{len(payload['actions'])} actions."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{payload["title"]}</title>
</head>
<body>
  <h1>{payload["title"]}</h1>
  <p>Research workflow planning only. Risky actions require human approval.</p>
  <nav><ul>{page_nav}</ul></nav>
  <section id="co-pilot-overview">
    <h2>Co-pilot overview</h2>
    <p>{overview_counts}</p>
  </section>
  <section id="active-campaigns">
    <h2>Active campaigns</h2>
    <table>
    <thead>
      <tr><th>Session</th><th>Campaign</th><th>Status</th><th>Autonomy</th></tr>
    </thead>
    <tbody>{session_rows}</tbody>
    </table>
  </section>
  <section id="event-feed"><h2>Event feed</h2><ul>{event_items}</ul></section>
  <section id="trigger-queue"><h2>Trigger queue</h2><ul>{trigger_items}</ul></section>
  <section id="action-queue"><h2>Action queue</h2><ul>{action_items}</ul></section>
  <section id="approval-queue"><h2>Approval queue</h2><ul>{approval_items}</ul></section>
  <section id="escalations"><h2>Escalations</h2><ul>{escalation_items}</ul></section>
  <section id="status-updates"><h2>Status updates</h2><ul>{status_items}</ul></section>
  <section id="memory-insights"><h2>Memory insights</h2><ul>{memory_items}</ul></section>
  <section id="policy-settings"><h2>Policy settings</h2><ul>{policy_items}</ul></section>
</body>
</html>
"""


def _anchor(label: str) -> str:
    return label.lower().replace(" ", "-")


def _list_items(items: list[dict[str, Any]], id_key: str, text_key: str) -> str:
    if not items:
        return "<li>None</li>"
    return "\n".join(
        f"<li><strong>{item.get(id_key, '')}</strong>: {item.get(text_key) or 'Pending'}</li>"
        for item in items
    )
