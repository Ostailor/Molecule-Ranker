from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.connectors import create_connector
from molecule_ranker.integrations.connectors.base import ExternalConnector
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.schemas import ConnectorConfig, IntegrationHealthStatus, SyncJob
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.sync import SyncEngine, SyncRequest
from molecule_ranker.platform.database import platform_jobs
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import PlatformJob, UserAccount

INTEGRATION_JOB_TYPES = {
    "integration_sync",
    "connector_health_check",
    "webhook_processing",
    "warehouse_export",
    "registry_mapping_review",
    "external_export",
}

ConnectorFactory = Callable[[ConnectorConfig], ExternalConnector]


class IntegrationWorker:
    """Worker for connector jobs running through the platform queue.

    Connector calls stay out of the Codex worker path. Codex can produce a
    recommendation payload, but only this worker may claim and execute connector
    jobs after RBAC is checked again at execution time.
    """

    def __init__(
        self,
        *,
        database: PlatformDatabase,
        connector_factory: ConnectorFactory | None = None,
    ) -> None:
        self.database = database
        self.queue = PlatformJobQueue(database)
        self.connector_factory = connector_factory or create_connector

    def run_next(self) -> PlatformJob | None:
        job = self.queue.claim_next(job_types=INTEGRATION_JOB_TYPES)
        if job is None:
            return None
        return self.run_job(job)

    def run_once(self) -> PlatformJob | None:
        return self.run_next()

    def run_job(self, job: PlatformJob) -> PlatformJob:
        try:
            user = self._check_authorization(job)
            connector_config = self._connector_config(job)
            connector = self.connector_factory(connector_config)
            if job.job_type == "connector_health_check":
                return self._complete_health_job(
                    job,
                    connector.health_check(),
                )
            if job.job_type == "registry_mapping_review":
                return self._complete_recommendation_job(
                    job,
                    recommendation=job.config_snapshot.get("mapping_review") or {},
                )
            request = self._sync_request(job, connector_config)
            store = IntegrationStore(
                self.database,
                user=user,
                org_id=request.org_id,
                project_id=request.project_id,
            )
            sync_job = SyncEngine(store).run(connector, request)
            artifact_ids = self._sync_artifact_ids(store, sync_job)
            if artifact_ids:
                sync_job = store.update_sync_job(sync_job.sync_job_id, artifact_ids=artifact_ids)
            return self._complete_sync_job(job, sync_job, artifact_ids=artifact_ids)
        except Exception as exc:
            return self.queue.fail(job, exc)

    def _check_authorization(self, job: PlatformJob) -> UserAccount:
        user = self.database.get_user(job.requested_by_user_id)
        if user is None or not user.is_active:
            raise PermissionError("Requesting user is no longer active.")
        permission = (
            "integration:read"
            if job.job_type == "connector_health_check"
            else "integration:sync"
        )
        if not user.is_admin and not has_permission(
            user,
            permission,
            org_id=job.org_id,
            project_id=job.project_id,
            database=self.database,
        ):
            raise PermissionError(f"Requesting user no longer has {permission}.")
        return user

    def _connector_config(self, job: PlatformJob) -> ConnectorConfig:
        raw_connector = job.config_snapshot.get("connector")
        if isinstance(raw_connector, dict):
            return ConnectorConfig.model_validate(raw_connector)
        connector_id = str(job.config_snapshot.get("connector_id") or "")
        if not connector_id:
            raise PlatformDatabaseError("Integration job is missing connector_id.")
        connector = self.database.get_integration_connector(connector_id)
        if connector is None:
            raise PlatformDatabaseError(f"Integration connector not found: {connector_id}")
        return connector

    def _sync_request(self, job: PlatformJob, connector: ConnectorConfig) -> SyncRequest:
        raw_request = dict(job.config_snapshot.get("sync_request") or {})
        direction = (
            raw_request.get("direction")
            or job.config_snapshot.get("direction")
            or connector.direction
        )
        if job.job_type in {"warehouse_export", "external_export"}:
            direction = "export"
        mode = str(raw_request.get("mode") or job.config_snapshot.get("mode") or connector.mode)
        if mode == "sandbox":
            mode = "dry_run"
        raw_request.update(
            {
                "direction": direction,
                "mode": mode,
                "project_id": raw_request.get("project_id", job.project_id),
                "org_id": raw_request.get("org_id", job.org_id),
                "requested_by_user_id": raw_request.get(
                    "requested_by_user_id",
                    job.requested_by_user_id,
                ),
                "metadata": {
                    **dict(raw_request.get("metadata") or {}),
                    "platform_job_id": job.job_id,
                    "platform_job_type": job.job_type,
                },
            }
        )
        return SyncRequest.model_validate(raw_request)

    def _sync_artifact_ids(self, store: IntegrationStore, sync_job: SyncJob) -> list[str]:
        artifact_ids: list[str] = []
        for record in store.list_sync_records(sync_job_id=sync_job.sync_job_id, limit=100_000):
            if record.raw_payload_artifact_id:
                artifact_ids.append(record.raw_payload_artifact_id)
        return _dedupe(artifact_ids)

    def _complete_health_job(
        self,
        job: PlatformJob,
        health: IntegrationHealthStatus,
    ) -> PlatformJob:
        return self._complete_platform_job(
            job,
            status="succeeded",
            result={"health": health.model_dump(mode="json")},
            artifact_ids=[],
            error_summary=None,
        )

    def _complete_recommendation_job(
        self,
        job: PlatformJob,
        *,
        recommendation: dict[str, Any],
    ) -> PlatformJob:
        return self._complete_platform_job(
            job,
            status="succeeded",
            result={
                "status": "recommendation_recorded",
                "recommendation": _redact_json(recommendation),
                "connector_execution": "not_run",
            },
            artifact_ids=[],
            error_summary=None,
        )

    def _complete_sync_job(
        self,
        job: PlatformJob,
        sync_job: SyncJob,
        *,
        artifact_ids: list[str],
    ) -> PlatformJob:
        status = _platform_status(sync_job.status)
        return self._complete_platform_job(
            job,
            status=status,
            result={
                "sync_job": sync_job.model_dump(mode="json"),
                "mirrored_sync_status": sync_job.status,
            },
            artifact_ids=artifact_ids,
            error_summary=sync_job.error_summary if status in {"failed", "partial"} else None,
        )

    def _complete_platform_job(
        self,
        job: PlatformJob,
        *,
        status: str,
        result: dict[str, Any],
        artifact_ids: list[str],
        error_summary: str | None,
    ) -> PlatformJob:
        now = datetime.now(UTC)
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status=status,
                    completed_at=now,
                    updated_at=now,
                    result_artifact_ids_json=artifact_ids,
                    result_json=_redact_json(result),
                    error_summary=redact_secret_values(error_summary or "") or None,
                )
            )
        self.database.write_audit(
            f"job_{status}",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} completed with status {status}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"job_type": job.job_type, "artifact_ids": artifact_ids},
        )
        refreshed = self.queue.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after completion.")
        return refreshed


def enqueue_integration_sync_job(
    *,
    database: PlatformDatabase,
    connector: ConnectorConfig,
    request: SyncRequest,
    requested_by: UserAccount,
    job_type: str = "integration_sync",
    priority: str = "normal",
    metadata: dict[str, Any] | None = None,
) -> PlatformJob:
    if job_type not in INTEGRATION_JOB_TYPES:
        raise PlatformDatabaseError(f"Unsupported integration job type: {job_type}")
    safe_request = request.model_copy(
        update={
            "org_id": request.org_id,
            "project_id": request.project_id,
            "requested_by_user_id": request.requested_by_user_id or requested_by.user_id,
        }
    )
    return PlatformJobQueue(database).enqueue(
        job_type=job_type,
        requested_by=requested_by,
        org_id=safe_request.org_id,
        project_id=safe_request.project_id,
        config_snapshot={
            "connector_id": connector.connector_id,
            "connector": connector.model_dump(mode="json"),
            "sync_request": safe_request.model_dump(mode="json"),
        },
        priority=priority,
        metadata=_redact_json(metadata or {}),
    )


def enqueue_connector_health_check_job(
    *,
    database: PlatformDatabase,
    connector: ConnectorConfig,
    requested_by: UserAccount,
    org_id: str = "default",
    project_id: str | None = None,
    priority: str = "normal",
) -> PlatformJob:
    return PlatformJobQueue(database).enqueue(
        job_type="connector_health_check",
        requested_by=requested_by,
        org_id=org_id,
        project_id=project_id,
        config_snapshot={
            "connector_id": connector.connector_id,
            "connector": connector.model_dump(mode="json"),
        },
        priority=priority,
        metadata={"connector_id": connector.connector_id},
    )


def recommend_safe_connector_task(
    *,
    connector_id: str,
    task_type: str,
    reason: str,
    object_types: list[str] | None = None,
    mode: str = "dry_run",
) -> dict[str, Any]:
    """Return a Codex-safe recommendation without enqueueing or running a connector job."""

    return _redact_json(
        {
            "status": "recommendation_only",
            "connector_id": connector_id,
            "task_type": task_type,
            "reason": reason,
            "object_types": object_types or [],
            "mode": "dry_run" if mode == "write_enabled" else mode,
            "connector_execution": "not_run",
            "requires_user_permission": "integration:sync",
        }
    )


def _platform_status(sync_status: str) -> str:
    if sync_status in {"succeeded", "failed", "partial", "cancelled"}:
        return sync_status
    if sync_status == "running":
        return "running"
    return "failed"


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secret_values(redact_secrets(value))
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
