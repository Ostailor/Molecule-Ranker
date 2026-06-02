from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.ops_observability import build_ops_alerts, build_ops_metrics
from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue


def test_alert_generated_for_failed_jobs(tmp_path: Path) -> None:
    database = _database_with_failed_job(tmp_path, error="worker failed api_key=secret-token-value")

    metrics_report = build_ops_metrics(root_dir=tmp_path, db_path=tmp_path / "platform.sqlite")
    alerts = build_ops_alerts(metrics_report)

    assert metrics_report["job_monitoring"]["failed_count"] == 1
    assert any(alert["alert_type"] == "job_failure_rate_high" for alert in alerts["alerts"])
    assert "secret-token-value" not in json.dumps(metrics_report)
    assert "secret-token-value" not in json.dumps(alerts)
    assert database.health()["ok"] is True


def test_alert_generated_for_stale_backup(tmp_path: Path) -> None:
    _database_with_failed_job(tmp_path, error="worker failed")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "platform-backup.zip"
    backup.write_text("backup fixture", encoding="utf-8")
    stale_time = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(backup, (stale_time, stale_time))

    metrics_report = build_ops_metrics(
        root_dir=tmp_path,
        db_path=tmp_path / "platform.sqlite",
        backup_path=backup_dir,
    )
    alerts = build_ops_alerts(metrics_report)

    assert any(alert["alert_type"] == "backup_stale" for alert in alerts["alerts"])


def test_ops_cli_outputs_no_secrets(tmp_path: Path) -> None:
    _database_with_failed_job(tmp_path, error="failed with service_token=secret-token-value")

    result = CliRunner().invoke(
        app,
        ["ops", "metrics", "--root", str(tmp_path), "--db-path", str(tmp_path / "platform.sqlite")],
    )
    alerts = CliRunner().invoke(
        app,
        ["ops", "alerts", "--root", str(tmp_path), "--db-path", str(tmp_path / "platform.sqlite")],
    )

    assert result.exit_code == 0, result.output
    assert alerts.exit_code == 0, alerts.output
    assert "secret-token-value" not in result.output
    assert "secret-token-value" not in alerts.output
    assert "job_failure_rate_high" in alerts.output


def _database_with_failed_job(tmp_path: Path, *, error: str) -> PlatformDatabase:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(
        email="pilot-ops@example.com",
        password="Pilot-ops-password-1",
        roles=["platform_admin"],
    )
    queue = PlatformJobQueue(database)
    job = queue.enqueue(job_type="ranking", requested_by=user, project_id="project-1")
    queue.fail(job, RuntimeError(error))
    return database
