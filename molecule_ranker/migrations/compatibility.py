from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from molecule_ranker.migrations.artifact_migrations import migrate_artifacts
from molecule_ranker.migrations.db_migrations import check_database_migrations
from molecule_ranker.migrations.reports import CompatibilityReport


def run_compatibility_check(
    root: str | Path,
    *,
    target_version: str = "1.9",
    database_url: str | None = None,
    db_path: str | Path | None = None,
) -> CompatibilityReport:
    root_path = Path(root).resolve()
    checks: list[dict[str, Any]] = []
    recommendations: list[str] = []

    artifact_manifest = migrate_artifacts(root_path, target_version=target_version, dry_run=True)
    unsupported_count = len(artifact_manifest.unsupported_artifacts)
    would_migrate_count = artifact_manifest.summary.get("would_migrate_count", 0)
    checks.append(
        {
            "name": "artifact_contract_compatibility",
            "status": "warning" if unsupported_count else "pass",
            "details": {
                "would_migrate_count": would_migrate_count,
                "unsupported_count": unsupported_count,
                "manifest_path": str(root_path / "migration_manifest.json"),
            },
        }
    )
    if unsupported_count:
        recommendations.append(
            "Review unsupported artifacts before pilot migration; unsupported files were "
            "left unchanged."
        )
    if would_migrate_count:
        recommendations.append(
            "Run artifact migration without --dry-run after reviewing migration_manifest.json."
        )

    db_report = check_database_migrations(
        root_dir=root_path,
        database_url=database_url,
        db_path=db_path,
    )
    checks.append(
        {
            "name": "database_migrations_current",
            "status": "pass" if db_report.migrations_current else "warning",
            "details": {
                "database": db_report.database_kind,
                "database_url": db_report.database_url,
                "current_schema_version": db_report.current_schema_version,
                "applied_migrations": db_report.applied_migrations,
                "warnings": db_report.warnings,
            },
        }
    )
    if not db_report.migrations_current:
        recommendations.append("Run molecule-ranker migrate db before enterprise pilot launch.")

    return CompatibilityReport(
        report_id=f"compat-{uuid.uuid4().hex[:12]}",
        root_path=str(root_path),
        target_version=target_version,
        compatible=all(check["status"] == "pass" for check in checks),
        checks=checks,
        unsupported_artifacts=artifact_manifest.unsupported_artifacts,
        recommendations=recommendations,
        metadata={
            "dry_run": True,
            "scope": "Compatibility checks do not recompute scientific outputs or call live APIs.",
        },
    )
