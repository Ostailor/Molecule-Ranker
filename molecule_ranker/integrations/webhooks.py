from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import insert, select

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.schemas import WebhookIngestRequest
from molecule_ranker.integrations.store import webhook_events
from molecule_ranker.platform.database import PlatformDatabase, artifact_records


class WebhookError(ValueError):
    """Raised when a webhook cannot be accepted safely."""


class WebhookEvent(BaseModel):
    webhook_event_id: str
    external_system_id: str
    event_type: str
    external_record_id: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signature_valid: bool
    payload_artifact_id: str
    status: str
    sync_job_id: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebhookIngestionConfig(BaseModel):
    external_system_id: str
    secret_env_var: str | None = None
    signature_header: str = "x-molecule-ranker-signature"
    event_id_header: str = "x-webhook-event-id"
    allow_unsigned_dev: bool = False
    max_payload_bytes: int = 1_048_576
    org_id: str = "default"
    project_id: str | None = None
    mode: str = "dry_run"
    provider: str = "generic_rest"
    service_account_user_id: str | None = None

    @classmethod
    def from_connector(cls, connector: Any) -> WebhookIngestionConfig:
        config = dict(connector.config or {})
        provider = str(connector.provider)
        signature_header = str(config.get("webhook_signature_header") or "")
        if not signature_header and provider == "benchling":
            signature_header = "x-benchling-signature"
        return cls(
            external_system_id=str(connector.connector_id),
            secret_env_var=config.get("webhook_secret_env")
            or config.get("webhook_signature_env_var"),
            signature_header=signature_header or "x-molecule-ranker-signature",
            event_id_header=str(config.get("webhook_event_id_header") or "x-webhook-event-id"),
            allow_unsigned_dev=bool(config.get("webhook_allow_unsigned_dev", False)),
            max_payload_bytes=int(config.get("webhook_max_payload_bytes") or 1_048_576),
            org_id=str(config.get("org_id") or "default"),
            project_id=connector.metadata.get("project_id")
            if hasattr(connector, "metadata")
            else None,
            mode=str(connector.mode),
            provider=provider,
            service_account_user_id=config.get("webhook_service_account_user_id")
            or config.get("webhook_service_user_id"),
        )


class WebhookIngestionService:
    def __init__(self, database: PlatformDatabase) -> None:
        self.database = database
        webhook_events.create(self.database.engine, checkfirst=True)

    def ingest(
        self,
        *,
        raw_payload: bytes,
        headers: dict[str, str],
        config: WebhookIngestionConfig,
        actor_user_id: str | None = None,
    ) -> WebhookEvent:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        if len(raw_payload) > config.max_payload_bytes:
            raise WebhookError("Webhook payload exceeds configured maximum size.")
        payload_hash = hashlib.sha256(raw_payload).hexdigest()
        payload = _decode_json(raw_payload)
        event_id = _event_id(payload, normalized_headers, config)
        if self._replayed(config.external_system_id, event_id, payload_hash):
            raise WebhookError("Webhook replay rejected.")
        signature_valid = verify_signature(
            raw_payload=raw_payload,
            headers=normalized_headers,
            config=config,
        )
        if not signature_valid:
            raise WebhookError("Webhook signature verification failed.")
        artifact = self._store_payload_artifact(
            raw_payload,
            external_system_id=config.external_system_id,
            payload_hash=payload_hash,
            org_id=config.org_id,
            project_id=config.project_id,
        )
        sync_job = self.database.start_integration_sync_job(
            connector_id=config.external_system_id,
            actor_user_id=actor_user_id,
            org_id=config.org_id,
            project_id=config.project_id,
            direction="import",
            mode="read_only" if config.mode == "read_only" else "dry_run",
            metadata={
                "webhook_event_id": event_id,
                "payload_artifact_id": artifact["artifact_id"],
                "payload_sha256": payload_hash,
            },
        )
        platform_job_id = self._enqueue_processing_job(
            config=config,
            sync_job_id=sync_job.sync_job_id,
            actor_user_id=actor_user_id,
            payload_artifact_id=str(artifact["artifact_id"]),
            payload_hash=payload_hash,
            event_id=event_id,
        )
        event = WebhookEvent(
            webhook_event_id=f"webhook-{uuid.uuid4().hex[:16]}",
            external_system_id=config.external_system_id,
            event_type=str(payload.get("event_type") or payload.get("type") or "webhook_event"),
            external_record_id=str(
                payload.get("external_record_id") or payload.get("record_id") or event_id
            ),
            signature_valid=True,
            payload_artifact_id=str(artifact["artifact_id"]),
            status="queued",
            sync_job_id=sync_job.sync_job_id,
            warnings=[],
            metadata={
                "source_event_id": event_id,
                "payload_sha256": payload_hash,
                "provider": config.provider,
                "platform_job_id": platform_job_id,
            },
        )
        self._write_event(event, org_id=config.org_id, project_id=config.project_id)
        return event

    def _enqueue_processing_job(
        self,
        *,
        config: WebhookIngestionConfig,
        sync_job_id: str,
        actor_user_id: str | None,
        payload_artifact_id: str,
        payload_hash: str,
        event_id: str,
    ) -> str | None:
        requested_by_user_id = actor_user_id or config.service_account_user_id
        if requested_by_user_id is None:
            return None
        job = self.database.enqueue_job(
            job_type="webhook_processing",
            requested_by_user_id=requested_by_user_id,
            project_id=config.project_id,
            payload={
                "org_id": config.org_id,
                "connector_id": config.external_system_id,
                "sync_job_id": sync_job_id,
                "payload_artifact_id": payload_artifact_id,
                "payload_sha256": payload_hash,
                "webhook_event_id": event_id,
                "direction": "import",
                "mode": "read_only" if config.mode == "read_only" else "dry_run",
            },
        )
        return job.job_id

    def _replayed(self, external_system_id: str, event_id: str, payload_hash: str) -> bool:
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(webhook_events).where(
                    webhook_events.c.external_system_id == external_system_id
                )
            ).mappings().fetchall()
        for row in rows:
            metadata = dict(row["metadata_json"] or {})
            if row["source_event_id"] == event_id:
                return True
            if metadata.get("payload_sha256") == payload_hash:
                return True
        return False

    def _store_payload_artifact(
        self,
        raw_payload: bytes,
        *,
        external_system_id: str,
        payload_hash: str,
        org_id: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        artifact_id = f"artifact-{payload_hash[:16]}"
        artifact_dir = self.database.root_dir / ".molecule-ranker" / "webhook-artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{artifact_id}.json"
        path.write_bytes(raw_payload)
        with self.database.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(artifact_records.c.artifact_id).where(
                        artifact_records.c.artifact_id == artifact_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(
                    insert(artifact_records).values(
                        artifact_id=artifact_id,
                        org_id=org_id,
                        project_id=project_id,
                        run_id=None,
                        artifact_type="webhook_payload",
                        path=str(path),
                        sha256=payload_hash,
                        size_bytes=len(raw_payload),
                        provenance_json={"external_system_id": external_system_id},
                        created_at=datetime.now(UTC),
                        metadata_json={},
                    )
                )
        return {
            "artifact_id": artifact_id,
            "path": str(path),
            "sha256": payload_hash,
            "size_bytes": len(raw_payload),
        }

    def _write_event(
        self,
        event: WebhookEvent,
        *,
        org_id: str,
        project_id: str | None,
    ) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(webhook_events).values(
                    webhook_event_id=event.webhook_event_id,
                    org_id=org_id,
                    project_id=project_id,
                    external_system_id=event.external_system_id,
                    event_type=event.event_type,
                    source_event_id=event.metadata.get("source_event_id"),
                    received_at=event.received_at,
                    status=event.status,
                    payload_artifact_id=event.payload_artifact_id,
                    metadata_json=_redact_json(
                        {
                            **event.metadata,
                            "external_record_id": event.external_record_id,
                            "signature_valid": event.signature_valid,
                            "sync_job_id": event.sync_job_id,
                            "warnings": event.warnings,
                        }
                    ),
                )
            )


def verify_signature(
    *,
    raw_payload: bytes,
    headers: dict[str, str],
    config: WebhookIngestionConfig,
) -> bool:
    signature = headers.get(config.signature_header.lower())
    if not signature:
        return bool(config.allow_unsigned_dev)
    secret = _webhook_secret(config)
    expected = hmac.new(secret.encode(), raw_payload, hashlib.sha256).hexdigest()
    observed = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, observed)


def sign_payload(raw_payload: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_payload, hashlib.sha256).hexdigest()


def _webhook_secret(config: WebhookIngestionConfig) -> str:
    if config.secret_env_var:
        value = os.environ.get(config.secret_env_var)
        if not value:
            raise WebhookError(f"Webhook secret env var {config.secret_env_var} is not set.")
        return value
    if config.allow_unsigned_dev:
        return ""
    raise WebhookError("Webhook signature secret is not configured.")


def _decode_json(raw_payload: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookError("Webhook payload must be JSON.") from exc
    if not isinstance(payload, dict):
        raise WebhookError("Webhook payload must be a JSON object.")
    return payload


def _event_id(
    payload: dict[str, Any],
    headers: dict[str, str],
    config: WebhookIngestionConfig,
) -> str:
    header_event_id = headers.get(config.event_id_header.lower())
    raw_event_id = (
        header_event_id
        or payload.get("webhook_event_id")
        or payload.get("event_id")
        or payload.get("id")
        or payload.get("external_record_id")
    )
    if raw_event_id:
        return str(raw_event_id)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


__all__ = [
    "WebhookError",
    "WebhookEvent",
    "WebhookIngestRequest",
    "WebhookIngestionConfig",
    "WebhookIngestionService",
    "sign_payload",
    "verify_signature",
]
