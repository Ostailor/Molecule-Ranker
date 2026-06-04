from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import update

from molecule_ranker.platform.database import users
from molecule_ranker.server import create_app
from molecule_ranker.tool_ecosystem.dashboard import seeded_tool_marketplace


def test_tool_dashboard_pages_require_auth(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    for path in [
        "/dashboard/tools",
        "/dashboard/tools/installed",
        "/dashboard/tools/packages/pkg-example-summary",
        "/dashboard/tools/tool/plugin.summary.safe_summary",
        "/dashboard/tools/packages/pkg-quarantined-evidence/security",
        "/dashboard/tools/approvals",
        "/dashboard/tools/packages/pkg-example-summary/usage",
        "/dashboard/tools/project-allowlist",
        "/dashboard/tools/skills",
        "/dashboard/tools/workflows",
        "/dashboard/tools/mcp-gateway",
        "/dashboard/agent/reliability",
    ]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"


def test_tool_dashboard_permission_enforcement(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    viewer = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    reader = client.post(
        "/admin/users",
        json={"email": "reader@example.com", "password": "Reader-password-1"},
        headers=admin_headers,
    )
    assert viewer.status_code == 200, viewer.text
    assert reader.status_code == 200, reader.text
    _grant_metadata_permissions(
        client,
        reader.json()["user"]["user_id"],
        ["tool:read"],
    )

    client.cookies.clear()
    _web_login(client, "viewer@example.com", "Viewer-password-1")
    blocked = client.get("/dashboard/tools")

    client.cookies.clear()
    _web_login(client, "reader@example.com", "Reader-password-1")
    readable = client.get("/dashboard/tools")
    approval_blocked = client.get("/dashboard/tools/approvals")

    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")
    approval_allowed = client.get("/dashboard/tools/approvals")

    assert blocked.status_code == 403
    assert readable.status_code == 200, readable.text
    assert approval_blocked.status_code == 403
    assert approval_allowed.status_code == 200, approval_allowed.text


def test_tool_dashboard_clean_app_does_not_show_seed_packages(tmp_path: Path) -> None:
    client = TestClient(_clean_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    marketplace = client.get("/dashboard/tools")
    installed = client.get("/dashboard/tools/installed")

    assert marketplace.status_code == 200, marketplace.text
    assert installed.status_code == 200, installed.text
    assert "pkg-example-summary" not in marketplace.text
    assert "pkg-quarantined-evidence" not in marketplace.text
    assert "No local/internal tool packages installed yet." in marketplace.text
    assert "No installed packages." in installed.text


def test_agent_reliability_dashboard_shows_v24_repair_eval(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/agent/reliability")

    assert response.status_code == 200, response.text
    assert "Agent reliability dashboard" in response.text
    assert "Repair eval metrics" in response.text
    assert "Agents may not repair scientific truth" in response.text


def test_tool_dashboard_shows_security_findings_and_redacts_secrets(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/tools/packages/pkg-quarantined-evidence/security")

    assert response.status_code == 200, response.text
    assert "Security scan result" in response.text
    assert "critical" in response.text
    assert "evidence_creation_without_validator" in response.text
    assert "external_write_without_approval" in response.text
    assert "[REDACTED]" in response.text
    assert "sk-secret-value" not in response.text
    assert "hidden-token" not in response.text


def test_tool_dashboard_escapes_untrusted_package_and_tool_text(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    marketplace = client.get("/dashboard/tools")
    tool_detail = client.get("/dashboard/tools/tool/plugin.quarantine.import_evidence")

    assert marketplace.status_code == 200, marketplace.text
    assert tool_detail.status_code == 200, tool_detail.text
    assert "<script>" not in marketplace.text
    assert "<script>" not in tool_detail.text
    assert "&lt;script&gt;" in marketplace.text
    assert "&lt;script&gt;" in tool_detail.text


def test_tool_dashboard_labels_governed_tool_visibility_and_validators(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    marketplace = client.get("/dashboard/tools")
    package_detail = client.get("/dashboard/tools/packages/pkg-example-summary")
    mcp_status = client.get("/dashboard/tools/mcp-gateway")

    assert marketplace.status_code == 200, marketplace.text
    assert package_detail.status_code == 200, package_detail.text
    assert mcp_status.status_code == 200, mcp_status.text
    assert "QUARANTINED until scan and approval" in marketplace.text
    assert "external write - approval required" in package_detail.text
    assert "Codex visible" in package_detail.text
    assert "artifact_metric_validator" in package_detail.text
    assert "Approved tools exposed" in mcp_status.text
    assert "plugin.summary.safe_summary" in mcp_status.text


def _app(tmp_path: Path):
    app = create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )
    app.state.tool_marketplace = seeded_tool_marketplace()
    return app


def _clean_app(tmp_path: Path):
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
