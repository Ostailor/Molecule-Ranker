from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.database import artifact_records, platform_jobs
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.observability import log_event, metrics
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import PlatformJob, UserAccount

QUEUE_JOB_TYPES = {
    "ranking",
    "generation",
    "developability",
    "experiment_import",
    "integration_sync",
    "connector_health_check",
    "webhook_processing",
    "warehouse_export",
    "registry_mapping_review",
    "external_export",
    "active_learning",
    "review_export",
    "dashboard_build",
    "codex_task",
}

JOB_PERMISSION: dict[str, str] = {
    "ranking": "run:create",
    "generation": "run:create",
    "developability": "run:create",
    "experiment_import": "experiment:import",
    "integration_sync": "integration:sync",
    "connector_health_check": "integration:read",
    "webhook_processing": "integration:sync",
    "warehouse_export": "integration:sync",
    "registry_mapping_review": "integration:sync",
    "external_export": "integration:sync",
    "active_learning": "run:create",
    "review_export": "artifact:export",
    "dashboard_build": "project:read",
    "codex_task": "codex:run",
}

PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
LOGGER = logging.getLogger("molecule_ranker.jobs")


@dataclass(frozen=True)
class JobResult:
    result: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[Path] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)


class PlatformJobQueue:
    def __init__(self, database: PlatformDatabase, *, root_dir: Path | None = None) -> None:
        self.database = database
        self.root_dir = (root_dir or database.root_dir).resolve()

    def enqueue(
        self,
        *,
        job_type: str,
        requested_by: UserAccount,
        org_id: str = "default",
        project_id: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> PlatformJob:
        if job_type not in QUEUE_JOB_TYPES:
            raise PlatformDatabaseError(f"Unsupported job type: {job_type}")
        permission = JOB_PERMISSION[job_type]
        if not requested_by.is_admin and not has_permission(
            requested_by,
            permission,
            org_id=org_id,
            project_id=project_id,
            database=self.database,
        ):
            self.database.write_audit(
                "job_permission_denied",
                actor_user_id=requested_by.user_id,
                org_id=org_id,
                project_id=project_id,
                summary=f"Denied job enqueue for {job_type}.",
                object_type="platform_job",
                object_id=job_type,
                metadata={"job_type": job_type, "permission": permission},
            )
            raise PermissionError(f"Missing permission {permission}.")
        now = _now()
        job = PlatformJob(
            job_id=f"job-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            project_id=project_id,
            requested_by_user_id=requested_by.user_id,
            job_type=job_type,  # type: ignore[arg-type]
            status="queued",
            priority=priority,  # type: ignore[arg-type]
            config_snapshot=config_snapshot or {},
            created_at=now,
            metadata=metadata or {},
        )
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(platform_jobs).values(
                    job_id=job.job_id,
                    org_id=job.org_id,
                    project_id=job.project_id,
                    requested_by_user_id=job.requested_by_user_id,
                    job_type=job.job_type,
                    status=job.status,
                    priority=job.priority,
                    config_snapshot_json=job.config_snapshot,
                    created_at=job.created_at,
                    started_at=None,
                    completed_at=None,
                    result_artifact_ids_json=[],
                    error_summary=None,
                    metadata_json=job.metadata,
                    updated_at=now,
                    attempts=0,
                    result_json=None,
                )
            )
        self.database.write_audit(
            "job_enqueued",
            actor_user_id=requested_by.user_id,
            org_id=org_id,
            project_id=project_id,
            summary=f"Enqueued {job_type}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"job_id": job.job_id, "job_type": job_type},
        )
        metrics.increment("jobs_queued_total")
        log_event(
            LOGGER,
            "job_enqueued",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
        )
        return job

    def list_jobs(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[PlatformJob]:
        statement = select(platform_jobs)
        if status is not None:
            statement = statement.where(platform_jobs.c.status == status)
        if project_id is not None:
            statement = statement.where(platform_jobs.c.project_id == project_id)
        statement = statement.order_by(platform_jobs.c.created_at.desc()).limit(limit)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_platform_job(row) for row in rows]

    def get(self, job_id: str) -> PlatformJob | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(platform_jobs).where(platform_jobs.c.job_id == job_id))
                .mappings()
                .first()
            )
        return _platform_job(row) if row else None

    def claim_next(self, *, job_types: set[str] | None = None) -> PlatformJob | None:
        statement = select(platform_jobs).where(platform_jobs.c.status == "queued")
        if job_types is not None:
            statement = statement.where(platform_jobs.c.job_type.in_(job_types))
        with self.database.engine.begin() as connection:
            rows = connection.execute(statement).mappings().fetchall()
            if not rows:
                return None
            row = min(
                rows,
                key=lambda item: (
                    PRIORITY_RANK.get(str(item["priority"]), 1),
                    _aware(item["created_at"]),
                ),
            )
            now = _now()
            result = connection.execute(
                update(platform_jobs)
                .where(
                    (platform_jobs.c.job_id == row["job_id"])
                    & (platform_jobs.c.status == "queued")
                )
                .values(
                    status="running",
                    started_at=now,
                    updated_at=now,
                    attempts=int(row["attempts"] or 0) + 1,
                )
            )
            if result.rowcount != 1:
                return None
            refreshed = (
                connection.execute(
                    select(platform_jobs).where(platform_jobs.c.job_id == row["job_id"])
                )
                .mappings()
                .one()
            )
        return _platform_job(refreshed)

    def cancel(self, job_id: str, *, actor_user_id: str) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        now = _now()
        if job.status == "queued":
            with self.database.engine.begin() as connection:
                connection.execute(
                    update(platform_jobs)
                    .where(
                        (platform_jobs.c.job_id == job_id)
                        & (platform_jobs.c.status == "queued")
                    )
                    .values(status="cancelled", completed_at=now, updated_at=now)
                )
        elif job.status == "running":
            metadata = {**job.metadata, "cancel_requested": True, "cancelled_by": actor_user_id}
            with self.database.engine.begin() as connection:
                connection.execute(
                    update(platform_jobs)
                    .where(platform_jobs.c.job_id == job_id)
                    .values(metadata_json=metadata, updated_at=now)
                )
        else:
            return job
        self.database.write_audit(
            "job_cancel_requested",
            actor_user_id=actor_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Cancel requested for job {job_id}.",
            object_type="platform_job",
            object_id=job_id,
            metadata={"previous_status": job.status},
        )
        refreshed = self.get(job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after cancellation.")
        return refreshed

    def succeed(self, job: PlatformJob, result: JobResult) -> PlatformJob:
        artifact_ids = [*result.artifact_ids]
        for artifact_path in result.artifact_paths:
            artifact_ids.append(
                self.register_artifact(
                    job,
                    artifact_path,
                    artifact_type=str(result.result.get("artifact_type") or job.job_type),
                )
            )
        now = _now()
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="succeeded",
                    completed_at=now,
                    updated_at=now,
                    result_artifact_ids_json=artifact_ids,
                    error_summary=None,
                    result_json=result.result,
                )
            )
        self.database.write_audit(
            "job_succeeded",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} succeeded.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"artifact_ids": artifact_ids},
        )
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_succeeded",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            artifact_count=len(artifact_ids),
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after success.")
        return refreshed

    def fail(self, job: PlatformJob, exc: Exception) -> PlatformJob:
        now = _now()
        error_summary = redact_secrets(str(exc))[:2000]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="failed",
                    completed_at=now,
                    updated_at=now,
                    error_summary=error_summary,
                )
            )
        self.database.write_audit(
            "job_failed",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} failed.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"error_summary": error_summary},
        )
        metrics.increment("jobs_failed_total")
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_failed",
            level=logging.ERROR,
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            error_summary=error_summary,
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after failure.")
        return refreshed

    def guardrail_fail(
        self,
        job: PlatformJob,
        *,
        summary: str,
        result: dict[str, Any] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> PlatformJob:
        now = _now()
        error_summary = redact_secrets(summary)[:2000]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="guardrail_failed",
                    completed_at=now,
                    updated_at=now,
                    error_summary=error_summary,
                    result_json=_redact_result(result or {}),
                    result_artifact_ids_json=artifact_ids or [],
                )
            )
        self.database.write_audit(
            "job_guardrail_failed",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} failed Codex guardrails.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"error_summary": error_summary},
        )
        metrics.increment("jobs_failed_total")
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_guardrail_failed",
            level=logging.ERROR,
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            error_summary=error_summary,
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after guardrail failure.")
        return refreshed

    def register_artifact(
        self,
        job: PlatformJob,
        path: Path,
        *,
        artifact_type: str,
    ) -> str:
        start = time.perf_counter()
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PlatformDatabaseError(f"Artifact path does not exist: {path}")
        data = resolved.read_bytes()
        artifact_id = f"artifact-{uuid.uuid4().hex[:16]}"
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(artifact_records).values(
                    artifact_id=artifact_id,
                    org_id=job.org_id,
                    project_id=job.project_id,
                    run_id=None,
                    artifact_type=artifact_type,
                    path=str(resolved),
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                    provenance_json={"job_id": job.job_id, "job_type": job.job_type},
                    created_at=_now(),
                    metadata_json={},
                )
            )
        metrics.increment("artifacts_written_total")
        metrics.observe("artifact_write_duration_seconds", time.perf_counter() - start)
        log_event(
            LOGGER,
            "artifact_registered",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            size_bytes=len(data),
        )
        return artifact_id


class RedisJobQueueAdapter:
    """Placeholder for future Redis/RQ/Celery queue integration.

    V0.9 intentionally uses the SQL-backed queue so local and hosted MVP
    deployments do not require Redis.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Redis/RQ/Celery adapters are planned after the V0.9 MVP.")


def enqueue_platform_job(
    database: PlatformDatabase,
    job: PlatformJob,
    *,
    requested_by: UserAccount | None = None,
) -> PlatformJob:
    user = requested_by or database.get_user(job.requested_by_user_id)
    if user is None:
        raise PlatformDatabaseError("Requesting user does not exist.")
    return PlatformJobQueue(database).enqueue(
        job_type=job.job_type,
        requested_by=user,
        org_id=job.org_id,
        project_id=job.project_id,
        config_snapshot=job.config_snapshot,
        priority=job.priority,
        metadata=job.metadata,
    )


def _platform_job(row: Any) -> PlatformJob:
    return PlatformJob(
        job_id=str(row["job_id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        requested_by_user_id=str(row["requested_by_user_id"]),
        job_type=str(row["job_type"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        priority=str(row["priority"]),  # type: ignore[arg-type]
        config_snapshot=dict(row["config_snapshot_json"] or {}),
        created_at=_aware(row["created_at"]),
        started_at=_aware(row["started_at"]) if row["started_at"] else None,
        completed_at=_aware(row["completed_at"]) if row["completed_at"] else None,
        result_artifact_ids=list(row["result_artifact_ids_json"] or []),
        error_summary=row["error_summary"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _redact_result(value: dict[str, Any]) -> dict[str, Any]:
    return _redact_json(value)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _job_duration_seconds(job: PlatformJob, completed_at: datetime) -> float:
    started_at = job.started_at or job.created_at
    return max((completed_at - started_at).total_seconds(), 0.0)


__all__ = [
    "JOB_PERMISSION",
    "QUEUE_JOB_TYPES",
    "JobResult",
    "PlatformJob",
    "PlatformJobQueue",
    "RedisJobQueueAdapter",
    "enqueue_platform_job",
]
