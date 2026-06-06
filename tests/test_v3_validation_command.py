from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app

runner = CliRunner()


def test_validate_v3_passes_in_mocked_mode(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "validate",
            "v3",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["mode"] == "mocked"
    assert payload["hard_failures"] == []
    assert payload["checks_total"] == 14
    assert payload["checks_passed"] == 14
    assert (tmp_path / "v3_validation_report.json").exists()
    assert (tmp_path / "v3_validation_report.md").exists()
    assert (tmp_path / "mocked_discover" / "v3_result_bundle.json").exists()
    assert (tmp_path / "mocked_discover" / "v3_result_certification.json").exists()
    assert (tmp_path / "mocked_discover" / "e2e_lineage.json").exists()


def test_validate_v3_red_team_failure_fails_command(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from molecule_ranker.v3 import validation as v3_validation

    original = v3_validation.run_red_team_forbidden_output_check

    def failing_red_team_check() -> v3_validation.V3ValidationCheck:
        check = original()
        return check.model_copy(
            update={
                "passed": False,
                "findings": ["medical/lab/synthesis/dosing content appears"],
                "hard_failures": ["medical/lab/synthesis/dosing content appears"],
            }
        )

    monkeypatch.setattr(
        v3_validation,
        "run_red_team_forbidden_output_check",
        failing_red_team_check,
    )

    result = runner.invoke(
        app,
        [
            "validate",
            "v3",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "fail"
    assert "medical/lab/synthesis/dosing content appears" in payload["hard_failures"]
