from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.engine import make_url

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.guardrails import is_secret_path, redact_secrets
from molecule_ranker.platform.database import (
    artifact_records,
    metadata,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.observability import redact_for_log

BACKUP_FORMAT_VERSION = "1.0"
BackupStatus = Literal["pass", "fail"]

CACHE_PATH_MARKERS = {
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".molecule-ranker/http-cache",
}
TEMPORARY_WORKER_MARKERS = {
    ".molecule-ranker/codex-worker",
    ".molecule-ranker/workers",
    "worker-tmp",
    "tmp-worker",
}
SECRET_FILE_NAMES = {".env", ".env.local", ".env.production", "secrets.json"}
SEPARATE_DB_NAME_MARKERS = ("review", "experiment", "assay")


@dataclass(frozen=True)
class BackupResult:
    status: BackupStatus
    path: Path
    manifest: dict[str, Any]
    excluded: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            "manifest": _json_ready(self.manifest),
            "excluded": _json_ready(self.excluded),
        }


@dataclass(frozen=True)
class BackupVerificationResult:
    status: BackupStatus
    path: Path
    entry_count: int
    checked_entries: int
    errors: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            "entry_count": self.entry_count,
            "checked_entries": self.checked_entries,
            "errors": list(self.errors),
            "manifest": _json_ready(self.manifest),
        }


@dataclass(frozen=True)
class RestoreResult:
    status: BackupStatus
    input_path: Path
    target_dir: Path
    dry_run: bool
    restored_entries: int
    errors: list[str] = field(default_factory=list)
    verification: BackupVerificationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "input_path": str(self.input_path),
            "target_dir": str(self.target_dir),
            "dry_run": self.dry_run,
            "restored_entries": self.restored_entries,
            "errors": list(self.errors),
            "verification": self.verification.to_dict() if self.verification else None,
        }


def create_platform_backup(
    database: PlatformDatabase,
    *,
    output_path: Path,
    include_cache: bool = False,
    include_codex_transcripts: bool = False,
) -> BackupResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    excluded: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    created_at = datetime.now(UTC).isoformat()

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _stage_table_dumps(database, staging=staging)
        _stage_workspace_metadata(database.root_dir, staging=staging, excluded=excluded)
        _stage_sqlite_database(database, staging=staging, excluded=excluded)
        _stage_separate_databases(database.root_dir, database, staging=staging, excluded=excluded)
        _stage_artifact_files(
            database,
            staging=staging,
            excluded=excluded,
            include_cache=include_cache,
            include_codex_transcripts=include_codex_transcripts,
        )
        output_path.unlink(missing_ok=True)
        with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if not path.is_file():
                    continue
                archive_path = path.relative_to(staging).as_posix()
                data = path.read_bytes()
                entries.append(
                    {
                        "archive_path": archive_path,
                        "kind": _entry_kind(archive_path),
                        "size_bytes": len(data),
                        "sha256": _sha256_bytes(data),
                    }
                )
                archive.writestr(archive_path, data)
            manifest = {
                "backup_format_version": BACKUP_FORMAT_VERSION,
                "molecule_ranker_version": __version__,
                "created_at": created_at,
                "database": database.check(),
                "entry_count": len(entries),
                "entries": entries,
                "excluded": excluded,
                "hash_algorithm": "sha256",
            }
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
            archive.writestr("backup_manifest.json", manifest_bytes)
        return BackupResult(
            status="pass",
            path=output_path,
            manifest=manifest,
            excluded=excluded,
        )


def verify_platform_backup(input_path: Path) -> BackupVerificationResult:
    errors: list[str] = []
    manifest: dict[str, Any] = {}
    checked_entries = 0
    try:
        with zipfile.ZipFile(input_path) as archive:
            names = set(archive.namelist())
            if "backup_manifest.json" not in names:
                return BackupVerificationResult(
                    status="fail",
                    path=input_path,
                    entry_count=0,
                    checked_entries=0,
                    errors=["backup_manifest.json is missing."],
                )
            manifest = json.loads(archive.read("backup_manifest.json"))
            for entry in manifest.get("entries", []):
                archive_path = str(entry.get("archive_path") or "")
                if not _safe_archive_path(archive_path):
                    errors.append(f"Unsafe archive path in manifest: {archive_path}")
                    continue
                if archive_path not in names:
                    errors.append(f"Manifest entry missing from archive: {archive_path}")
                    continue
                data = archive.read(archive_path)
                checked_entries += 1
                expected_hash = str(entry.get("sha256") or "")
                if _sha256_bytes(data) != expected_hash:
                    errors.append(f"Hash mismatch for {archive_path}")
                expected_size = int(entry.get("size_bytes") or 0)
                if len(data) != expected_size:
                    errors.append(f"Size mismatch for {archive_path}")
            unexpected_names = names - {
                "backup_manifest.json",
                *{str(entry.get("archive_path")) for entry in manifest.get("entries", [])},
            }
            if unexpected_names:
                errors.append(
                    f"Archive contains files not listed in manifest: {sorted(unexpected_names)}"
                )
    except Exception as exc:
        errors.append(str(exc))
    return BackupVerificationResult(
        status="fail" if errors else "pass",
        path=input_path,
        entry_count=int(manifest.get("entry_count") or 0),
        checked_entries=checked_entries,
        errors=errors,
        manifest=manifest,
    )


def restore_platform_backup(
    input_path: Path,
    *,
    target_dir: Path,
    dry_run: bool = False,
) -> RestoreResult:
    verification = verify_platform_backup(input_path)
    if verification.status != "pass":
        return RestoreResult(
            status="fail",
            input_path=input_path,
            target_dir=target_dir,
            dry_run=dry_run,
            restored_entries=0,
            errors=verification.errors,
            verification=verification,
        )
    errors: list[str] = []
    restored_entries = 0
    try:
        with zipfile.ZipFile(input_path) as archive:
            entries = verification.manifest.get("entries", [])
            if dry_run:
                return RestoreResult(
                    status="pass",
                    input_path=input_path,
                    target_dir=target_dir,
                    dry_run=True,
                    restored_entries=len(entries),
                    verification=verification,
                )
            target_dir.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                archive_path = str(entry["archive_path"])
                if not _safe_archive_path(archive_path):
                    errors.append(f"Unsafe archive path: {archive_path}")
                    continue
                destination = (target_dir / archive_path).resolve()
                if not _is_relative_to(destination, target_dir.resolve()):
                    errors.append(f"Archive path escapes target: {archive_path}")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(archive_path))
                restored_entries += 1
        post_errors = _validate_restored_files(verification.manifest, target_dir)
        errors.extend(post_errors)
    except Exception as exc:
        errors.append(str(exc))
    return RestoreResult(
        status="fail" if errors else "pass",
        input_path=input_path,
        target_dir=target_dir,
        dry_run=dry_run,
        restored_entries=restored_entries,
        errors=errors,
        verification=verification,
    )


def _stage_table_dumps(database: PlatformDatabase, *, staging: Path) -> None:
    table_dir = staging / "database" / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    with database.engine.connect() as connection:
        for table in sorted(metadata.sorted_tables, key=lambda item: item.name):
            rows = [
                _sanitize_record(dict(row))
                for row in connection.execute(select(table)).mappings().fetchall()
            ]
            (table_dir / f"{table.name}.json").write_text(
                json.dumps(rows, indent=2, sort_keys=True) + "\n"
            )


def _stage_workspace_metadata(
    root_dir: Path,
    *,
    staging: Path,
    excluded: list[dict[str, Any]],
) -> None:
    workspace_path = root_dir / ".molecule-ranker" / "workspace.json"
    if not workspace_path.exists():
        return
    skip_reason = _backup_skip_reason(workspace_path, include_cache=False)
    if skip_reason:
        excluded.append(_excluded(workspace_path, skip_reason))
        return
    payload = redact_for_log(json.loads(workspace_path.read_text()))
    output_path = staging / "workspace" / "workspace.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _stage_sqlite_database(
    database: PlatformDatabase,
    *,
    staging: Path,
    excluded: list[dict[str, Any]],
) -> None:
    if database.database_kind != "sqlite":
        excluded.append(
            {"source_path": "[database]", "reason": "Non-SQLite database uses table dumps."}
        )
        return
    db_file = make_url(database.database_url).database
    if not db_file:
        excluded.append(
            {"source_path": "[database]", "reason": "SQLite database path is unavailable."}
        )
        return
    source = Path(db_file)
    if not source.exists():
        excluded.append(_excluded(source, "SQLite database file does not exist."))
        return
    output = staging / "database" / "platform.sqlite"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)


def _stage_separate_databases(
    root_dir: Path,
    database: PlatformDatabase,
    *,
    staging: Path,
    excluded: list[dict[str, Any]],
) -> None:
    platform_db_path = _sqlite_database_path(database)
    for source in sorted(root_dir.rglob("*.sqlite")):
        if platform_db_path is not None and source.resolve() == platform_db_path.resolve():
            continue
        lowered = source.name.lower()
        if not any(marker in lowered for marker in SEPARATE_DB_NAME_MARKERS):
            continue
        skip_reason = _backup_skip_reason(source, include_cache=False)
        if skip_reason:
            excluded.append(_excluded(source, skip_reason))
            continue
        output = staging / "database" / "separate" / source.relative_to(root_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)


def _stage_artifact_files(
    database: PlatformDatabase,
    *,
    staging: Path,
    excluded: list[dict[str, Any]],
    include_cache: bool,
    include_codex_transcripts: bool,
) -> None:
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records)).mappings().fetchall()
    for row in rows:
        source = Path(str(row["path"])).expanduser()
        artifact_type = str(row["artifact_type"])
        skip_reason = _artifact_skip_reason(
            source,
            artifact_type=artifact_type,
            include_cache=include_cache,
            include_codex_transcripts=include_codex_transcripts,
        )
        if skip_reason:
            excluded.append(
                {
                    **_excluded(source, skip_reason),
                    "artifact_id": row["artifact_id"],
                    "artifact_type": artifact_type,
                }
            )
            continue
        output = staging / "artifacts" / str(row["artifact_id"]) / source.name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)


def _artifact_skip_reason(
    path: Path,
    *,
    artifact_type: str,
    include_cache: bool,
    include_codex_transcripts: bool,
) -> str | None:
    if not path.exists() or not path.is_file():
        return "artifact file does not exist"
    if not include_codex_transcripts:
        lowered_type = artifact_type.lower()
        lowered_path = _normalized_path(path)
        if "codex_transcript" in lowered_type or "codex_project_outputs" in lowered_path:
            return "Codex transcripts are excluded by default"
    return _backup_skip_reason(path, include_cache=include_cache)


def _backup_skip_reason(path: Path, *, include_cache: bool) -> str | None:
    lowered_path = _normalized_path(path)
    if not include_cache and any(marker in lowered_path for marker in CACHE_PATH_MARKERS):
        return "cache files are excluded by default"
    if any(marker in lowered_path for marker in TEMPORARY_WORKER_MARKERS):
        return "temporary worker directories are excluded"
    if path.name in SECRET_FILE_NAMES or is_secret_path(path):
        return "secret-like files and environment files are excluded"
    return None


def _validate_restored_files(manifest: dict[str, Any], target_dir: Path) -> list[str]:
    errors: list[str] = []
    for entry in manifest.get("entries", []):
        archive_path = str(entry["archive_path"])
        path = target_dir / archive_path
        if not path.exists():
            errors.append(f"Restored file is missing: {archive_path}")
            continue
        if _sha256_path(path) != entry["sha256"]:
            errors.append(f"Restored file hash mismatch: {archive_path}")
    return errors


def _sanitize_record(row: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            sanitized[key] = value.isoformat()
        elif isinstance(value, bytes):
            sanitized[key] = hashlib.sha256(value).hexdigest()
        elif isinstance(value, dict | list):
            sanitized[key] = redact_for_log(value)
        elif isinstance(value, str):
            sanitized[key] = redact_secrets(value)
        else:
            sanitized[key] = value
    return sanitized


def _sqlite_database_path(database: PlatformDatabase) -> Path | None:
    if database.database_kind != "sqlite":
        return None
    path = make_url(database.database_url).database
    return Path(path) if path else None


def _entry_kind(archive_path: str) -> str:
    if archive_path.startswith("database/tables/"):
        return "database_table_dump"
    if archive_path == "database/platform.sqlite":
        return "platform_sqlite_database"
    if archive_path.startswith("database/separate/"):
        return "separate_sqlite_database"
    if archive_path.startswith("artifacts/"):
        return "artifact_file"
    if archive_path.startswith("workspace/"):
        return "project_workspace_metadata"
    return "metadata"


def _safe_archive_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        return False
    return bool(path and path != "backup_manifest.json")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _excluded(path: Path, reason: str) -> dict[str, Any]:
    return {"source_path": _safe_source_path(path), "reason": reason}


def _safe_source_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _normalized_path(path: Path) -> str:
    return str(path).replace(os.sep, "/").lower()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


__all__ = [
    "BACKUP_FORMAT_VERSION",
    "BackupResult",
    "BackupVerificationResult",
    "RestoreResult",
    "create_platform_backup",
    "restore_platform_backup",
    "verify_platform_backup",
]
