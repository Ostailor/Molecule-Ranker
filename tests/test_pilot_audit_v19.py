from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.isolation import run_isolation_audit


def test_pilot_readiness_passes_in_synthetic_dev_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "pilot.sqlite"
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=Path(__file__).resolve().parents[1],
            database_path=db_path,
            artifact_storage_path=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
        )
    )
    isolation = run_isolation_audit(PlatformDatabase(tmp_path, db_path=db_path))

    assert report.version == "2.7.0"
    assert report.environment == "development"
    assert len(report.checks) == 20
    assert report.failed_count == 0
    assert report.blockers == []
    assert report.passed_count >= 20
    assert isolation["status"] == "pass"


def test_pilot_readiness_missing_secret_fails_in_production_mode(tmp_path: Path) -> None:
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=Path(__file__).resolve().parents[1],
            database_path=tmp_path / "pilot.sqlite",
            environment="production",
            secret_key=None,
            allowed_hosts=["ranker.internal"],
            artifact_storage_path=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
        )
    )

    assert report.failed_count >= 1
    assert any("Production auth secret is not configured" in blocker for blocker in report.blockers)
    auth_check = _check(report, "platform_auth_configured")
    assert auth_check["status"] == "fail"


def test_pilot_readiness_unhealthy_worker_fails(tmp_path: Path) -> None:
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=Path(__file__).resolve().parents[1],
            database_path=tmp_path / "pilot.sqlite",
            artifact_storage_path=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
            worker_queue_healthy=False,
        )
    )

    assert report.failed_count >= 1
    assert _check(report, "worker_queue_healthy")["status"] == "fail"


def test_pilot_readiness_missing_backup_policy_warns(tmp_path: Path) -> None:
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=Path(__file__).resolve().parents[1],
            database_path=tmp_path / "pilot.sqlite",
            artifact_storage_path=tmp_path / "artifacts",
            backup_path=None,
        )
    )

    assert report.warning_count >= 1
    assert _check(report, "backup_path_configured")["status"] == "warn"


def test_pilot_readiness_cli_outputs_json_and_markdown(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = CliRunner()
    markdown_path = tmp_path / "pilot_readiness_report.md"

    json_result = runner.invoke(
        app,
        [
            "pilot",
            "readiness",
            "--json",
            "--root",
            str(root),
            "--db-path",
            str(tmp_path / "pilot.sqlite"),
            "--artifact-storage-path",
            str(tmp_path / "artifacts"),
            "--backup-path",
            str(tmp_path / "backups"),
        ],
    )
    markdown_result = runner.invoke(
        app,
        [
            "pilot",
            "readiness",
            "--output",
            str(markdown_path),
            "--root",
            str(root),
            "--db-path",
            str(tmp_path / "pilot.sqlite"),
            "--artifact-storage-path",
            str(tmp_path / "artifacts"),
            "--backup-path",
            str(tmp_path / "backups"),
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["version"] == "2.7.0"
    assert payload["failed_count"] == 0
    assert markdown_result.exit_code == 0, markdown_result.output
    assert markdown_path.exists()
    assert "# Enterprise Pilot Readiness Audit" in markdown_path.read_text()


def _check(report, check_id: str) -> dict:
    return next(check for check in report.checks if check["check_id"] == check_id)
