from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.models import run_model_validation


def test_golden_model_workflow_passes(tmp_path: Path) -> None:
    report = run_model_validation(output_dir=tmp_path)

    assert report.status == "pass"
    assert "synthetic assay results imported" in report.required_steps
    assert (
        "calibrated surrogate predictions integrated into oracle scoring"
        in report.required_steps
    )
    assert (tmp_path / "model_guardrail_audit.json").exists()
    assert (tmp_path / "model_validation_report.json").exists()
    assert (tmp_path / "model_predictions.json").exists()
    assert (tmp_path / "reports" / "model_prediction_report.md").exists()

    predictions = json.loads((tmp_path / "model_predictions.json").read_text())["predictions"]
    assert {prediction["candidate_origin"] for prediction in predictions} == {
        "existing",
        "generated",
    }
    assert all(prediction["metadata"]["not_evidence_item"] is True for prediction in predictions)
    assert all(prediction["metadata"]["not_assay_result"] is True for prediction in predictions)
    assert not report.guardrail_audit.findings


def test_validate_models_cli_writes_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "models", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "models"
    assert payload["status"] == "pass"
    assert (output_dir / "model_guardrail_audit.json").exists()
    assert (output_dir / "model_validation_report.md").exists()


def test_leakage_fixture_fails(tmp_path: Path) -> None:
    report = run_model_validation(output_dir=tmp_path, fixture="leakage")

    assert report.status == "fail"
    assert "no_leakage" in _check_ids(report)


def test_uncalibrated_overclaim_fails(tmp_path: Path) -> None:
    report = run_model_validation(output_dir=tmp_path, fixture="uncalibrated_overclaim")

    assert report.status == "fail"
    assert "no_uncalibrated_prediction_shown_as_calibrated" in _check_ids(report)


def test_fake_prediction_evidence_fails(tmp_path: Path) -> None:
    report = run_model_validation(output_dir=tmp_path, fixture="fake_prediction_evidence")

    assert report.status == "fail"
    check_ids = _check_ids(report)
    assert "predictions_are_not_evidence_items" in check_ids
    assert "predictions_are_not_assay_results" in check_ids


def _check_ids(report: object) -> set[str]:
    return {finding.check_id for finding in report.guardrail_audit.findings}  # type: ignore[attr-defined]
