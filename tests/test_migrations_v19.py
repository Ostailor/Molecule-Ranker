from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.migrations.artifact_migrations import migrate_artifacts


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def test_migrates_sample_old_artifact(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    original = _write_json(
        results_dir / "candidates.json",
        {
            "schema_version": "1.0",
            "success": True,
            "disease": {"name": "synthetic condition"},
            "targets": [],
            "candidates": [{"molecule_id": "mol-1", "score": 0.42}],
            "summary": "Synthetic ranking fixture.",
        },
    )
    original_hash = hashlib.sha256(original).hexdigest()

    manifest = migrate_artifacts(results_dir, target_version="1.9", dry_run=False)

    assert manifest.summary["migrated_count"] == 1
    assert manifest.migrations[0].original_sha256 == original_hash
    assert manifest.migrations[0].backup_path is not None
    assert Path(manifest.migrations[0].backup_path).exists()
    migrated = json.loads((results_dir / "candidates.json").read_text(encoding="utf-8"))
    assert migrated["artifact_contract_version"] == "1.9"
    assert migrated["metadata"]["migration"]["source_version"] == "1.0"
    assert migrated["metadata"]["scientific_output_policy"] == (
        "Rankings and generated records are computational artifacts, not biomedical evidence."
    )
    assert (results_dir / "migration_manifest.json").exists()


def test_artifact_migration_dry_run_changes_nothing(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    original = _write_json(
        results_dir / "generated_candidates.json",
        {
            "schema_version": "1.4",
            "success": True,
            "generation_enabled": True,
            "generated_count": 1,
            "retained_generated_molecules": [{"smiles": "C"}],
        },
    )

    manifest = migrate_artifacts(results_dir, target_version="1.9", dry_run=True)

    assert (results_dir / "generated_candidates.json").read_bytes() == original
    assert manifest.summary["would_migrate_count"] == 1
    assert manifest.migrations[0].action == "would_migrate"
    assert manifest.migrations[0].backup_path is None
    assert (results_dir / "migration_manifest.json").exists()


def test_artifact_migration_creates_backup_before_overwrite(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    original = _write_json(
        results_dir / "benchmark_results.json",
        {
            "schema_version": "1.8",
            "benchmark_id": "bench-synthetic",
            "metrics": {"accuracy": 0.5},
        },
    )

    manifest = migrate_artifacts(results_dir, target_version="1.9", dry_run=False)

    backup_path = Path(manifest.migrations[0].backup_path or "")
    assert backup_path.exists()
    assert backup_path.read_bytes() == original
    backup_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    assert backup_hash == manifest.migrations[0].original_sha256
    assert "restore the backup" in manifest.rollback_plan["instructions"].lower()


def test_unsupported_artifact_reported(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    original = _write_json(results_dir / "unknown.json", {"foo": "bar"})

    manifest = migrate_artifacts(results_dir, target_version="1.9", dry_run=False)

    assert manifest.summary["unsupported_count"] == 1
    assert manifest.unsupported_artifacts[0].path.endswith("unknown.json")
    assert (results_dir / "unknown.json").read_bytes() == original


def test_migrate_artifacts_cli_dry_run(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _write_json(
        results_dir / "candidates.json",
        {
            "schema_version": "1.0",
            "success": True,
            "disease": {"name": "synthetic condition"},
            "targets": [],
            "candidates": [],
            "summary": "Synthetic ranking fixture.",
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "migrate",
            "artifacts",
            "--path",
            str(results_dir),
            "--target-version",
            "1.9",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["would_migrate_count"] == 1
    assert payload["dry_run"] is True
