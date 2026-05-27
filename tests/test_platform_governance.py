from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.database import (
    PlatformDatabase,
    artifact_records,
    project_workspaces,
)
from molecule_ranker.platform.db import PlatformDatabaseError
from molecule_ranker.platform.export import export_project_package
from molecule_ranker.platform.rbac import visible_project_ids
from molecule_ranker.platform.retention import (
    hard_delete_project,
    list_active_projects,
    soft_delete_project,
)


def test_project_export_excludes_secrets_and_cache_files(tmp_path: Path) -> None:
    database = _database_with_project(tmp_path)
    safe_path = tmp_path / "rankings.json"
    safe_path.write_text('{"candidate": "safe"}\n')
    secret_path = tmp_path / ".env"
    secret_path.write_text("OPENAI_API_KEY=sk-12345678901234567890\n")
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "cached.json"
    cache_path.write_text('{"cached": true}\n')
    _insert_artifact(database, "artifact-safe", safe_path, artifact_type="ranking")
    _insert_artifact(database, "artifact-env", secret_path, artifact_type="env")
    _insert_artifact(database, "artifact-cache", cache_path, artifact_type="cache")

    package = export_project_package(
        database,
        project_id="project-1",
        output_path=tmp_path / "project-export.zip",
        actor_user_id="user-1",
    )

    with zipfile.ZipFile(package.path) as archive:
        names = set(archive.namelist())
        payload = json.loads(archive.read("project_export.json"))
    assert any(name.startswith("artifacts/artifact-safe") for name in names)
    assert not any("artifact-env" in name for name in names if name.startswith("artifacts/"))
    assert not any("artifact-cache" in name for name in names if name.startswith("artifacts/"))
    manifest = {item["artifact_id"]: item for item in payload["artifact_manifest"]}
    assert manifest["artifact-safe"]["included"] is True
    assert manifest["artifact-safe"]["actual_sha256"]
    assert manifest["artifact-env"]["included"] is False
    assert manifest["artifact-env"]["source_path"] == "[EXCLUDED]"
    assert manifest["artifact-cache"]["included"] is False


def test_soft_delete_hides_project_from_active_lists_and_visibility(tmp_path: Path) -> None:
    database = _database_with_project(tmp_path)
    user = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    assert "project-1" in visible_project_ids(database, user)

    soft_delete_project(database, project_id="project-1", actor_user_id=user.user_id)

    assert [project["project_id"] for project in list_active_projects(database)] == []
    assert "project-1" not in visible_project_ids(database, user)


def test_hard_delete_requires_confirmation(tmp_path: Path) -> None:
    database = _database_with_project(tmp_path)

    with pytest.raises(PlatformDatabaseError, match="confirm-project-id"):
        hard_delete_project(database, project_id="project-1", confirm_project_id=None)

    with database.engine.connect() as connection:
        still_exists = connection.execute(select(project_workspaces)).mappings().first()
    assert still_exists is not None

    result = hard_delete_project(
        database,
        project_id="project-1",
        confirm_project_id="project-1",
        actor_user_id="admin",
    )
    assert result["deleted_rows"]["project_workspaces"] == 1
    with database.engine.connect() as connection:
        project = connection.execute(select(project_workspaces)).mappings().first()
    assert project is None


def test_export_and_delete_write_audit_events(tmp_path: Path) -> None:
    database = _database_with_project(tmp_path)
    export_project_package(
        database,
        project_id="project-1",
        output_path=tmp_path / "project-export.zip",
        actor_user_id="admin",
    )
    soft_delete_project(database, project_id="project-1", actor_user_id="admin")

    event_types = [event.event_type for event in database.list_audit_events(project_id="project-1")]
    assert "project_exported" in event_types
    assert "project_soft_deleted" in event_types


def test_platform_cli_delete_project_and_retention_run(tmp_path: Path) -> None:
    database = _database_with_project(tmp_path)
    db_path = Path(database.database_url.removeprefix("sqlite:///"))
    runner = CliRunner()

    deleted = runner.invoke(
        app,
        [
            "platform",
            "delete-project",
            "project-1",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--soft",
            "--json",
        ],
    )
    assert deleted.exit_code == 0
    assert json.loads(deleted.output)["project"]["metadata_json"]["deleted_at"]

    retention = runner.invoke(
        app,
        [
            "platform",
            "retention",
            "run",
            "--root",
            str(tmp_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert retention.exit_code == 0
    assert json.loads(retention.output)["artifacts_soft_deleted"] == 0


def _database_with_project(tmp_path: Path) -> PlatformDatabase:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    now = datetime.now(UTC)
    with database.engine.begin() as connection:
        connection.execute(
            insert(project_workspaces).values(
                project_id="project-1",
                org_id="default",
                name="Governance Project",
                root_dir=str(tmp_path),
                created_at=now,
                updated_at=now,
                metadata_json={},
            )
        )
    return database


def _insert_artifact(
    database: PlatformDatabase,
    artifact_id: str,
    path: Path,
    *,
    artifact_type: str,
) -> None:
    import hashlib

    data = path.read_bytes()
    with database.engine.begin() as connection:
        connection.execute(
            insert(artifact_records).values(
                artifact_id=artifact_id,
                org_id="default",
                project_id="project-1",
                run_id=None,
                artifact_type=artifact_type,
                path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                provenance_json={},
                created_at=datetime.now(UTC),
                metadata_json={},
            )
        )
