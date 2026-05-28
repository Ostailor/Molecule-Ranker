from __future__ import annotations

from pathlib import Path

from conftest import assert_common_release_invariants, load_json, run_validation_workflow


def test_integration_sync_release_validation_is_dry_run(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "integration_sync_workflow")

    assert_common_release_invariants(result)

    config = load_json(result.artifact_dir / "external_system_config.json")
    dry_run = load_json(result.artifact_dir / "dry_run_sync_report.json")
    integration_sync = load_json(result.artifact_dir / "integration_sync.json")
    export_manifest = load_json(result.artifact_dir / "artifact_export_manifest.json")

    assert config["mode"] == "dry_run"
    assert config["credentials"] == "not_required"
    assert dry_run["dry_run"] is True
    assert dry_run["rows_written"] == 0
    assert integration_sync["sync_job"]["dry_run"] is True
    assert integration_sync["records"][0]["write_mode"] == "dry_run"
    assert export_manifest["external_write"] is False
    assert export_manifest["secrets_included"] is False
