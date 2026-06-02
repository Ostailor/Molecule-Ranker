from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class MigrationRecord(BaseModel):
    path: str
    artifact_kind: str
    source_version: str | None = None
    target_version: str
    action: Literal["migrated", "would_migrate", "already_current"]
    original_sha256: str
    migrated_sha256: str | None = None
    backup_path: str | None = None
    notes: list[str] = Field(default_factory=list)


class UnsupportedArtifact(BaseModel):
    path: str
    reason: str
    original_sha256: str | None = None


class MigrationManifest(BaseModel):
    manifest_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tool_version: str
    target_version: str
    dry_run: bool
    root_path: str
    migrations: list[MigrationRecord] = Field(default_factory=list)
    unsupported_artifacts: list[UnsupportedArtifact] = Field(default_factory=list)
    backups: list[dict[str, Any]] = Field(default_factory=list)
    rollback_plan: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatabaseMigrationReport(BaseModel):
    database_url: str
    database_kind: str
    current_schema_version: str
    applied_migrations: list[str] = Field(default_factory=list)
    migrations_current: bool
    dry_run: bool = False
    actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CompatibilityReport(BaseModel):
    report_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    root_path: str
    target_version: str
    compatible: bool
    checks: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_artifacts: list[UnsupportedArtifact] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def render_migration_manifest_markdown(manifest: MigrationManifest) -> str:
    lines = [
        "# Molecule Ranker Migration Manifest",
        "",
        f"- Manifest ID: {manifest.manifest_id}",
        f"- Created at: {manifest.created_at.isoformat()}",
        f"- Target version: {manifest.target_version}",
        f"- Dry run: {manifest.dry_run}",
        f"- Root path: {manifest.root_path}",
        "",
        "## Summary",
    ]
    for key, value in manifest.summary.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Artifact Actions"])
    if not manifest.migrations:
        lines.append("- None")
    for record in manifest.migrations:
        lines.append(
            f"- {record.action}: {record.path} ({record.artifact_kind}, "
            f"{record.source_version or 'unknown'} -> {record.target_version})"
        )
    lines.extend(["", "## Unsupported Artifacts"])
    if not manifest.unsupported_artifacts:
        lines.append("- None")
    for artifact in manifest.unsupported_artifacts:
        lines.append(f"- {artifact.path}: {artifact.reason}")
    lines.extend(["", "## Rollback"])
    lines.append(str(manifest.rollback_plan.get("instructions", "No rollback actions recorded.")))
    return "\n".join(lines) + "\n"


def render_compatibility_report_markdown(report: CompatibilityReport) -> str:
    lines = [
        "# Molecule Ranker Compatibility Report",
        "",
        f"- Report ID: {report.report_id}",
        f"- Created at: {report.created_at.isoformat()}",
        f"- Target version: {report.target_version}",
        f"- Compatible: {report.compatible}",
        f"- Root path: {report.root_path}",
        "",
        "## Checks",
    ]
    for check in report.checks:
        lines.append(f"- {check.get('status', 'unknown')}: {check.get('name', 'unnamed check')}")
    lines.extend(["", "## Recommendations"])
    if not report.recommendations:
        lines.append("- None")
    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")
    return "\n".join(lines) + "\n"
