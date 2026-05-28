from __future__ import annotations

import json
from pathlib import Path

from conftest import (
    assert_common_release_invariants,
    load_json,
    load_project_export,
    run_validation_workflow,
)
from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_hosted_platform_release_validation(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "hosted_platform_workflow")

    assert_common_release_invariants(result)

    auth_session = load_json(result.artifact_dir / "auth_session.json")
    export_manifest = load_json(result.artifact_dir / "project_export_manifest.json")
    zip_payload = load_project_export(result.artifact_dir / "project_export.zip")
    dashboard = (result.artifact_dir / "dashboard_snapshot.html").read_text().lower()

    assert auth_session["token_stored"] is False
    assert export_manifest["secrets_included"] is False
    assert zip_payload["artifact_type"] == "project_export"
    assert zip_payload["secrets_included"] is False
    assert "internal research use only" in dashboard


def test_validate_release_cli_runs_deterministic_suite(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "release", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["workflow_count"] == 7
    assert payload["live_validation"] is False
    assert payload["external_services"] == "mocked"
    assert payload["codex_provider"] == "NullCodexProvider"
    assert payload["contract_artifact_count"] >= 7
