from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.rbac import (
    filter_visible_projects,
    has_permission,
    is_org_member,
    require_project_access,
)
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server import create_app


def test_viewer_is_read_only(tmp_path: Path) -> None:
    database, _admin, viewer = _database_with_users(tmp_path)
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id="user-admin",
        user_id=viewer.user_id,
    )

    assert has_permission(viewer, "project:read", project_id="project-1", database=database)
    assert has_permission(viewer, "artifact:read", project_id="project-1", database=database)
    assert not has_permission(viewer, "run:create", project_id="project-1", database=database)
    assert not has_permission(
        viewer,
        "experiment:import",
        project_id="project-1",
        database=database,
    )


def test_reviewer_can_review_but_not_update_project(tmp_path: Path) -> None:
    database, _admin, reviewer = _database_with_users(tmp_path)
    database.grant_project_permission(
        project_id="project-1",
        role="reviewer",
        actor_user_id="user-admin",
        user_id=reviewer.user_id,
    )

    assert has_permission(reviewer, "review:write", project_id="project-1", database=database)
    assert has_permission(reviewer, "review:read", project_id="project-1", database=database)
    assert not has_permission(reviewer, "project:update", project_id="project-1", database=database)


def test_scientist_org_member_and_project_editor_can_run_ranking(tmp_path: Path) -> None:
    database, admin, scientist = _database_with_users(tmp_path)
    org = database.create_organization(name="Discovery", created_by_user_id=admin.user_id)
    database.add_membership(user_id=scientist.user_id, org_id=org.org_id, role="scientist")
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=admin.user_id,
        user_id=scientist.user_id,
    )

    assert is_org_member(database, scientist, org_id=org.org_id, roles={"scientist"})
    assert has_permission(scientist, "project:create", org_id=org.org_id, database=database)
    assert has_permission(scientist, "run:create", project_id="project-1", database=database)
    assert not has_permission(
        scientist,
        "admin:manage_users",
        org_id=org.org_id,
        database=database,
    )


def test_service_account_scopes_are_enforced(tmp_path: Path) -> None:
    database, _admin, service_user = _database_with_users(tmp_path)
    database.grant_project_permission(
        project_id="project-1",
        role="project_owner",
        actor_user_id="user-admin",
        user_id=service_user.user_id,
    )
    payload = service_user.model_dump()
    payload["auth_provider"] = "service_account"
    payload["metadata"] = {"scopes": ["project:read"]}
    service_account = UserAccount(**payload)

    assert has_permission(
        service_account,
        "project:read",
        project_id="project-1",
        database=database,
    )
    assert not has_permission(
        service_account,
        "codex:run",
        project_id="project-1",
        database=database,
    )
    assert not has_permission(
        service_account,
        "admin:manage_users",
        project_id="project-1",
        database=database,
    )


def test_project_visibility_filters(tmp_path: Path) -> None:
    database, _admin, viewer = _database_with_users(tmp_path)
    database.grant_project_permission(
        project_id="visible-project",
        role="viewer",
        actor_user_id="user-admin",
        user_id=viewer.user_id,
    )
    projects = [
        {"project_id": "visible-project"},
        {"project_id": "hidden-project"},
    ]

    visible = filter_visible_projects(database, viewer, projects)

    assert visible == [{"project_id": "visible-project"}]


def test_denied_permission_can_be_audited(tmp_path: Path) -> None:
    database, _admin, viewer = _database_with_users(tmp_path)
    database.audit_permission_denials = True
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id="user-admin",
        user_id=viewer.user_id,
    )

    try:
        require_project_access(database, viewer, project_id="project-1", action="run_codex")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected codex permission denial.")

    events = database.list_audit_events(project_id="project-1")
    denied_events = [event for event in events if event.event_type == "permission_denied"]
    assert denied_events
    assert denied_events[0].metadata["permission"] == "codex:run"


def test_codex_task_blocked_without_codex_permission(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    created_project = client.post(
        "/projects",
        json={"workspace_id": "project-1", "name": "Project"},
        headers=admin_headers,
    )
    assert created_project.status_code == 200, created_project.text
    created_user = client.post(
        "/admin/users",
        json={"email": "editor@example.com", "password": "Editor-password-1"},
        headers=admin_headers,
    )
    editor_id = created_user.json()["user"]["user_id"]
    client.post(
        "/projects/project-1/share",
        json={"role": "editor", "user_id": editor_id},
        headers=admin_headers,
    )
    editor_headers = _login(client, "editor@example.com", "Editor-password-1")

    response = client.post("/projects/project-1/codex/summarize", headers=editor_headers)

    assert response.status_code == 403


def _database_with_users(tmp_path: Path) -> tuple[PlatformDatabase, UserAccount, UserAccount]:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    admin = database.create_user(
        email="admin@example.com",
        password="Admin-password-1",
        roles=["platform_admin", "user"],
    )
    user = database.create_user(email="user@example.com", password="User-password-1")
    return database, admin, user


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"
