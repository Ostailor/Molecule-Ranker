from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from molecule_ranker.platform.backup import (
    BackupResult,
    RestoreResult,
    create_platform_backup,
    restore_platform_backup,
    verify_platform_backup,
)
from molecule_ranker.platform.database import (
    SCHEMA_VERSION,
    PlatformDatabase,
    artifact_records,
    memberships,
    project_permissions,
    project_workspaces,
    users,
)
from molecule_ranker.platform.readiness import ReadinessConfig, run_smoke_test

SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9]{8,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|"
    r"MOLECULE_RANKER_[A-Z0-9_]*SECRET[A-Z0-9_]*=",
    re.I,
)


@dataclass(frozen=True)
class DisasterRecoveryReport:
    status: str
    created_at: datetime
    output_dir: Path
    backup_path: Path
    restore_dir: Path
    report_path: Path
    markdown_path: Path
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "output_dir": str(self.output_dir),
            "backup_path": str(self.backup_path),
            "restore_dir": str(self.restore_dir),
            "report_path": str(self.report_path),
            "markdown_path": str(self.markdown_path),
            "checks": _json_ready(self.checks),
        }


def run_disaster_recovery_drill(
    database: PlatformDatabase,
    *,
    output_dir: str | Path,
    key_project_ids: list[str] | None = None,
    key_artifact_ids: list[str] | None = None,
) -> DisasterRecoveryReport:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    backup_path = output / "platform_dr_backup.zip"
    restore_dir = output / "restored_environment"
    _clear_restore_dir(restore_dir)

    backup = create_platform_backup(database, output_path=backup_path)
    verification = verify_platform_backup(backup.path)
    restore = restore_platform_backup(backup.path, target_dir=restore_dir, dry_run=False)
    restored_db = _restored_database(restore_dir)

    checks = {
        "backup_created": _check(
            backup.status == "pass" and backup.path.exists(),
            "Backup created.",
            backup.to_dict(),
        ),
        "backup_manifest": _check(
            verification.status == "pass",
            "Backup manifest and entry hashes verified.",
            verification.to_dict(),
        ),
        "restore": _check(
            restore.status == "pass",
            "Backup restored into temporary environment.",
            restore.to_dict(),
        ),
        "migration": _migration_check(restored_db),
        "artifact_hashes": _artifact_hash_check(restore),
        "key_project_artifact_load": _key_project_artifact_check(
            restored_db,
            key_project_ids=key_project_ids or [],
            key_artifact_ids=key_artifact_ids or [],
        ),
        "user_role_metadata": _user_role_metadata_check(restored_db),
        "no_secrets_in_backup": _no_secrets_check(backup),
        "smoke_workflow": _smoke_check(restore_dir),
    }
    status = "pass" if all(check["status"] == "pass" for check in checks.values()) else "fail"
    report_path = output / "dr_report.json"
    markdown_path = output / "dr_report.md"
    report = DisasterRecoveryReport(
        status=status,
        created_at=datetime.now(UTC),
        output_dir=output,
        backup_path=backup_path,
        restore_dir=restore_dir,
        report_path=report_path,
        markdown_path=markdown_path,
        checks=checks,
    )
    report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(_render_markdown(report))
    return report


def _restored_database(restore_dir: Path) -> PlatformDatabase:
    db_path = restore_dir / "database" / "platform.sqlite"
    return PlatformDatabase(restore_dir, db_path=db_path, initialize=False)


def _migration_check(database: PlatformDatabase) -> dict[str, Any]:
    try:
        check = database.check()
        applied = database.applied_migrations()
    except Exception as exc:
        return _check(False, "Database migration check failed.", {"error": str(exc)})
    return _check(
        check.get("ok") is True and SCHEMA_VERSION in applied,
        "Restored database migrations are current.",
        {"database_check": check, "schema_version": SCHEMA_VERSION, "applied_migrations": applied},
    )


def _artifact_hash_check(restore: RestoreResult) -> dict[str, Any]:
    verification = restore.verification
    ok = restore.status == "pass" and verification is not None and verification.status == "pass"
    return _check(
        ok,
        "Restored artifact hashes match the backup manifest.",
        {
            "restore_status": restore.status,
            "checked_entries": verification.checked_entries if verification else 0,
            "errors": restore.errors,
        },
    )


def _key_project_artifact_check(
    database: PlatformDatabase,
    *,
    key_project_ids: list[str],
    key_artifact_ids: list[str],
) -> dict[str, Any]:
    missing_projects: list[str] = []
    missing_artifacts: list[str] = []
    try:
        with database.engine.connect() as connection:
            for project_id in key_project_ids:
                exists = connection.execute(
                    select(project_workspaces.c.project_id).where(
                        project_workspaces.c.project_id == project_id
                    )
                ).first()
                if exists is None:
                    missing_projects.append(project_id)
            for artifact_id in key_artifact_ids:
                exists = connection.execute(
                    select(artifact_records.c.artifact_id).where(
                        artifact_records.c.artifact_id == artifact_id
                    )
                ).first()
                if exists is None:
                    missing_artifacts.append(artifact_id)
    except Exception as exc:
        return _check(False, "Key project/artifact load check failed.", {"error": str(exc)})
    return _check(
        not missing_projects and not missing_artifacts,
        "Key projects and artifacts load from the restored database.",
        {"missing_projects": missing_projects, "missing_artifacts": missing_artifacts},
    )


def _user_role_metadata_check(database: PlatformDatabase) -> dict[str, Any]:
    try:
        with database.engine.connect() as connection:
            user_count = connection.execute(select(users.c.user_id)).fetchall()
            memberships_count = connection.execute(select(memberships.c.membership_id)).fetchall()
            permission_count = connection.execute(
                select(project_permissions.c.permission_id)
            ).fetchall()
    except Exception as exc:
        return _check(False, "User and role metadata check failed.", {"error": str(exc)})
    return _check(
        bool(user_count),
        "User and role metadata is present in the restored database.",
        {
            "user_count": len(user_count),
            "membership_count": len(memberships_count),
            "project_permission_count": len(permission_count),
        },
    )


def _no_secrets_check(backup: BackupResult) -> dict[str, Any]:
    try:
        names_text = " ".join(entry["archive_path"] for entry in backup.manifest.get("entries", []))
        archive_text = backup.path.read_bytes().decode("latin1", errors="ignore")
    except Exception as exc:
        return _check(False, "Backup secret scan failed.", {"error": str(exc)})
    secret_file_names = {".env", ".env.local", ".env.production", "secrets.json"}
    secret_name_present = any(name in names_text for name in secret_file_names)
    secret_value_present = bool(SECRET_VALUE_RE.search(archive_text))
    return _check(
        not secret_name_present and not secret_value_present,
        "Backup excludes secret files and plaintext secret values.",
        {
            "secret_file_names_present": secret_name_present,
            "plaintext_secret_values_present": secret_value_present,
            "excluded_count": len(backup.excluded),
        },
    )


def _smoke_check(restore_dir: Path) -> dict[str, Any]:
    config = ReadinessConfig(
        root_dir=restore_dir,
        database_path=restore_dir / "database" / "platform.sqlite",
        artifact_storage_root=restore_dir / "artifacts",
        backup_path=restore_dir / "backups",
    )
    report = run_smoke_test(config)
    return _check(
        report.status == "pass",
        "Smoke workflow passed on the restored environment.",
        report.to_dict(),
    )


def _check(passed: bool, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "pass" if passed else "fail", "message": message, "details": details or {}}


def _render_markdown(report: DisasterRecoveryReport) -> str:
    lines = [
        "# V2.0 Disaster Recovery Drill",
        "",
        f"- Status: `{report.status}`",
        f"- Backup: `{report.backup_path}`",
        f"- Restore target: `{report.restore_dir}`",
        "",
        "| Check | Status | Message |",
        "| --- | --- | --- |",
    ]
    for check_id, check in report.checks.items():
        lines.append(f"| `{check_id}` | {check['status']} | {check['message']} |")
    lines.append("")
    return "\n".join(lines)


def _clear_restore_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


__all__ = ["DisasterRecoveryReport", "run_disaster_recovery_drill"]
