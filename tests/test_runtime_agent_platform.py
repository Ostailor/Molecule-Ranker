from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.server import create_app


def test_runtime_agent_api_permissions_enforced(tmp_path: Path) -> None:
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
        "/api/v2/agent/sessions",
        json={
            "goal": "Rank Alzheimer disease",
            "project_id": "workspace-a",
            "autonomy_level": "execute_safe_tools",
        },
        headers=viewer_headers,
    )
    allowed = client.post(
        "/api/v2/agent/sessions",
        json={
            "goal": "Rank Alzheimer disease",
            "project_id": "workspace-a",
            "autonomy_level": "execute_safe_tools",
        },
        headers=admin_headers,
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["session"]["status"] == "created"


def test_runtime_agent_dashboard_renders(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session = client.post(
        "/api/v2/agent/sessions",
        json={
            "goal": "Rank Alzheimer disease",
            "project_id": "workspace-a",
            "autonomy_level": "execute_safe_tools",
        },
        headers=admin_headers,
    ).json()["session"]
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    pages = {
        "/dashboard/agent/sessions": ["Agent sessions", "workspace-a"],
        f"/dashboard/agent/sessions/{session['session_id']}": [
            "Agent session detail",
            "Action plan view",
            "Step execution view",
            "Approval queue",
            "Runtime audit log",
            "Guardrail report",
            "Produced artifacts",
            "Next actions",
        ],
        "/dashboard/agent/approvals": ["Approval queue"],
        "/dashboard/agent/audit": ["Runtime audit log"],
    }

    for path, expected in pages.items():
        response = client.get(path)
        assert response.status_code == 200, response.text
        for text in expected:
            assert text in response.text


def test_runtime_agent_approval_queue_works(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    created = client.post(
        "/api/v2/agent/sessions",
        json={
            "goal": "Run external sync write",
            "project_id": "workspace-a",
            "autonomy_level": "execute_with_approval",
        },
        headers=admin_headers,
    )
    session_id = created.json()["session"]["session_id"]
    plan = client.post(f"/api/v2/agent/sessions/{session_id}/plan", headers=admin_headers)
    execute = client.post(f"/api/v2/agent/sessions/{session_id}/execute", headers=admin_headers)

    assert plan.status_code == 200, plan.text
    assert execute.status_code == 200, execute.text
    assert execute.json()["status"] == "approval_required"
    approvals = client.get("/api/v2/agent/sessions", headers=admin_headers).json()["approvals"]
    approval_id = approvals[0]["approval_id"]
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")
    queue = client.get("/dashboard/agent/approvals")

    assert queue.status_code == 200
    assert approval_id in queue.text
    approved = client.post(
        f"/api/v2/agent/approvals/{approval_id}/approve",
        json={"decided_by": "admin-user", "rationale": "Approved external sync."},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["status"] == "approved"


def test_runtime_agent_codex_cannot_self_approve(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers)
    session_id = client.post(
        "/api/v2/agent/sessions",
        json={
            "goal": "Run external sync write",
            "project_id": "workspace-a",
            "autonomy_level": "execute_with_approval",
        },
        headers=admin_headers,
    ).json()["session"]["session_id"]
    client.post(f"/api/v2/agent/sessions/{session_id}/plan", headers=admin_headers)
    client.post(f"/api/v2/agent/sessions/{session_id}/execute", headers=admin_headers)
    approval_id = client.get("/api/v2/agent/sessions", headers=admin_headers).json()[
        "approvals"
    ][0]["approval_id"]

    denied = client.post(
        f"/api/v2/agent/approvals/{approval_id}/approve",
        json={"decided_by": "codex", "rationale": "Self-approved."},
        headers=admin_headers,
    )

    assert denied.status_code == 403
    assert "Codex cannot approve" in denied.text


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret="runtime-agent-hosted-secret-change-me",
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
