from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from molecule_ranker.codex_backbone.guardrails import is_secret_path, redact_secrets
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
from molecule_ranker.platform.observability import redact_for_log

CACHE_PATH_MARKERS = {
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".molecule-ranker/http-cache",
}


@dataclass(frozen=True)
class ProjectExportPackage:
    project_id: str
    path: Path
    artifact_count: int
    skipped_artifact_count: int
    sha256: str


def export_project_package(
    database: PlatformDatabase,
    *,
    project_id: str,
    output_path: Path | None = None,
    actor_user_id: str | None = None,
) -> ProjectExportPackage:
    project = _project_row(database, project_id)
    if project is None:
        raise PlatformDatabaseError(f"Project not found: {project_id}")
    export_path = output_path or _default_export_path(database.root_dir, project_id=project_id)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _project_export_payload(database, project_id=project_id, project=project)
    included_artifacts = [
        item for item in payload["artifact_manifest"] if item.get("included") is True
    ]
    skipped_artifacts = [
        item for item in payload["artifact_manifest"] if item.get("included") is not True
    ]

    with zipfile.ZipFile(export_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "project_export.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        for manifest_item in included_artifacts:
            source_path = Path(str(manifest_item["source_path"]))
            archive_name = str(manifest_item["export_path"])
            archive.write(source_path, archive_name)

    package_hash = _sha256_path(export_path)
    database.write_audit(
        "project_exported",
        actor_user_id=actor_user_id,
        org_id=str(project.get("org_id") or "default"),
        project_id=project_id,
        summary=f"Exported project {project_id}.",
        object_type="project",
        object_id=project_id,
        metadata={
            "export_path": str(export_path.resolve()),
            "sha256": package_hash,
            "artifact_count": len(included_artifacts),
            "skipped_artifact_count": len(skipped_artifacts),
        },
    )
    return ProjectExportPackage(
        project_id=project_id,
        path=export_path,
        artifact_count=len(included_artifacts),
        skipped_artifact_count=len(skipped_artifacts),
        sha256=package_hash,
    )


def export_user_data_package(
    database: PlatformDatabase,
    *,
    user_id: str,
    output_path: Path | None = None,
    actor_user_id: str | None = None,
) -> Path:
    export_path = output_path or _default_export_path(database.root_dir, project_id=user_id)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_for_log(database.export_user_data(user_id))
    with zipfile.ZipFile(export_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "user_export.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
    database.write_audit(
        "user_data_exported",
        actor_user_id=actor_user_id,
        summary=f"Exported user data for {user_id}.",
        object_type="user",
        object_id=user_id,
        metadata={"export_path": str(export_path.resolve()), "sha256": _sha256_path(export_path)},
    )
    return export_path


def _project_export_payload(
    database: PlatformDatabase,
    *,
    project_id: str,
    project: dict[str, Any],
) -> dict[str, Any]:
    tables = {
        "project_permissions": project_permissions,
        "project_runs": project_runs,
        "review_workspaces": review_workspaces,
        "assay_results": assay_results,
        "active_learning_batches": active_learning_batches,
        "platform_jobs": platform_jobs,
        "codex_worker_jobs": codex_worker_jobs,
        "project_comments": project_comments,
        "assignments": assignments,
        "notifications": notifications,
        "activity_feed": activity_feed,
        "audit_events": platform_audit_events,
    }
    with database.engine.connect() as connection:
        table_payloads = {
            name: [
                _sanitize_record(dict(row))
                for row in connection.execute(
                    select(table).where(table.c.project_id == project_id)
                )
                .mappings()
                .fetchall()
            ]
            for name, table in tables.items()
        }
        artifacts = [
            dict(row)
            for row in connection.execute(
                select(artifact_records).where(artifact_records.c.project_id == project_id)
            )
            .mappings()
            .fetchall()
        ]
    artifact_manifest = [_artifact_manifest_item(row) for row in artifacts]
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "project": _sanitize_record(project),
        "artifact_manifest": artifact_manifest,
        **table_payloads,
    }


def _artifact_manifest_item(row: dict[str, Any]) -> dict[str, Any]:
    source_path = Path(str(row["path"])).resolve()
    base_item = {
        "artifact_id": str(row["artifact_id"]),
        "artifact_type": str(row["artifact_type"]),
        "project_id": row.get("project_id"),
        "run_id": row.get("run_id"),
        "source_path": str(source_path),
        "recorded_sha256": row.get("sha256"),
        "recorded_size_bytes": row.get("size_bytes"),
        "included": False,
        "skip_reason": "",
    }
    skip_reason = _export_skip_reason(source_path)
    if skip_reason:
        return {**base_item, "source_path": "[EXCLUDED]", "skip_reason": skip_reason}
    actual_hash = _sha256_path(source_path)
    export_name = f"artifacts/{row['artifact_id']}-{source_path.name}"
    return {
        **base_item,
        "included": True,
        "skip_reason": None,
        "actual_sha256": actual_hash,
        "actual_size_bytes": source_path.stat().st_size,
        "export_path": export_name,
    }


def _export_skip_reason(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return "artifact file does not exist"
    lowered = str(path).lower()
    if any(marker in lowered for marker in CACHE_PATH_MARKERS):
        return "cache files are excluded from exports"
    if path.name == ".env" or is_secret_path(path):
        return "secret-like files are excluded from exports"
    return None


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


def _default_export_path(root_dir: Path, *, project_id: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root_dir / ".molecule-ranker" / "exports" / f"{project_id}-{timestamp}.zip"


def _sanitize_record(row: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            sanitized[key] = value.isoformat()
        elif isinstance(value, dict | list):
            sanitized[key] = redact_for_log(value)
        elif isinstance(value, str):
            sanitized[key] = redact_secrets(value)
        else:
            sanitized[key] = value
    return sanitized


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ProjectExportPackage",
    "export_project_package",
    "export_user_data_package",
]
