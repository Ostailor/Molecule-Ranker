from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from molecule_ranker.platform.database import users
from molecule_ranker.server import create_app


def test_e2e_api_permissions_enforced(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    viewer = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
    )
    assert viewer.status_code == 200, viewer.text
    viewer_headers = _api_login(client, "viewer@example.com", "Viewer-password-1")

    denied = client.get("/api/v2/e2e/workflows", headers=viewer_headers)
    allowed_create = client.post(
        "/api/v2/e2e/workflows",
        headers=admin_headers,
        json={"workflow": "full_discovery_loop", "mode": "mocked", "disease": "Demo disease"},
    )
    assert allowed_create.status_code == 200, allowed_create.text
    workflow_id = allowed_create.json()["workflow"]["workflow"]["workflow_id"]
    allowed_list = client.get("/api/v2/e2e/workflows", headers=admin_headers)
    allowed_detail = client.get(f"/api/v2/e2e/workflows/{workflow_id}", headers=admin_headers)
    allowed_validate = client.post(
        f"/api/v2/e2e/workflows/{workflow_id}/validate",
        headers=admin_headers,
    )
    allowed_resume = client.post(
        f"/api/v2/e2e/workflows/{workflow_id}/resume",
        headers=admin_headers,
    )
    allowed_cancel = client.post(
        f"/api/v2/e2e/workflows/{workflow_id}/cancel",
        headers=admin_headers,
    )

    assert denied.status_code == 403
    assert allowed_list.status_code == 200, allowed_list.text
    assert allowed_list.json()["workflows"]
    assert allowed_detail.status_code == 200, allowed_detail.text
    assert allowed_validate.status_code == 200, allowed_validate.text
    assert allowed_resume.status_code == 200, allowed_resume.text
    assert allowed_cancel.status_code == 200, allowed_cancel.text
    assert allowed_cancel.json()["workflow"]["workflow"]["status"] == "cancelled"


def test_e2e_dashboard_renders_and_lineage_visible(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/api/v2/e2e/workflows",
        headers=headers,
        json={"workflow": "full_discovery_loop", "mode": "mocked", "disease": "Demo disease"},
    )
    assert created.status_code == 200, created.text
    workflow_id = created.json()["workflow"]["workflow"]["workflow_id"]
    _web_login(client)

    listing = client.get("/dashboard/e2e")
    detail = client.get(f"/dashboard/e2e/{workflow_id}")
    lineage = client.get(f"/api/v2/e2e/workflows/{workflow_id}/lineage", headers=headers)
    bundle = client.get(f"/api/v2/e2e/workflows/{workflow_id}/bundle", headers=headers)

    assert listing.status_code == 200, listing.text
    assert "E2E workflow list" in listing.text
    assert workflow_id in listing.text
    assert detail.status_code == 200, detail.text
    assert "Step timeline" in detail.text
    assert "Lineage" in detail.text
    assert "Result bundle" in detail.text
    assert "Validation report" in detail.text
    assert "External sync summary" in detail.text
    assert "produced" in detail.text
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["lineage"]
    assert bundle.status_code == 200, bundle.text
    assert bundle.json()["bundle"]["workflow_id"] == workflow_id


def test_e2e_partial_failure_visible_on_dashboard(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/api/v2/e2e/workflows",
        headers=headers,
        json={
            "workflow": "full_discovery_loop",
            "mode": "read_only_live",
            "disease": "Demo disease",
            "partial_on_live_data_unavailable": True,
            "unavailable_required_data": ["generation"],
        },
    )
    assert created.status_code == 200, created.text
    workflow_id = created.json()["workflow"]["workflow"]["workflow_id"]
    _web_login(client)

    detail = client.get(f"/dashboard/e2e/{workflow_id}")

    assert detail.status_code == 200, detail.text
    assert "partially_succeeded" in detail.text
    assert "optional step can be resumed" in detail.text
    assert "live required data unavailable" in detail.text


def _app(tmp_path: Path):
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


def _web_login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "Admin-password-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _grant_metadata_permissions(
    client: TestClient,
    user_id: str,
    permissions: list[str],
) -> None:
    app: Any = client.app
    with app.state.platform_database.engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.user_id == user_id)
            .values(metadata_json={"permissions": permissions})
        )
