from __future__ import annotations

import copy
import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.migrations.reports import (
    MigrationManifest,
    MigrationRecord,
    UnsupportedArtifact,
)

SUPPORTED_TARGET_VERSION = "1.9"
MANIFEST_FILENAME = "migration_manifest.json"
BACKUP_DIRNAME = ".migration_backups"
SKIPPED_DIRS = {BACKUP_DIRNAME, ".cache", "__pycache__", ".git", ".pytest_cache"}


@dataclass(frozen=True)
class ArtifactMigrationConfig:
    path: Path
    target_version: str = SUPPORTED_TARGET_VERSION
    dry_run: bool = False
    manifest_filename: str = MANIFEST_FILENAME
    backup_dir: Path | None = None


def migrate_artifacts(
    path: str | Path,
    *,
    target_version: str = SUPPORTED_TARGET_VERSION,
    dry_run: bool = False,
    manifest_filename: str = MANIFEST_FILENAME,
    backup_dir: str | Path | None = None,
) -> MigrationManifest:
    """Migrate recognized JSON artifact contracts to the V1.9 pilot contract.

    The migrator intentionally avoids scientific recomputation. It only adds
    compatibility metadata and safety labels to known artifact shapes.
    """

    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"artifact path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"artifact path must be a directory: {root}")

    target = _normalize_target_version(target_version)
    if target != SUPPORTED_TARGET_VERSION:
        raise ValueError(f"unsupported target artifact contract version: {target_version}")

    manifest_id = f"migration-{uuid.uuid4().hex[:12]}"
    resolved_backup_dir = (
        Path(backup_dir).resolve() if backup_dir else root / BACKUP_DIRNAME / manifest_id
    )
    migrations: list[MigrationRecord] = []
    unsupported: list[UnsupportedArtifact] = []
    backups: list[dict[str, Any]] = []

    for artifact_path in _iter_json_artifacts(root, manifest_filename=manifest_filename):
        original_hash = sha256_file(artifact_path)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            unsupported.append(
                UnsupportedArtifact(
                    path=_relative_path(root, artifact_path),
                    reason=f"JSON could not be parsed: {exc.msg}",
                    original_sha256=original_hash,
                )
            )
            continue
        if not isinstance(payload, dict):
            unsupported.append(
                UnsupportedArtifact(
                    path=_relative_path(root, artifact_path),
                    reason="Only JSON object artifacts are supported for V1.9 migration.",
                    original_sha256=original_hash,
                )
            )
            continue

        plan = _plan_artifact_migration(artifact_path, payload, target_version=target)
        if plan is None:
            unsupported.append(
                UnsupportedArtifact(
                    path=_relative_path(root, artifact_path),
                    reason="Artifact shape is not a recognized molecule-ranker contract.",
                    original_sha256=original_hash,
                )
            )
            continue

        migrated_payload = plan["payload"]
        source_version = plan["source_version"]
        artifact_kind = plan["artifact_kind"]
        notes = plan["notes"]
        if migrated_payload == payload:
            migrations.append(
                MigrationRecord(
                    path=_relative_path(root, artifact_path),
                    artifact_kind=artifact_kind,
                    source_version=source_version,
                    target_version=target,
                    action="already_current",
                    original_sha256=original_hash,
                    migrated_sha256=original_hash,
                    notes=notes,
                )
            )
            continue

        migrated_bytes = _json_bytes(migrated_payload)
        migrated_hash = hashlib.sha256(migrated_bytes).hexdigest()
        backup_path: Path | None = None
        if not dry_run:
            backup_path = _backup_artifact(root, artifact_path, resolved_backup_dir)
            artifact_path.write_bytes(migrated_bytes)
            backups.append(
                {
                    "artifact_path": _relative_path(root, artifact_path),
                    "backup_path": str(backup_path),
                    "original_sha256": original_hash,
                }
            )
        migrations.append(
            MigrationRecord(
                path=_relative_path(root, artifact_path),
                artifact_kind=artifact_kind,
                source_version=source_version,
                target_version=target,
                action="would_migrate" if dry_run else "migrated",
                original_sha256=original_hash,
                migrated_sha256=migrated_hash,
                backup_path=str(backup_path) if backup_path else None,
                notes=notes,
            )
        )

    manifest = _build_manifest(
        root=root,
        manifest_id=manifest_id,
        target_version=target,
        dry_run=dry_run,
        migrations=migrations,
        unsupported=unsupported,
        backups=backups,
    )
    _write_manifest(root / manifest_filename, manifest)
    return manifest


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_json_artifacts(root: Path, *, manifest_filename: str) -> list[Path]:
    artifacts: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.name == manifest_filename:
            continue
        artifacts.append(path)
    return artifacts


def _plan_artifact_migration(
    path: Path,
    payload: dict[str, Any],
    *,
    target_version: str,
) -> dict[str, Any] | None:
    artifact_kind = _classify_artifact(path, payload)
    if artifact_kind is None:
        return None
    migrated = copy.deepcopy(payload)
    source_version = _artifact_version(payload)
    notes = [
        (
            "No scientific values, molecules, scores, assay results, or benchmark metrics "
            "were recomputed."
        ),
    ]
    migrated["artifact_contract_version"] = target_version
    migrated.setdefault("schema_version", source_version or "unknown")
    metadata = migrated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["migration"] = {
        "migrated_to": target_version,
        "source_version": source_version,
        "migrated_at": datetime.now(UTC).isoformat(),
        "migration_scope": "metadata-only compatibility update",
    }
    metadata["scientific_output_policy"] = (
        "Rankings and generated records are computational artifacts, not biomedical evidence."
    )
    migrated["metadata"] = metadata

    if artifact_kind in {"generated_candidates", "generation_trace", "hypotheses"}:
        migrated["generated_molecule_warning"] = (
            "Generated molecules are computational hypotheses and are not claims of safety, "
            "activity, binding, synthesizability, or clinical utility."
        )
        notes.append("Added generated molecule computational-hypothesis warning.")
    if artifact_kind in {"evaluation", "benchmark"}:
        migrated["evaluation_output_label"] = (
            "Evaluation and benchmark outputs are evaluation artifacts, not biomedical evidence."
        )
        notes.append("Added evaluation-artifact label.")
    if artifact_kind in {"codex", "codex_review"}:
        migrated["codex_output_label"] = (
            "Codex output is engineering assistance text and is not evidence, assay data, "
            "molecule data, a score, a benchmark result, or a decision."
        )
        notes.append("Added Codex output label.")

    return {
        "artifact_kind": artifact_kind,
        "source_version": source_version,
        "payload": migrated,
        "notes": notes,
    }


def _classify_artifact(path: Path, payload: dict[str, Any]) -> str | None:
    name = path.name.lower()
    keys = set(payload)
    if name == "candidates.json" or {"candidates", "targets", "summary"} <= keys:
        return "candidates"
    if name in {"generated_candidates.json", "generation_trace.json"}:
        return "generated_candidates" if name == "generated_candidates.json" else "generation_trace"
    generated_keys = ("generated_molecule_hypotheses", "retained_generated_molecules")
    if any(key in keys for key in generated_keys):
        return "generated_candidates"
    if "hypotheses" in keys or name.startswith("hypothesis"):
        return "hypotheses"
    if "benchmark_id" in keys or name.startswith("benchmark"):
        return "benchmark"
    if "evaluation_id" in keys or "metrics" in keys or name.startswith("evaluation"):
        return "evaluation"
    if "codex_task_id" in keys or "guardrail_status" in keys or name.startswith("codex"):
        return "codex"
    if "review_items" in keys or name.startswith("review"):
        return "review"
    if "portfolio" in keys or name.startswith("portfolio"):
        return "portfolio"
    if "campaign_id" in keys or name.startswith("campaign"):
        return "campaign"
    if "graph" in keys or "nodes" in keys and "edges" in keys:
        return "knowledge_graph"
    if "developability_assessments" in keys or name.startswith("developability"):
        return "developability"
    return None


def _artifact_version(payload: dict[str, Any]) -> str | None:
    for key in ("artifact_contract_version", "schema_version", "version"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("artifact_contract_version") or metadata.get("schema_version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _backup_artifact(root: Path, artifact_path: Path, backup_root: Path) -> Path:
    relative = artifact_path.relative_to(root)
    backup_path = backup_root / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact_path, backup_path)
    return backup_path


def _build_manifest(
    *,
    root: Path,
    manifest_id: str,
    target_version: str,
    dry_run: bool,
    migrations: list[MigrationRecord],
    unsupported: list[UnsupportedArtifact],
    backups: list[dict[str, Any]],
) -> MigrationManifest:
    migrated_count = sum(1 for item in migrations if item.action == "migrated")
    would_migrate_count = sum(1 for item in migrations if item.action == "would_migrate")
    already_current_count = sum(1 for item in migrations if item.action == "already_current")
    rollback_plan = {
        "available": bool(backups),
        "instructions": (
            "To rollback, restore the backup files listed in this manifest to their artifact paths "
            "and verify each restored file against original_sha256."
            if backups
            else "No overwrite occurred; no rollback action is required."
        ),
        "backups": backups,
    }
    return MigrationManifest(
        manifest_id=manifest_id,
        tool_version=__version__,
        target_version=target_version,
        dry_run=dry_run,
        root_path=str(root),
        migrations=migrations,
        unsupported_artifacts=unsupported,
        backups=backups,
        rollback_plan=rollback_plan,
        summary={
            "artifact_count": len(migrations) + len(unsupported),
            "migrated_count": migrated_count,
            "would_migrate_count": would_migrate_count,
            "already_current_count": already_current_count,
            "unsupported_count": len(unsupported),
            "backup_count": len(backups),
        },
        metadata={
            "safety": (
                "Migration reports contain paths, hashes, and metadata only. Artifact contents, "
                "secrets, cache files, environment variables, and credentials are not included."
            )
        },
    )


def _write_manifest(path: Path, manifest: MigrationManifest) -> None:
    path.write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _normalize_target_version(version: str) -> str:
    cleaned = str(version).strip()
    if cleaned == "1.9.0":
        return SUPPORTED_TARGET_VERSION
    return cleaned


def _relative_path(root: Path, path: Path) -> str:
    return str(path.relative_to(root))
