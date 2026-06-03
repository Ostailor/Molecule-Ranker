from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.integrations.schemas import ConnectorConfig
from molecule_ranker.platform.database import (
    PlatformDatabase,
    artifact_records,
    project_workspaces,
)
from molecule_ranker.platform.isolation import (
    IsolationViolation,
    require_artifact_access,
    require_connector_access,
    require_cross_project_permissions,
    require_project_access,
    run_isolation_audit,
    validate_codex_artifact_scope,
)
from molecule_ranker.platform.schemas import UserAccount


def test_user_cannot_access_another_tenant_project(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)

    try:
        require_project_access(database, user_a, project_id="project-b", permission="project:read")
    except IsolationViolation as exc:
        assert exc.namespace.project_id == "project-b"
        assert exc.permission == "project:read"
    else:
        raise AssertionError("Expected tenant isolation denial.")


def test_artifact_path_from_another_tenant_blocked(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)
    _insert_artifact(
        database,
        artifact_id="artifact-b",
        org_id="org-b",
        project_id="project-b",
        path=tmp_path / "tenant-b" / "result.json",
    )

    try:
        require_artifact_access(
            database,
            user_a,
            artifact_id="artifact-b",
            project_id="project-a",
        )
    except IsolationViolation as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Expected artifact isolation denial.")


def test_codex_task_cannot_read_unauthorized_artifact(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)
    _insert_artifact(
        database,
        artifact_id="artifact-a",
        org_id="org-a",
        project_id="project-a",
        path=tmp_path / "tenant-a" / "allowed.json",
    )
    _insert_artifact(
        database,
        artifact_id="artifact-b",
        org_id="org-b",
        project_id="project-b",
        path=tmp_path / "tenant-b" / "blocked.json",
    )

    try:
        validate_codex_artifact_scope(
            database,
            user_a,
            job_project_id="project-a",
            artifact_ids={"artifact-a", "artifact-b"},
        )
    except IsolationViolation as exc:
        assert exc.namespace.artifact_id == "artifact-b"
    else:
        raise AssertionError("Expected Codex artifact isolation denial.")


def test_integration_credential_cannot_cross_tenant_without_permission(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)
    database.create_integration_connector(
        ConnectorConfig(
            connector_id="conn-b",
            name="Tenant B connector",
            provider="generic_rest",
            kind="generic_rest",
            mode="read_only",
        ),
        actor_user_id="user-b",
        org_id="org-b",
        project_id="project-b",
    )

    try:
        require_connector_access(
            database,
            user_a,
            connector_id="conn-b",
            permission="integration:read",
        )
    except IsolationViolation as exc:
        assert exc.namespace.integration_id == "conn-b"
    else:
        raise AssertionError("Expected integration isolation denial.")


def test_graph_cross_project_query_enforces_permissions(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)

    try:
        require_cross_project_permissions(
            database,
            user_a,
            project_ids=["project-a", "project-b"],
            permission="graph:query",
            domain="graph",
        )
    except IsolationViolation as exc:
        assert exc.namespace.project_id == "project-b"
    else:
        raise AssertionError("Expected graph cross-project isolation denial.")


def test_isolation_audit_and_cli_report_missing_scope(tmp_path: Path) -> None:
    database, user_a, _user_b = _tenant_database(tmp_path)
    database.create_integration_connector(
        ConnectorConfig(
            connector_id="conn-unscoped",
            name="Unscoped connector",
            provider="generic_rest",
            kind="generic_rest",
            mode="read_only",
        ),
        actor_user_id=user_a.user_id,
        org_id="default",
        project_id=None,
    )
    database.enqueue_job(
        job_type="ranking",
        requested_by_user_id=user_a.user_id,
        project_id=None,
        payload={"org_id": "org-a"},
    )

    report = run_isolation_audit(database)
    result = CliRunner().invoke(
        app,
        ["validate", "isolation", "--db-path", str(tmp_path / "platform.sqlite"), "--json"],
    )

    assert report["status"] == "fail"
    assert any(finding["check_id"] == "job_namespace_scope" for finding in report["findings"])
    assert result.exit_code == 1
    assert "job_namespace_scope" in result.stdout


def _tenant_database(tmp_path: Path) -> tuple[PlatformDatabase, UserAccount, UserAccount]:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    admin = database.create_user(
        email="admin@example.com",
        password="Admin-password-1",
        roles=["platform_admin", "user"],
    )
    user_a = database.create_user(email="a@example.com", password="User-password-1")
    user_b = database.create_user(email="b@example.com", password="User-password-1")
    org_a = database.create_organization(
        name="Tenant A",
        org_id="org-a",
        created_by_user_id=admin.user_id,
    )
    org_b = database.create_organization(
        name="Tenant B",
        org_id="org-b",
        created_by_user_id=admin.user_id,
    )
    database.add_membership(user_id=user_a.user_id, org_id=org_a.org_id, role="scientist")
    database.add_membership(user_id=user_b.user_id, org_id=org_b.org_id, role="scientist")
    _insert_project(database, org_id=org_a.org_id, project_id="project-a")
    _insert_project(database, org_id=org_b.org_id, project_id="project-b")
    database.grant_project_permission(
        project_id="project-a",
        role="project_owner",
        actor_user_id=admin.user_id,
        user_id=user_a.user_id,
    )
    database.grant_project_permission(
        project_id="project-b",
        role="project_owner",
        actor_user_id=admin.user_id,
        user_id=user_b.user_id,
    )
    return database, user_a, user_b


def _insert_project(database: PlatformDatabase, *, org_id: str, project_id: str) -> None:
    now = datetime.now(UTC)
    with database.engine.begin() as connection:
        connection.execute(
            insert(project_workspaces).values(
                project_id=project_id,
                org_id=org_id,
                name=project_id,
                root_dir=None,
                created_at=now,
                updated_at=now,
                metadata_json={},
            )
        )


def _insert_artifact(
    database: PlatformDatabase,
    *,
    artifact_id: str,
    org_id: str,
    project_id: str,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    with database.engine.begin() as connection:
        connection.execute(
            insert(artifact_records).values(
                artifact_id=artifact_id,
                org_id=org_id,
                project_id=project_id,
                run_id=None,
                artifact_type="json",
                path=str(path),
                sha256="0" * 64,
                size_bytes=3,
                provenance_json={},
                created_at=datetime.now(UTC),
                metadata_json={},
            )
        )
