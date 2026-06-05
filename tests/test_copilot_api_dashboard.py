from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.copilot.schemas import CoPilotAction
from molecule_ranker.server.copilot_api import CoPilotAPIRepository, create_copilot_api_app

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _action() -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id="action-1",
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type="run_campaign_replan",
        tool_name=None,
        tool_args={},
        side_effect_level="db_write",
        risk_level="high",
        requires_approval=True,
        approval_reason="Human approval required before campaign replan.",
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata={},
    )


def test_copilot_api_permissions_enforced():
    app = create_copilot_api_app(repository=CoPilotAPIRepository(now=lambda: NOW))

    denied = app.handle("GET", "/api/v2/copilot/sessions", permissions=set())
    allowed = app.handle(
        "GET",
        "/api/v2/copilot/sessions",
        permissions={"copilot:read"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json == []


def test_copilot_action_approval_route_works_for_human_approver():
    repository = CoPilotAPIRepository(now=lambda: NOW)
    repository.action_queue.queue_action(_action())
    app = create_copilot_api_app(repository=repository)

    response = app.handle(
        "POST",
        "/api/v2/copilot/actions/action-1/approve",
        permissions={"copilot:approve_action"},
        actor_id="user-1",
        actor_type="human",
    )

    assert response.status_code == 200
    assert isinstance(response.json, dict)
    assert response.json["status"] == "approved"
    assert repository.action_queue.get_action("action-1").status == "approved"


def test_copilot_dashboard_renders_required_pages():
    repository = CoPilotAPIRepository(now=lambda: NOW)
    repository.action_queue.queue_action(_action())
    app = create_copilot_api_app(repository=repository)

    response = app.handle("GET", "/copilot", permissions={"copilot:read"})

    assert response.status_code == 200
    html = response.text
    for label in [
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
    ]:
        assert label in html


def test_codex_cannot_approve_copilot_action():
    repository = CoPilotAPIRepository(now=lambda: NOW)
    repository.action_queue.queue_action(_action())
    app = create_copilot_api_app(repository=repository)

    response = app.handle(
        "POST",
        "/api/v2/copilot/actions/action-1/approve",
        permissions={"copilot:approve_action"},
        actor_id="codex",
        actor_type="service_account",
    )

    assert response.status_code == 403
    assert repository.action_queue.get_action("action-1").status == "queued"
