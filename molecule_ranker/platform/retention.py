from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update

from molecule_ranker.codex_backbone.guardrails import is_secret_path
from molecule_ranker.platform.database import (
    active_learning_batches,
    activity_feed,
    artifact_records,
    assay_results,
    assignments,
    codex_worker_jobs,
    notifications,
    platform_audit_events,
    platform_jobs,
    project_comments,
    project_permissions,
    project_runs,
    project_workspaces,
    review_workspaces,
)
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.schemas import RetentionPolicy

CACHE_MARKERS = {".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


@dataclass(frozen=True)
class DataRetentionPolicy:
    artifact_retention_days: int | None = None
    codex_transcript_retention_days: int | None = None
    audit_log_retention_days: int | None = None
    cache_retention_days: int | None = None
    assay_result_retention_days: int | None = None


@dataclass(frozen=True)
class RetentionRunResult:
    artifacts_soft_deleted: int = 0
    codex_transcripts_soft_deleted: int = 0
    audit_events_deleted: int = 0
    cache_files_deleted: int = 0
    assay_results_soft_deleted: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "artifacts_soft_deleted": self.artifacts_soft_deleted,
            "codex_transcripts_soft_deleted": self.codex_transcripts_soft_deleted,
            "audit_events_deleted": self.audit_events_deleted,
            "cache_files_deleted": self.cache_files_deleted,
            "assay_results_soft_deleted": self.assay_results_soft_deleted,
        }


def get_retention_policy(
    database: PlatformDatabase,
    *,
    scope_type: str,
    scope_id: str,
) -> RetentionPolicy:
    return database.get_retention_policy(scope_type=scope_type, scope_id=scope_id)


def list_active_projects(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = connection.execute(select(project_workspaces)).mappings().fetchall()
    return [dict(row) for row in rows if not _is_soft_deleted(dict(row))]


def soft_delete_project(
    database: PlatformDatabase,
    *,
    project_id: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    project = _project_row(database, project_id)
    if project is None:
        raise PlatformDatabaseError(f"Project not found: {project_id}")
    metadata = dict(project.get("metadata_json") or {})
    if "deleted_at" not in metadata:
        metadata.update(
            {
                "deleted_at": datetime.now(UTC).isoformat(),
                "deleted_by": actor_user_id,
                "delete_mode": "soft",
            }
        )
        with database.engine.begin() as connection:
            connection.execute(
                update(project_workspaces)
                .where(project_workspaces.c.project_id == project_id)
                .values(metadata_json=metadata, updated_at=datetime.now(UTC))
            )
    database.write_audit(
        "project_soft_deleted",
        actor_user_id=actor_user_id,
        org_id=str(project.get("org_id") or "default"),
        project_id=project_id,
        summary=f"Soft-deleted project {project_id}.",
        object_type="project",
        object_id=project_id,
        metadata={"delete_mode": "soft"},
    )
    refreshed = _project_row(database, project_id)
    if refreshed is None:
        raise PlatformDatabaseError("Project disappeared during soft delete.")
    return refreshed


def hard_delete_project(
    database: PlatformDatabase,
    *,
    project_id: str,
    confirm_project_id: str | None,
    actor_user_id: str | None = None,
    delete_files: bool = False,
) -> dict[str, Any]:
    if confirm_project_id != project_id:
        raise PlatformDatabaseError("Hard delete requires --confirm-project-id to match.")
    project = _project_row(database, project_id)
    if project is None:
        raise PlatformDatabaseError(f"Project not found: {project_id}")
    artifact_paths = _project_artifact_paths(database, project_id)
    deleted_files = 0
    if delete_files:
        deleted_files = _delete_safe_artifact_files(artifact_paths, root_dir=database.root_dir)
    deleted_rows: dict[str, int] = {}
    tables = [
        active_learning_batches,
        activity_feed,
        assignments,
        assay_results,
        codex_worker_jobs,
        notifications,
        platform_jobs,
        project_comments,
        project_permissions,
        project_runs,
        review_workspaces,
        artifact_records,
        project_workspaces,
    ]
    with database.engine.begin() as connection:
        for table in tables:
            result = connection.execute(delete(table).where(table.c.project_id == project_id))
            deleted_rows[table.name] = int(result.rowcount or 0)
    database.write_audit(
        "project_hard_deleted",
        actor_user_id=actor_user_id,
        org_id=str(project.get("org_id") or "default"),
        project_id=project_id,
        summary=f"Hard-deleted project {project_id}.",
        object_type="project",
        object_id=project_id,
        metadata={
            "delete_mode": "hard",
            "deleted_rows": deleted_rows,
            "deleted_files": deleted_files,
        },
    )
    return {"project_id": project_id, "deleted_rows": deleted_rows, "deleted_files": deleted_files}


def run_retention(
    database: PlatformDatabase,
    *,
    policy: DataRetentionPolicy | None = None,
    actor_user_id: str | None = None,
) -> RetentionRunResult:
    active_policy = policy or DataRetentionPolicy()
    result = RetentionRunResult(
        artifacts_soft_deleted=_soft_delete_old_artifacts(
            database,
            retention_days=active_policy.artifact_retention_days,
            artifact_type=None,
        ),
        codex_transcripts_soft_deleted=_soft_delete_old_artifacts(
            database,
            retention_days=active_policy.codex_transcript_retention_days,
            artifact_type="codex_transcript",
        ),
        audit_events_deleted=_delete_old_audit_events(
            database,
            retention_days=active_policy.audit_log_retention_days,
        ),
        cache_files_deleted=_delete_old_cache_files(
            database.root_dir,
            retention_days=active_policy.cache_retention_days,
        ),
        assay_results_soft_deleted=_soft_delete_old_assay_results(
            database,
            retention_days=active_policy.assay_result_retention_days,
        ),
    )
    database.write_audit(
        "retention_run_completed",
        actor_user_id=actor_user_id,
        summary="Completed platform retention run.",
        object_type="retention_policy",
        object_id="platform",
        metadata=result.model_dump(),
    )
    return result


def _soft_delete_old_artifacts(
    database: PlatformDatabase,
    *,
    retention_days: int | None,
    artifact_type: str | None,
) -> int:
    if retention_days is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    statement = select(artifact_records).where(artifact_records.c.created_at < cutoff)
    if artifact_type is not None:
        statement = statement.where(artifact_records.c.artifact_type == artifact_type)
    with database.engine.connect() as connection:
        rows = connection.execute(statement).mappings().fetchall()
    changed = 0
    with database.engine.begin() as connection:
        for row in rows:
            metadata = dict(row["metadata_json"] or {})
            if metadata.get("retention_deleted_at"):
                continue
            metadata["retention_deleted_at"] = datetime.now(UTC).isoformat()
            metadata["retention_delete_mode"] = "soft"
            connection.execute(
                update(artifact_records)
                .where(artifact_records.c.artifact_id == row["artifact_id"])
                .values(metadata_json=metadata)
            )
            changed += 1
    return changed


def _soft_delete_old_assay_results(
    database: PlatformDatabase,
    *,
    retention_days: int | None,
) -> int:
    if retention_days is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    with database.engine.connect() as connection:
        rows = (
            connection.execute(select(assay_results).where(assay_results.c.created_at < cutoff))
            .mappings()
            .fetchall()
        )
    changed = 0
    with database.engine.begin() as connection:
        for row in rows:
            metadata = dict(row["metadata_json"] or {})
            if metadata.get("retention_deleted_at"):
                continue
            metadata["retention_deleted_at"] = datetime.now(UTC).isoformat()
            metadata["retention_delete_mode"] = "soft"
            connection.execute(
                update(assay_results)
                .where(assay_results.c.assay_result_id == row["assay_result_id"])
                .values(metadata_json=metadata)
            )
            changed += 1
    return changed


def _delete_old_audit_events(database: PlatformDatabase, *, retention_days: int | None) -> int:
    if retention_days is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    with database.engine.begin() as connection:
        result = connection.execute(
            delete(platform_audit_events).where(platform_audit_events.c.timestamp < cutoff)
        )
    return int(result.rowcount or 0)


def _delete_old_cache_files(root_dir: Path, *, retention_days: int | None) -> int:
    if retention_days is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    for marker in CACHE_MARKERS:
        for path in root_dir.rglob(marker):
            if not path.exists():
                continue
            for file_path in sorted(path.rglob("*"), reverse=True):
                if file_path.is_file() and _mtime(file_path) < cutoff:
                    file_path.unlink()
                    deleted += 1
    return deleted


def _delete_safe_artifact_files(paths: list[Path], *, root_dir: Path) -> int:
    root = root_dir.resolve()
    deleted = 0
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        lowered = str(resolved).lower()
        if any(marker in lowered for marker in CACHE_MARKERS):
            continue
        if resolved.name == ".env" or is_secret_path(resolved):
            continue
        if resolved.exists() and resolved.is_file():
            resolved.unlink()
            deleted += 1
    return deleted


def _project_artifact_paths(database: PlatformDatabase, project_id: str) -> list[Path]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(artifact_records.c.path).where(artifact_records.c.project_id == project_id)
            )
            .mappings()
            .fetchall()
        )
    return [Path(str(row["path"])) for row in rows]


def _project_row(database: PlatformDatabase, project_id: str) -> dict[str, Any] | None:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(project_workspaces).where(project_workspaces.c.project_id == project_id)
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def _is_soft_deleted(project: dict[str, Any]) -> bool:
    return bool(dict(project.get("metadata_json") or {}).get("deleted_at"))


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


__all__ = [
    "DataRetentionPolicy",
    "RetentionPolicy",
    "RetentionRunResult",
    "get_retention_policy",
    "hard_delete_project",
    "list_active_projects",
    "run_retention",
    "soft_delete_project",
]
