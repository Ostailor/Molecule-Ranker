from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.server import create_app


def test_admin_dashboard_pages_require_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()
    _web_login(client, "viewer@example.com", "Viewer-password-1")

    for path in [
        "/dashboard/admin",
        "/dashboard/admin/users",
        "/dashboard/admin/organizations",
        "/dashboard/admin/teams",
        "/dashboard/admin/memberships",
        "/dashboard/admin/roles",
        "/dashboard/admin/service-accounts",
        "/dashboard/admin/project-permissions",
        "/dashboard/admin/integrations",
        "/dashboard/admin/audit",
        "/dashboard/admin/jobs",
        "/dashboard/admin/workers",
        "/dashboard/admin/health",
        "/dashboard/admin/codex-worker",
        "/dashboard/admin/policies",
        "/dashboard/admin/support",
        "/dashboard/admin/backup-restore",
        "/dashboard/admin/slo",
        "/dashboard/admin/release-validation",
        "/dashboard/admin/feedback",
    ]:
        response = client.get(path)
        assert response.status_code == 403, path


def test_non_admin_blocked_from_admin_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    response = client.get("/admin/users", headers=viewer_headers)
    support = client.get("/admin/support-console", headers=viewer_headers)

    assert response.status_code == 403
    assert support.status_code == 403


def test_admin_support_console_redacts_failed_job_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client, "admin@example.com", "Admin-password-1")
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    queue = PlatformJobQueue(app.state.platform_database)
    job = queue.enqueue(job_type="ranking", requested_by=admin, project_id="project-1")
    queue.fail(job, RuntimeError("failed with service_token=secret-token-value"))

    api = client.get("/admin/support-console", headers=headers)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")
    page = client.get("/dashboard/admin/support")

    assert api.status_code == 200, api.text
    assert page.status_code == 200, page.text
    assert "Admin support console" in page.text
    assert "Failed jobs" in page.text
    assert "secret-token-value" not in api.text
    assert "secret-token-value" not in page.text
    assert "[REDACTED]" in api.text


def test_admin_support_action_requires_admin_and_audits(tmp_path: Path) -> None:
    client = _client(tmp_path)
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    queue = PlatformJobQueue(app.state.platform_database)
    job = queue.enqueue(job_type="ranking", requested_by=admin, project_id="project-1")
    queue.fail(job, RuntimeError("transient worker failed"))
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    blocked = client.post(f"/admin/support/jobs/{job.job_id}/retry", headers=viewer_headers)
    retried = client.post(f"/admin/support/jobs/{job.job_id}/retry", headers=admin_headers)
    audit = client.get("/admin/audit", headers=admin_headers)

    assert blocked.status_code == 403
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert any(
        event["event_type"] == "admin_support_retry_failed_job"
        for event in audit.json()["events"]
    )


def test_admin_user_action_writes_audit_event(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/admin/users",
        json={"email": "user@example.com", "password": "User-password-1"},
        headers=headers,
    )
    user_id = created.json()["user"]["user_id"]

    disabled = client.post(f"/admin/users/{user_id}/deactivate", headers=headers)
    audit = client.get("/admin/audit", headers=headers)

    assert disabled.status_code == 200, disabled.text
    assert audit.status_code == 200
    assert any(event["event_type"] == "user_disabled" for event in audit.json()["events"])


def test_service_account_token_not_recoverable_after_creation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client, "admin@example.com", "Admin-password-1")
    admin_user_id = client.get("/auth/me", headers=headers).json()["user"]["user_id"]

    created = client.post(
        "/admin/service-accounts",
        json={
            "name": "automation",
            "user_id": admin_user_id,
            "scopes": ["project:read"],
        },
        headers=headers,
    )
    token = created.json()["access_token"]
    listed = client.get("/admin/service-accounts", headers=headers)

    assert created.status_code == 200, created.text
    assert created.json()["shown_once"] is True
    assert listed.status_code == 200
    assert "access_token" not in listed.text
    assert token not in listed.text


def test_enterprise_admin_console_pages_render_and_show_rbac_policy_context(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    app.state.platform_database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=admin.user_id,
        user_id=admin.user_id,
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/admin/roles": ["RBAC matrix", "project_owner", "artifact:export"],
        "/dashboard/admin/project-permissions": ["Project permissions", "project-1", "viewer"],
        "/dashboard/admin/integrations": [
            "Integration administration",
            "Dry-run/read-only by default",
        ],
        "/dashboard/admin/workers": ["Workers", "Queue backlog", "Codex worker"],
        "/dashboard/admin/policies": [
            "Policy explanations",
            "generated_molecules_require_review_before_export",
        ],
        "/dashboard/admin/backup-restore": ["Backup/restore", "Disaster recovery"],
        "/dashboard/admin/release-validation": [
            "Release and validation package",
            "V2 contract validation",
        ],
    }

    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, f"{snippet} missing from {path}"


def test_admin_console_redacts_secrets_from_audit_and_integration_pages(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    app.state.platform_database.write_audit(
        "integration_probe",
        actor_user_id=admin.user_id,
        summary="Checked integration with authorization=Bearer secret-token-value",
        metadata={"api_key": "sk-secret-value", "nested": {"password": "plain-secret"}},
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    audit = client.get("/dashboard/admin/audit")
    integrations = client.get("/dashboard/admin/integrations")

    assert audit.status_code == 200
    assert integrations.status_code == 200
    combined = audit.text + integrations.text
    assert "secret-token-value" not in combined
    assert "sk-secret-value" not in combined
    assert "plain-secret" not in combined
    assert "[REDACTED]" in combined


def test_dashboard_admin_backup_action_is_audited(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.post("/dashboard/admin/backup-restore/verify", follow_redirects=False)
    audit = client.get("/dashboard/admin/audit")

    assert response.status_code == 303
    assert audit.status_code == 200
    assert "admin_support_run_backup_verification" in audit.text


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret="test-hosted-secret-value-with-at-least-32-chars",
            platform_db_path=tmp_path / "platform.sqlite",
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
