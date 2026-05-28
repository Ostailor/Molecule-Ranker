from __future__ import annotations

import json
import zipfile
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.backup import (
    create_platform_backup,
    restore_platform_backup,
    verify_platform_backup,
)
from molecule_ranker.platform.database import PlatformDatabase, artifact_records


def test_platform_backup_restore_round_trip_excludes_secrets_and_preserves_hashes(
    tmp_path: Path,
) -> None:
    database = _seed_platform(tmp_path)
    backup_path = tmp_path / "backup.zip"

    backup = create_platform_backup(database, output_path=backup_path)

    assert backup.path == backup_path
    assert backup.status == "pass"
    assert backup.manifest["entry_count"] > 0
    assert "sk-test-secret-value" not in backup_path.read_bytes().decode("latin1", errors="ignore")
    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("backup_manifest.json"))
    assert "database/platform.sqlite" in names
    assert "database/tables/artifact_records.json" in names
    assert any(name.startswith("artifacts/") for name in names)
    assert ".env" not in " ".join(names)
    assert all(entry["sha256"] for entry in manifest["entries"])

    dry_run = restore_platform_backup(backup_path, target_dir=tmp_path / "dry-run", dry_run=True)
    restored = restore_platform_backup(backup_path, target_dir=tmp_path / "restored")
    verified = verify_platform_backup(backup_path)

    assert dry_run.status == "pass"
    assert not (tmp_path / "dry-run").exists()
    assert restored.status == "pass"
    assert (tmp_path / "restored" / "database" / "platform.sqlite").exists()
    assert verified.status == "pass"


def test_platform_backup_cli_round_trip(tmp_path: Path) -> None:
    _seed_platform(tmp_path)
    backup_path = tmp_path / "cli-backup.zip"
    target_dir = tmp_path / "cli-restore"
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "platform",
            "backup",
            "--root",
            str(tmp_path),
            "--db-path",
            str(tmp_path / "platform.sqlite"),
            "--output",
            str(backup_path),
            "--json",
        ],
    )
    verified = runner.invoke(app, ["platform", "backup-verify", str(backup_path), "--json"])
    dry_run = runner.invoke(
        app,
        [
            "platform",
            "restore",
            "--input",
            str(backup_path),
            "--target-dir",
            str(target_dir),
            "--dry-run",
            "--json",
        ],
    )
    restored = runner.invoke(
        app,
        [
            "platform",
            "restore",
            "--input",
            str(backup_path),
            "--target-dir",
            str(target_dir),
            "--json",
        ],
    )

    assert created.exit_code == 0, created.stdout
    assert verified.exit_code == 0, verified.stdout
    assert dry_run.exit_code == 0, dry_run.stdout
    assert restored.exit_code == 0, restored.stdout
    assert json.loads(created.stdout)["status"] == "pass"
    assert json.loads(verified.stdout)["status"] == "pass"
    assert json.loads(dry_run.stdout)["dry_run"] is True
    assert json.loads(restored.stdout)["dry_run"] is False
    assert (target_dir / "database" / "platform.sqlite").exists()


def _seed_platform(tmp_path: Path) -> PlatformDatabase:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(
        email="admin@example.test",
        password="Not-plaintext-password-1",
        roles=["platform_admin", "user"],
    )
    database.write_audit(
        "secret_probe",
        actor_user_id=user.user_id,
        summary="Created with API key sk-test-secret-value-1234567890",
        metadata={"api_key": "sk-test-secret-value-1234567890"},
    )
    artifact_path = tmp_path / "artifacts" / "report.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Report\nNo medical claims.\n")
    cache_path = tmp_path / ".cache" / "ignored.txt"
    cache_path.parent.mkdir()
    cache_path.write_text("cache\n")
    (tmp_path / ".env").write_text("MOLECULE_RANKER_SECRET_KEY=plaintext\n")
    with database.engine.begin() as connection:
        connection.execute(
            artifact_records.insert().values(
                artifact_id="artifact-report",
                org_id="default",
                project_id="project-1",
                run_id="run-1",
                artifact_type="report",
                path=str(artifact_path),
                sha256="placeholder",
                size_bytes=artifact_path.stat().st_size,
                provenance_json={"source": "test"},
                created_at=user.created_at,
                metadata_json={},
            )
        )
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records)).mappings().fetchall()
    assert len(rows) == 1
    return database
