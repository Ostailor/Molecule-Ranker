from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.jobs import JobResult, PlatformJobQueue
from molecule_ranker.platform.slo import DEFAULT_V2_SLOS, generate_slo_report


def test_slo_report_generated_and_cli_exports_json(tmp_path: Path) -> None:
    database, user = _database_with_user(tmp_path)
    backup = _fresh_backup(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(job_type="ranking", requested_by=user, project_id="project-1")
    queue.succeed(job, JobResult(result={"ok": True}))

    report = generate_slo_report(database=database, backup_path=backup)

    payload = report.to_dict()
    assert payload["report_type"] == "v2_slo_report"
    assert payload["status"] == "pass"
    assert {item["slo_id"] for item in payload["measurements"]} == {
        definition.slo_id for definition in DEFAULT_V2_SLOS
    }
    assert payload["error_budget_summary"]["overall_status"] == "pass"

    output = tmp_path / "slo-report.json"
    result = CliRunner().invoke(
        app,
        [
            "ops",
            "slo-report",
            "--root",
            str(tmp_path),
            "--db-path",
            str(tmp_path / "platform.sqlite"),
            "--backup-path",
            str(backup),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    exported = json.loads(output.read_text())
    assert exported["report_type"] == "v2_slo_report"
    assert exported["status"] == "pass"


def test_failed_jobs_affect_job_success_slo(tmp_path: Path) -> None:
    database, user = _database_with_user(tmp_path)
    backup = _fresh_backup(tmp_path)
    queue = PlatformJobQueue(database)
    succeeded = queue.enqueue(job_type="ranking", requested_by=user, project_id="project-1")
    failed = queue.enqueue(job_type="developability", requested_by=user, project_id="project-1")
    queue.succeed(succeeded, JobResult(result={"ok": True}))
    queue.fail(failed, RuntimeError("worker failed"))

    report = generate_slo_report(database=database, backup_path=backup)
    job_success = _measurement(report.to_dict(), "job_success_rate")

    assert job_success["status"] == "fail"
    assert job_success["observed_value"] == 0.5
    assert job_success["bad_events"] == 1


def test_stale_backup_affects_backup_freshness_slo(tmp_path: Path) -> None:
    database, user = _database_with_user(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
    )
    PlatformJobQueue(database).succeed(job, JobResult(result={"ok": True}))
    stale_backup = tmp_path / "backups"
    stale_backup.mkdir()
    stale_file = stale_backup / "backup.zip"
    stale_file.write_text("old backup")
    old_timestamp = (datetime.now(UTC) - timedelta(hours=72)).timestamp()
    os.utime(stale_file, (old_timestamp, old_timestamp))

    report = generate_slo_report(database=database, backup_path=stale_backup)
    backup = _measurement(report.to_dict(), "backup_freshness")

    assert backup["status"] == "fail"
    assert backup["observed_value_hours"] >= 24


def test_slo_metrics_are_redacted(tmp_path: Path) -> None:
    database, user = _database_with_user(tmp_path)
    database.write_audit(
        "auth_login_failed",
        actor_user_id=user.user_id,
        summary="Failed login with Authorization: Bearer secret-token-value",
        metadata={"api_key": "sk-secret-value", "nested": {"password": "plain-secret"}},
    )
    backup = _fresh_backup(tmp_path)

    payload = generate_slo_report(
        database=database,
        backup_path=backup,
        runtime_metrics={"authorization": "Bearer secret-token-value"},
    ).to_dict()
    serialized = json.dumps(payload)

    assert "secret-token-value" not in serialized
    assert "sk-secret-value" not in serialized
    assert "plain-secret" not in serialized
    assert "[REDACTED]" in serialized


def _database_with_user(tmp_path: Path) -> tuple[PlatformDatabase, Any]:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="slo-admin@example.com", password="Admin-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    return database, user


def _fresh_backup(tmp_path: Path) -> Path:
    backup = tmp_path / "backups"
    backup.mkdir(exist_ok=True)
    backup.joinpath("backup.zip").write_text("backup manifest placeholder")
    return backup


def _measurement(payload: dict[str, Any], slo_id: str) -> dict[str, Any]:
    for measurement in payload["measurements"]:
        if measurement["slo_id"] == slo_id:
            return measurement
    raise AssertionError(f"Missing SLO measurement: {slo_id}")
