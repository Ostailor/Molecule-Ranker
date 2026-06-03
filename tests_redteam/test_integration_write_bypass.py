from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.integrations.schemas import ExternalSystem
from molecule_ranker.server import create_app


def test_external_write_without_permission_is_blocked(tmp_path: Path) -> None:
    app = create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )
    client = TestClient(app)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    system = ExternalSystem(
        external_system_id="redteam-system",
        name="Red-team Generic REST",
        system_type="generic_rest",
        default_mode="write_enabled",
        base_url="https://example.invalid",
    )
    created = client.post(
        "/api/v2/integrations/systems",
        headers=admin_headers,
        json={"system": system.model_dump(mode="json")},
    )
    assert created.status_code == 200, created.text
    viewer = client.post(
        "/api/v2/admin/users",
        headers=admin_headers,
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
    )
    assert viewer.status_code == 200, viewer.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    response = client.post(
        "/api/v2/integrations/systems/redteam-system/sync",
        headers=viewer_headers,
        json={"sync_request": {"mode": "write_enabled"}},
    )

    assert response.status_code == 403
    assert "permission denied" in response.text.lower()


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/v2/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
