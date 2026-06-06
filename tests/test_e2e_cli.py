from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app

runner = CliRunner()


def test_e2e_cli_help() -> None:
    result = runner.invoke(app, ["e2e", "--help"])

    assert result.exit_code == 0, result.output
    assert "run" in result.output
    assert "validate" in result.output


def test_e2e_demo_succeeds(tmp_path: Path) -> None:
    result = runner.invoke(app, ["e2e", "demo", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "succeeded" in result.output
    assert (tmp_path / "index.json").exists()


def test_e2e_dry_run_workflow_no_writes(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "e2e",
            "run",
            "--workflow",
            "full_discovery_loop",
            "--disease",
            "Parkinson disease",
            "--mode",
            "dry_run",
            "--enable-integrations",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    workflow_id = _workflow_id(tmp_path)
    run_payload = json.loads((tmp_path / workflow_id / "result.json").read_text())
    assert run_payload["external_writes_performed"] == 0
    assert run_payload["planned_external_writes"] == 1


def test_e2e_resume_after_optional_failure(tmp_path: Path) -> None:
    first = runner.invoke(
        app,
        [
            "e2e",
            "run",
            "--workflow",
            "full_discovery_loop",
            "--disease",
            "Parkinson disease",
            "--mode",
            "read_only_live",
            "--output-dir",
            str(tmp_path),
            "--partial-on-live-data-unavailable",
            "--unavailable-data",
            "generation",
        ],
    )
    assert first.exit_code == 0, first.output
    workflow_id = _workflow_id(tmp_path)

    resumed = runner.invoke(
        app,
        [
            "e2e",
            "resume",
            "--workflow-id",
            workflow_id,
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert resumed.exit_code == 0, resumed.output
    assert "succeeded" in resumed.output


def test_e2e_validate_detects_missing_artifact(tmp_path: Path) -> None:
    result = runner.invoke(app, ["e2e", "demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    workflow_id = _workflow_id(tmp_path)
    run_dir = tmp_path / workflow_id
    bundle_path = run_dir / "bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["key_artifact_ids"] = bundle["key_artifact_ids"][:-1]
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")

    validation = runner.invoke(app, ["e2e", "validate", "--run-dir", str(run_dir)])

    assert validation.exit_code == 1, validation.output
    assert "required artifacts missing" in validation.output


def _workflow_id(output_dir: Path) -> str:
    index = json.loads((output_dir / "index.json").read_text())
    return index["workflows"][-1]["workflow_id"]
