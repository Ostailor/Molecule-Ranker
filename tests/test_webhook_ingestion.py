from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from molecule_ranker.integrations.store import webhook_events
from molecule_ranker.integrations.webhooks import (
    WebhookError,
    WebhookIngestionConfig,
    WebhookIngestionService,
    sign_payload,
)
from molecule_ranker.platform.database import integration_sync_jobs
from molecule_ranker.platform.db import PlatformDatabase


def test_valid_signature_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    database = _database(tmp_path)
    payload = _payload()

    event = WebhookIngestionService(database).ingest(
        raw_payload=payload,
        headers={
            "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
            "x-webhook-event-id": "evt-1",
        },
        config=_config(),
    )

    assert event.signature_valid is True
    assert event.status == "queued"
    assert event.payload_artifact_id.startswith("artifact-")
    assert event.external_record_id == "rec-1"


def test_invalid_signature_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    database = _database(tmp_path)
    payload = _payload()

    with pytest.raises(WebhookError, match="signature"):
        WebhookIngestionService(database).ingest(
            raw_payload=payload,
            headers={
                "x-molecule-ranker-signature": "sha256=bad",
                "x-webhook-event-id": "evt-1",
            },
            config=_config(),
        )


def test_replay_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    database = _database(tmp_path)
    payload = _payload()
    service = WebhookIngestionService(database)
    headers = {
        "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
        "x-webhook-event-id": "evt-1",
    }

    service.ingest(raw_payload=payload, headers=headers, config=_config())
    with pytest.raises(WebhookError, match="replay"):
        service.ingest(raw_payload=payload, headers=headers, config=_config())


def test_payload_too_large_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    database = _database(tmp_path)
    payload = b'{"id":"evt-large","blob":"' + b"x" * 128 + b'"}'

    with pytest.raises(WebhookError, match="maximum size"):
        WebhookIngestionService(database).ingest(
            raw_payload=payload,
            headers={
                "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
                "x-webhook-event-id": "evt-large",
            },
            config=_config(max_payload_bytes=32),
        )


def test_sync_job_enqueued_and_payload_not_stored_in_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    database = _database(tmp_path)
    payload = _payload(secret_value="payload-secret-token")
    event = WebhookIngestionService(database).ingest(
        raw_payload=payload,
        headers={
            "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
            "x-webhook-event-id": "evt-1",
        },
        config=_config(),
    )

    with database.engine.connect() as connection:
        sync_job = connection.execute(
            select(integration_sync_jobs).where(
                integration_sync_jobs.c.sync_job_id == event.sync_job_id
            )
        ).mappings().one()
        webhook_row = connection.execute(select(webhook_events)).mappings().one()

    db_payload = json.dumps(
        {"sync_job": dict(sync_job), "webhook": dict(webhook_row)},
        default=str,
        sort_keys=True,
    )
    assert sync_job["status"] == "running"
    assert webhook_row["payload_artifact_id"] == event.payload_artifact_id
    assert "payload-secret-token" not in db_payload
    assert event.sync_job_id


def _database(tmp_path: Path) -> PlatformDatabase:
    return PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")


def _payload(*, secret_value: str | None = None) -> bytes:
    payload = {
        "id": "evt-1",
        "event_type": "assay_result.created",
        "external_record_id": "rec-1",
        "summary": "changed",
    }
    if secret_value:
        payload["token"] = secret_value
    return json.dumps(payload, sort_keys=True).encode()


def _config(*, max_payload_bytes: int = 1024) -> WebhookIngestionConfig:
    return WebhookIngestionConfig(
        external_system_id="ext-webhook",
        secret_env_var="WEBHOOK_SIGNING_SECRET",
        max_payload_bytes=max_payload_bytes,
        org_id="org-a",
        project_id="proj-a",
        mode="dry_run",
    )
