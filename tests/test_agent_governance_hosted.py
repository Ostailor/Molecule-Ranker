from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import update

from molecule_ranker.agent_governance.incidents import (
    AgentIncidentManager,
    IncidentStore,
    IncidentTriggerEvent,
)
from molecule_ranker.platform.database import users
from molecule_ranker.server import create_app


def test_governance_api_permissions_enforced(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    unauthenticated = client.get("/api/v2/governance/policies")
    assert unauthenticated.status_code in {401, 403}

    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    allowed = client.get("/api/v2/governance/policies", headers=admin_headers)
    assert allowed.status_code == 200, allowed.text

    viewer = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert viewer.status_code == 200, viewer.text
    viewer_headers = _api_login(client, "viewer@example.com", "Viewer-password-1")
    blocked = client.get("/api/v2/governance/policies", headers=viewer_headers)
    assert blocked.status_code == 403

    _grant_metadata_permissions(
        client,
        viewer.json()["user"]["user_id"],
        ["governance:read"],
    )
    readable = client.get("/api/v2/governance/policies", headers=viewer_headers)
    assert readable.status_code == 200, readable.text


def test_governance_policy_page_renders(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/api/v2/governance/policies",
        json=_policy_payload(),
        headers=headers,
    )
    assert created.status_code == 200, created.text

    _web_login(client, "admin@example.com", "Admin-password-1")
    response = client.get("/dashboard/governance/policies")

    assert response.status_code == 200, response.text
    assert "Active policies" in response.text
    assert "Enterprise autonomy cap" in response.text
    assert "execute_with_approval" in response.text


def test_governance_incident_page_renders_and_redacts(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_incident(tmp_path, summary="Secret exposure attempt with sk-secret-value")

    _web_login(client, "admin@example.com", "Admin-password-1")
    response = client.get("/dashboard/governance/incidents")

    assert response.status_code == 200, response.text
    assert "Incidents" in response.text
    assert "[REDACTED]" in response.text
    assert "sk-secret-value" not in response.text


def test_governance_kill_switch_works(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    headers = _api_login(client, "admin@example.com", "Admin-password-1")

    response = client.post(
        "/api/v2/governance/run-controls",
        json={
            "control_id": "kill-project-1",
            "control_type": "kill_switch",
            "project_id": "project-1",
            "reason": "Emergency governance stop",
            "metadata": {"session_action": "cancel"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["control"]["control_type"] == "kill_switch"
    assert payload["decision"]["allowed"] is False
    assert payload["decision"]["session_action"] == "cancel"

    _web_login(client, "admin@example.com", "Admin-password-1")
    dashboard = client.get("/dashboard/governance/kill-switches")
    assert dashboard.status_code == 200, dashboard.text
    assert "Kill switches" in dashboard.text
    assert "Emergency governance stop" in dashboard.text
    assert "kill_switch" in dashboard.text


def test_governance_api_redacts_secrets(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _seed_incident(tmp_path, summary="Leaked token: sk-secret-value")
    headers = _api_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/api/v2/governance/incidents", headers=headers)

    assert response.status_code == 200, response.text
    assert "[REDACTED]" in response.text
    assert "sk-secret-value" not in response.text


def test_governance_policy_simulator_page_renders_decision(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get(
        "/dashboard/governance/policy-simulator",
        params={
            "agent_id": "agent-1",
            "tool": "run_external_sync_write",
            "action": "run_external_sync_write",
            "side_effect_level": "external_write",
        },
    )

    assert response.status_code == 200, response.text
    assert "Policy simulator" in response.text
    assert "Simulation result" in response.text
    assert "approval_required" in response.text
    assert "external_write" in response.text


def _app(tmp_path: Path) -> Any:
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _web_login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/dashboard"


def _grant_metadata_permissions(
    client: TestClient,
    user_id: str,
    permissions: list[str],
) -> None:
    app = cast(Any, client.app)
    database = app.state.platform_database
    with database.engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.user_id == user_id)
            .values(metadata_json={"permissions": permissions})
        )


def _policy_payload() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "policy_id": "policy-enterprise-autonomy",
        "org_id": None,
        "project_id": None,
        "policy_name": "Enterprise autonomy cap",
        "policy_version": "2.6.0",
        "applies_to_roles": ["runtime_agent", "subagent"],
        "applies_to_agents": [],
        "max_autonomy_level": "execute_with_approval",
        "allowed_tool_categories": ["ranking", "summary"],
        "denied_tool_categories": ["external_write"],
        "allowed_side_effect_levels": ["none", "artifact_write"],
        "approval_required_actions": ["external_write"],
        "blocked_actions": ["generated_molecule_advancement_without_human_approval"],
        "budget_policy_id": None,
        "guardrail_profile": "enterprise_default",
        "incident_policy_id": None,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
        "metadata": {"secret_token": "sk-secret-value"},
    }


def _seed_incident(tmp_path: Path, *, summary: str) -> None:
    manager = AgentIncidentManager(
        store=IncidentStore(
            tmp_path / ".molecule-ranker" / "agent-governance" / "incidents.json"
        )
    )
    manager.create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="secret_exposure_attempt",
            agent_id="agent-1",
            project_id="project-1",
            summary=summary,
            metadata={"api_key": "sk-secret-value"},
        ),
        incident_id="incident-secret-1",
    )
