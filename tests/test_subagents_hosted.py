from __future__ import annotations

from fastapi.testclient import TestClient

from molecule_ranker.server import create_app


def test_subagent_api_rbac_enforced(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    viewer_headers = _create_user_and_share(
        client,
        admin_headers,
        email="viewer@example.com",
        role="viewer",
    )

    denied = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Diagnose why campaign X stalled",
            "project_id": "workspace-a",
            "skill": "analyze_failed_campaign",
        },
        headers=viewer_headers,
    )
    allowed = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Diagnose why campaign X stalled",
            "project_id": "workspace-a",
            "skill": "analyze_failed_campaign",
        },
        headers=admin_headers,
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["session"]["metadata"]["skill_name"] == "analyze_failed_campaign"


def test_subagent_session_dashboard_renders(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Improve generated candidates",
            "project_id": "workspace-a",
            "skill": "improve_generated_candidates",
        },
        headers=admin_headers,
    ).json()["session"]
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    list_page = client.get("/dashboard/subagents/sessions")
    detail_page = client.get(f"/dashboard/subagents/sessions/{session['multi_agent_session_id']}")

    assert list_page.status_code == 200, list_page.text
    assert "Subagent sessions" in list_page.text
    assert "workspace-a" in list_page.text
    assert detail_page.status_code == 200, detail_page.text
    for expected in [
        "Session timeline",
        "Task graph",
        "Subagent messages",
        "Subagent results",
        "Critiques",
        "Consensus summary",
        "Approval queue",
        "Guardrail findings",
    ]:
        assert expected in detail_page.text


def test_subagent_messages_sanitized(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session_id = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Diagnose project",
            "project_id": "workspace-a",
            "metadata": {
                "inject_secret_message": "Connector token sk-live-secret should be hidden."
            },
        },
        headers=admin_headers,
    ).json()["session"]["multi_agent_session_id"]

    messages = client.get(
        f"/api/v2/subagents/sessions/{session_id}/messages",
        headers=admin_headers,
    )

    assert messages.status_code == 200, messages.text
    body = messages.text
    assert "sk-live-secret" not in body
    assert "[REDACTED]" in body


def test_subagent_guardrail_findings_visible(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session_id = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Improve generated candidates",
            "project_id": "workspace-a",
            "skill": "improve_generated_candidates",
            "metadata": {"force_guardrail_finding": True},
        },
        headers=admin_headers,
    ).json()["session"]["multi_agent_session_id"]
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    critiques = client.get(
        f"/api/v2/subagents/sessions/{session_id}/critiques",
        headers=admin_headers,
    )
    page = client.get(f"/dashboard/subagents/sessions/{session_id}")

    assert critiques.status_code == 200, critiques.text
    assert "Guardrail review required" in critiques.text
    assert page.status_code == 200, page.text
    assert "Guardrail review required" in page.text


def test_subagent_result_critique_api_works(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session = client.post(
        "/api/v2/subagents/sessions",
        json={
            "goal": "Diagnose campaign evidence and guardrails",
            "project_id": "workspace-a",
        },
        headers=admin_headers,
    ).json()["session"]
    result_id = session["results"][0]["result_id"]

    response = client.post(
        f"/api/v2/subagents/results/{result_id}/critique",
        json={"critic": "guardrail_sentinel"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result_id"] == result_id
    assert payload["critiques"]
    assert payload["consensus"]["consensus_status"] in {
        "agreed",
        "requires_human_review",
    }


def _client(tmp_path) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret="subagent-hosted-secret-change-me-32",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
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


def _create_project(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/projects",
        json={"workspace_id": "workspace-a", "name": "Research"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _create_user_and_share(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    email: str,
    role: str,
) -> dict[str, str]:
    created = client.post(
        "/admin/users",
        json={"email": email, "password": "User-password-1", "roles": ["user"]},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.post(
        "/projects/workspace-a/share",
        json={"role": role, "user_id": created.json()["user"]["user_id"]},
        headers=admin_headers,
    )
    return _login(client, email, "User-password-1")
