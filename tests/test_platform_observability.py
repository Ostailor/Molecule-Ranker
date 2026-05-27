from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture

from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.observability import log_event, metrics
from molecule_ranker.server.app import create_app


def test_api_logs_include_request_id(tmp_path: Path, caplog: LogCaptureFixture) -> None:
    metrics.reset()
    client = TestClient(create_app(root_dir=tmp_path))

    with caplog.at_level(logging.INFO, logger="molecule_ranker.api"):
        response = client.get("/health", headers={"X-Request-ID": "req-observability-1"})

    assert response.status_code == 200
    assert any(
        '"request_id": "req-observability-1"' in record.getMessage()
        for record in caplog.records
    )


def test_observability_logs_redact_secrets(caplog: LogCaptureFixture) -> None:
    logger = logging.getLogger("molecule_ranker.test_observability")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(
            logger,
            "secret_probe",
            api_key="sk-12345678901234567890",
            nested={"password": "plain-password-value"},
            message="authorization: Bearer abcdefghijklmnop",
        )

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-12345678901234567890" not in combined
    assert "plain-password-value" not in combined
    assert "abcdefghijklmnop" not in combined
    assert "[REDACTED]" in combined


def test_metrics_endpoint_returns_expected_names(tmp_path: Path) -> None:
    metrics.reset()
    client = TestClient(create_app(root_dir=tmp_path))

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    for metric_name in [
        "pipeline_runs_total",
        "pipeline_run_failures_total",
        "jobs_queued_total",
        "jobs_failed_total",
        "codex_tasks_total",
        "codex_guardrail_failures_total",
        "artifacts_written_total",
        "auth_failures_total",
        "api_request_duration_seconds",
    ]:
        assert metric_name in body


def test_job_metrics_update_on_enqueue_and_failure(tmp_path: Path) -> None:
    metrics.reset()
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="scientist@example.com", password="Scientist-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    queue = PlatformJobQueue(database)

    job = queue.enqueue(job_type="ranking", requested_by=user, project_id="project-1")
    queue.fail(job, RuntimeError("worker failed with api_key=secret-value"))

    rendered = metrics.render_prometheus()
    assert "jobs_queued_total 1" in rendered
    assert "jobs_failed_total 1" in rendered
