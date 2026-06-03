from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.backup import create_platform_backup, verify_platform_backup
from molecule_ranker.platform.database import (
    PlatformDatabase,
    artifact_records,
    project_workspaces,
)
from molecule_ranker.platform.disaster_recovery import run_disaster_recovery_drill


def test_disaster_recovery_drill_round_trip_and_smoke_pass(tmp_path: Path) -> None:
    database = _seed_platform(tmp_path)

    report = run_disaster_recovery_drill(
        database,
        output_dir=tmp_path / "dr",
        key_project_ids=["project-dr"],
        key_artifact_ids=["artifact-report"],
    )

    assert report.status == "pass"
    payload = report.to_dict()
    assert payload["checks"]["backup_manifest"]["status"] == "pass"
    assert payload["checks"]["restore"]["status"] == "pass"
    assert payload["checks"]["migration"]["status"] == "pass"
    assert payload["checks"]["artifact_hashes"]["status"] == "pass"
    assert payload["checks"]["key_project_artifact_load"]["status"] == "pass"
    assert payload["checks"]["user_role_metadata"]["status"] == "pass"
    assert payload["checks"]["no_secrets_in_backup"]["status"] == "pass"
    assert payload["checks"]["smoke_workflow"]["status"] == "pass"
    assert (tmp_path / "dr" / "dr_report.json").exists()
    assert (tmp_path / "dr" / "dr_report.md").exists()


def test_backup_verification_detects_missing_artifact(tmp_path: Path) -> None:
    database = _seed_platform(tmp_path)
    backup = create_platform_backup(database, output_path=tmp_path / "backup.zip")
    broken = tmp_path / "missing-artifact.zip"
    _rewrite_zip_without_first_artifact(backup.path, broken)

    verification = verify_platform_backup(broken)

    assert verification.status == "fail"
    assert any("Manifest entry missing from archive" in error for error in verification.errors)


def test_backup_verification_detects_hash_mismatch(tmp_path: Path) -> None:
    database = _seed_platform(tmp_path)
    backup = create_platform_backup(database, output_path=tmp_path / "backup.zip")
    broken = tmp_path / "hash-mismatch.zip"
    _rewrite_zip_with_corrupted_first_artifact(backup.path, broken)

    verification = verify_platform_backup(broken)

    assert verification.status == "fail"
    assert any("Hash mismatch" in error for error in verification.errors)


def test_disaster_recovery_drill_excludes_secrets(tmp_path: Path) -> None:
    database = _seed_platform(tmp_path)

    report = run_disaster_recovery_drill(database, output_dir=tmp_path / "dr")

    report_text = json.dumps(report.to_dict())
    backup_text = report.backup_path.read_bytes().decode("latin1", errors="ignore")
    assert report.status == "pass"
    assert "sk-dr-secret-value" not in report_text
    assert "sk-dr-secret-value" not in backup_text


def test_disaster_recovery_cli_outputs_report(tmp_path: Path) -> None:
    _seed_platform(tmp_path)
    output_dir = tmp_path / "dr-cli"

    result = CliRunner().invoke(
        app,
        [
            "platform",
            "dr-drill",
            "--root",
            str(tmp_path),
            "--db-path",
            str(tmp_path / "platform.sqlite"),
            "--output-dir",
            str(output_dir),
            "--project-id",
            "project-dr",
            "--artifact-id",
            "artifact-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["checks"]["smoke_workflow"]["status"] == "pass"
    assert payload["report_path"] == str((output_dir / "dr_report.json").resolve())


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
        summary="Created with API key sk-dr-secret-value-1234567890",
        metadata={"api_key": "sk-dr-secret-value-1234567890"},
    )
    artifact_path = tmp_path / "artifacts" / "report.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Report\nInternal research software validation artifact.\n")
    (tmp_path / ".env").write_text("MOLECULE_RANKER_SECRET_KEY=plaintext\n")
    now = datetime.now(UTC)
    with database.engine.begin() as connection:
        connection.execute(
            insert(project_workspaces).values(
                project_id="project-dr",
                org_id="default",
                name="DR Project",
                root_dir=None,
                created_at=now,
                updated_at=now,
                metadata_json={},
            )
        )
        connection.execute(
            artifact_records.insert().values(
                artifact_id="artifact-report",
                org_id="default",
                project_id="project-dr",
                run_id="run-1",
                artifact_type="report",
                path=str(artifact_path),
                sha256="placeholder",
                size_bytes=artifact_path.stat().st_size,
                provenance_json={"source": "test"},
                created_at=now,
                metadata_json={},
            )
        )
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records)).mappings().fetchall()
    assert len(rows) == 1
    return database


def _rewrite_zip_without_first_artifact(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as rewritten:
        artifact_name = next(name for name in original.namelist() if name.startswith("artifacts/"))
        for name in original.namelist():
            if name == artifact_name:
                continue
            rewritten.writestr(name, original.read(name))


def _rewrite_zip_with_corrupted_first_artifact(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as rewritten:
        artifact_name = next(name for name in original.namelist() if name.startswith("artifacts/"))
        for name in original.namelist():
            data = b"corrupted artifact bytes" if name == artifact_name else original.read(name)
            rewritten.writestr(name, data)
