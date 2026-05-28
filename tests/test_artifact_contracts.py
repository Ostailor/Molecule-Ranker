from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.contracts import (
    ARTIFACT_CONTRACT_VERSION,
    ARTIFACT_CONTRACTS,
    validate_artifact_directory,
    validate_artifact_file,
)


def test_v1_artifact_contract_registry_covers_release_artifacts() -> None:
    assert set(ARTIFACT_CONTRACTS) == {
        "candidates.json",
        "generated_candidates.json",
        "generation_trace.json",
        "developability.json",
        "experimental_results.json",
        "experimental_evidence.json",
        "active_learning_batch.json",
        "review_queue.json",
        "codex_backbone.json",
        "integration_sync.json",
        "report.md",
        "trace.json",
        "project_export.zip",
    }
    assert all(
        contract.schema_version == "1.0" and contract.artifact_contract_version == "1.0"
        for contract in ARTIFACT_CONTRACTS.values()
    )


def test_valid_v1_candidates_artifact_passes_contract(tmp_path: Path) -> None:
    path = tmp_path / "candidates.json"
    _write_json(
        path,
        {
            "artifact_type": "candidates",
            "schema_version": "1.0",
            "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
            "success": True,
            "disease": {"canonical_name": "Synthetic disease"},
            "targets": [],
            "candidates": [],
            "summary": {"candidate_count": 0},
        },
    )

    result = validate_artifact_file(path)

    assert result.valid is True
    assert result.migrated is False
    assert result.errors == []


def test_legacy_v09_artifact_is_migrated_when_allowed(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    _write_json(path, {"success": True, "traces": [], "artifacts": {}})

    result = validate_artifact_file(path, migrate=True)

    assert result.valid is True
    assert result.migrated is True
    payload = json.loads(path.read_text())
    assert payload["artifact_type"] == "trace"
    assert payload["schema_version"] == "1.0"
    assert payload["artifact_contract_version"] == "1.0"


def test_invalid_artifact_reports_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "generated_candidates.json"
    _write_json(
        path,
        {
            "artifact_type": "generated_candidates",
            "schema_version": "1.0",
            "artifact_contract_version": "1.0",
            "success": True,
        },
    )

    result = validate_artifact_file(path)

    assert result.valid is False
    assert "missing required field: generated_count" in result.errors


def test_project_export_zip_contract_validates_project_export_member(tmp_path: Path) -> None:
    path = tmp_path / "project_export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "project_export.json",
            json.dumps(
                {
                    "artifact_type": "project_export",
                    "schema_version": "1.0",
                    "artifact_contract_version": "1.0",
                    "exported_at": "2026-01-01T00:00:00+00:00",
                    "project_id": "project-1",
                    "project": {},
                    "artifact_manifest": [],
                }
            ),
        )

    result = validate_artifact_file(path)

    assert result.valid is True


def test_validate_artifacts_cli_migrates_legacy_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "synthetic-disease"
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "candidates.json",
        {
            "success": True,
            "disease": {"canonical_name": "Synthetic disease"},
            "targets": [],
            "candidates": [],
            "summary": {},
        },
    )
    (run_dir / "report.md").write_text("# Synthetic report\n\nInternal research use only.\n")

    result = CliRunner().invoke(
        app,
        ["validate", "artifacts", str(run_dir), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert payload["artifact_count"] == 2
    assert payload["migrated_count"] == 1
    migrated = json.loads((run_dir / "candidates.json").read_text())
    assert migrated["artifact_contract_version"] == "1.0"


def test_validate_artifacts_cli_fails_invalid_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "synthetic-disease"
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "experimental_results.json",
        {
            "artifact_type": "experimental_results",
            "schema_version": "1.0",
            "artifact_contract_version": "1.0",
        },
    )

    result = CliRunner().invoke(app, ["validate", "artifacts", str(run_dir), "--json"])

    assert result.exit_code == 1
    assert "missing required field" in result.stdout


def test_artifact_directory_ignores_unknown_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not a release artifact")

    report = validate_artifact_directory(tmp_path)

    assert report.valid is True
    assert report.artifact_count == 0


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
