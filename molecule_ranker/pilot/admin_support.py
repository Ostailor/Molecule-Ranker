from __future__ import annotations

import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import update

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.migrations.artifact_migrations import migrate_artifacts
from molecule_ranker.pilot.ops_observability import (
    build_health_history,
    build_ops_alerts,
    build_ops_metrics,
)
from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit
from molecule_ranker.pilot.support_bundle import create_support_bundle, redact_text
from molecule_ranker.platform.backup import verify_platform_backup
from molecule_ranker.platform.database import SCHEMA_VERSION, platform_jobs
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.schemas import UserAccount

SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|service[_-]?token|token)"
    r"\s*[:=]\s*[^\s,;]+"
)


def build_admin_support_console(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    queue = PlatformJobQueue(database)
    jobs = queue.list_jobs(limit=200)
    metrics = build_ops_metrics(root_dir=root, db_path=_database_file(database))
    readiness = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=root,
            database_path=_database_file(database),
            artifact_storage_path=root / ".molecule-ranker" / "artifacts",
            backup_path=root / ".molecule-ranker" / "backups",
        )
    )
    failed_jobs = [job for job in jobs if str(job.status) in {"failed", "timed_out"}]
    dead_letter_jobs = [job for job in jobs if str(job.status) == "dead_lettered"]
    guardrail_jobs = [job for job in jobs if str(job.status) == "guardrail_failed"]
    console = {
        "pilot_readiness": readiness.model_dump(mode="json"),
        "health_history": build_health_history(root_dir=root, db_path=_database_file(database)),
        "worker_status": _worker_status(metrics),
        "queue_backlog": metrics.get("queue_backlog_monitoring", {}),
        "failed_jobs": [_job_summary(job) for job in failed_jobs],
        "dead_letter_jobs": [_job_summary(job) for job in dead_letter_jobs],
        "support_bundle": {
            "default_output": str(
                root / ".molecule-ranker" / "support-bundles" / "support_bundle.zip"
            ),
            "available": True,
        },
        "migration_status": {
            "current_schema_version": SCHEMA_VERSION,
            "applied_migrations": database.applied_migrations(),
            "migrations_current": SCHEMA_VERSION in set(database.applied_migrations()),
        },
        "storage_usage": _storage_usage(root),
        "backup_status": metrics.get("backup_monitoring", {}),
        "retention_status": database.get_retention_policy(
            scope_type="platform",
            scope_id="platform",
        ).model_dump(mode="json"),
        "guardrail_failures": [_job_summary(job) for job in guardrail_jobs],
        "codex_worker_status": database.codex_worker_status(),
        "integration_health": _integration_health(database),
        "alerts": build_ops_alerts(metrics),
        "redacted_logs_available": bool(_redacted_logs(root)),
        "security": {
            "admin_permission_required": True,
            "secrets_redacted": True,
            "cache_files_shown": False,
        },
    }
    return _redact_json(console)


def retry_failed_job(
    database: PlatformDatabase,
    *,
    job_id: str,
    actor: UserAccount,
) -> dict[str, Any]:
    retried = PlatformJobQueue(database).retry_failed(job_id, requested_by=actor)
    _audit_admin_action(database, actor, "retry_failed_job", object_id=job_id)
    return {"status": "queued", "job": retried.model_dump(mode="json")}


def cancel_job(database: PlatformDatabase, *, job_id: str, actor: UserAccount) -> dict[str, Any]:
    cancelled = PlatformJobQueue(database).cancel(job_id, actor_user_id=actor.user_id)
    _audit_admin_action(database, actor, "cancel_job", object_id=job_id)
    return {"status": "cancel_requested", "job": cancelled.model_dump(mode="json")}


def requeue_dead_letter_job(
    database: PlatformDatabase,
    *,
    job_id: str,
    actor: UserAccount,
) -> dict[str, Any]:
    queue = PlatformJobQueue(database)
    job = queue.get(job_id)
    if job is None:
        raise PlatformDatabaseError("Job not found.")
    if job.status != "dead_lettered":
        raise PlatformDatabaseError("Only dead-lettered jobs can be moved back to the queue.")
    metadata = {
        **job.metadata,
        "requeued_from_dead_letter_at": datetime.now(UTC).isoformat(),
        "requeued_by": actor.user_id,
    }
    metadata.pop("dead_letter", None)
    with database.engine.begin() as connection:
        connection.execute(
            update(platform_jobs)
            .where(platform_jobs.c.job_id == job_id)
            .values(
                status="queued",
                error_summary=None,
                completed_at=None,
                metadata_json=metadata,
                updated_at=datetime.now(UTC),
            )
        )
    _audit_admin_action(database, actor, "requeue_dead_letter_job", object_id=job_id)
    refreshed = queue.get(job_id)
    return {"status": "queued", "job": refreshed.model_dump(mode="json") if refreshed else None}


def generate_admin_support_bundle(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
    actor: UserAccount,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    output = (
        root
        / ".molecule-ranker"
        / "support-bundles"
        / f"support_bundle_{uuid.uuid4().hex[:8]}.zip"
    )
    result = create_support_bundle(root_dir=root, output_path=output)
    _audit_admin_action(database, actor, "generate_support_bundle", object_id=output.name)
    return {"status": "created", "path": str(result.output_path), "manifest": result.manifest}


def run_admin_readiness_check(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
    actor: UserAccount,
) -> dict[str, Any]:
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=Path(root_dir),
            database_path=_database_file(database),
            artifact_storage_path=Path(root_dir) / ".molecule-ranker" / "artifacts",
            backup_path=Path(root_dir) / ".molecule-ranker" / "backups",
        )
    )
    _audit_admin_action(database, actor, "run_readiness_check")
    return {"status": "completed", "report": report.model_dump(mode="json")}


def run_admin_migration_dry_run(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
    actor: UserAccount,
) -> dict[str, Any]:
    manifest = migrate_artifacts(root_dir, target_version="1.9", dry_run=True)
    _audit_admin_action(database, actor, "run_migration_dry_run")
    return {"status": "completed", "manifest": manifest.model_dump(mode="json")}


def run_admin_backup_verification(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
    actor: UserAccount,
) -> dict[str, Any]:
    backup = _latest_backup(Path(root_dir).resolve())
    if backup is None:
        payload = {"status": "unavailable", "message": "No backup file found."}
    else:
        verification = verify_platform_backup(backup)
        payload = {"status": "completed", "verification": verification.to_dict()}
    _audit_admin_action(database, actor, "run_backup_verification")
    return _redact_json(payload)


def view_redacted_logs(
    database: PlatformDatabase,
    *,
    root_dir: str | Path,
    actor: UserAccount,
) -> dict[str, Any]:
    logs = _redacted_logs(Path(root_dir).resolve())
    _audit_admin_action(database, actor, "view_redacted_logs")
    return {"logs": logs, "raw_cache_files_shown": False}


def _audit_admin_action(
    database: PlatformDatabase,
    actor: UserAccount,
    action: str,
    *,
    object_id: str = "support-console",
) -> None:
    database.write_audit(
        f"admin_support_{action}",
        actor_user_id=actor.user_id,
        summary=f"Admin support action {action}.",
        object_type="admin_support_console",
        object_id=object_id,
        metadata={"action": action},
    )


def _worker_status(metrics: dict[str, Any]) -> dict[str, Any]:
    health = metrics.get("platform_health", {})
    return {
        "status": "healthy" if health.get("ok") else "degraded",
        "pending_jobs": health.get("pending_jobs", 0),
    }


def _integration_health(database: PlatformDatabase) -> dict[str, Any]:
    try:
        return _redact_json(database.integration_dashboard_summary())
    except Exception as exc:
        return {"status": "unavailable", "error": _redact_text(str(exc))}


def _storage_usage(root: Path) -> dict[str, Any]:
    artifact_root = root / ".molecule-ranker" / "artifacts"
    total_bytes = 0
    file_count = 0
    if artifact_root.exists():
        for path in artifact_root.rglob("*"):
            if path.is_file() and ".cache" not in path.parts:
                total_bytes += path.stat().st_size
                file_count += 1
    disk = shutil.disk_usage(root)
    return {
        "artifact_root": str(artifact_root),
        "artifact_file_count": file_count,
        "artifact_bytes": total_bytes,
        "disk_free_bytes": disk.free,
        "cache_payloads_included": False,
    }


def _redacted_logs(root: Path) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    for directory in (root / "logs", root / ".molecule-ranker" / "logs"):
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or ".cache" in path.parts:
                continue
            if path.suffix.lower() not in {".log", ".txt", ".trace"}:
                continue
            logs.append(
                {
                    "path": path.name,
                    "content": _redact_text(
                        path.read_text(encoding="utf-8", errors="replace")[:4000]
                    ),
                }
            )
            if len(logs) >= 10:
                return logs
    return logs


def _latest_backup(root: Path) -> Path | None:
    backup_dir = root / ".molecule-ranker" / "backups"
    backups = (
        [path for path in backup_dir.glob("*.zip") if path.is_file()]
        if backup_dir.exists()
        else []
    )
    return max(backups, key=lambda item: item.stat().st_mtime) if backups else None


def _job_summary(job: Any) -> dict[str, Any]:
    return _redact_json(
        {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "project_id": job.project_id,
            "created_at": job.created_at.isoformat(),
            "error_summary": job.error_summary or "",
        }
    )


def _database_file(database: PlatformDatabase) -> Path:
    try:
        from sqlalchemy.engine import make_url

        url = make_url(database.database_url)
        if url.drivername.startswith("sqlite") and url.database:
            return Path(url.database)
    except Exception:
        pass
    return database.root_dir / ".molecule-ranker" / "platform.sqlite"


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            sensitive_parts = ("api_key", "password", "secret", "token", "credential")
            if any(part in lowered for part in sensitive_parts):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return SENSITIVE_ASSIGNMENT_RE.sub("[REDACTED]", redact_text(redact_secrets(value)))


__all__ = [
    "build_admin_support_console",
    "cancel_job",
    "generate_admin_support_bundle",
    "retry_failed_job",
    "requeue_dead_letter_job",
    "run_admin_backup_verification",
    "run_admin_migration_dry_run",
    "run_admin_readiness_check",
    "view_redacted_logs",
]
