from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.structure import run_structure_validation


def test_golden_structure_workflow_passes(tmp_path: Path) -> None:
    report = run_structure_validation(output_dir=tmp_path)

    assert report.status == "pass"
    assert "mock target with structures" in report.required_steps
    assert "consensus rescoring completed" in report.required_steps
    assert "structure guardrails verified" in report.required_steps
    assert (tmp_path / "structures.json").exists()
    assert (tmp_path / "structure_report.md").exists()
    assert (tmp_path / "structure_guardrail_audit.json").exists()
    assert (tmp_path / "structure_validation_report.json").exists()
    assert not report.guardrail_audit.findings

    structures = json.loads((tmp_path / "structures.json").read_text())["structures"]
    predicted = [item for item in structures if item["structure_type"] == "predicted"]
    assert predicted
    assert all(
        item["quality_metrics"]["relative_confidence"]
        == "lower_than_suitable_experimental_structure"
        for item in predicted
    )


def test_validate_structure_cli_writes_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "structure", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "structure"
    assert payload["status"] == "pass"
    assert (output_dir / "structure_guardrail_audit.json").exists()
    assert (output_dir / "structure_validation_report.md").exists()


def test_structure_overclaim_fixture_fails(tmp_path: Path) -> None:
    report = run_structure_validation(output_dir=tmp_path, fixture="overclaim")

    assert report.status == "fail"
    check_ids = _check_ids(report)
    assert "no_binding_claim_from_docking" in check_ids
    assert "generated_molecule_not_validated_by_pose" in check_ids


def test_fake_docking_score_fixture_fails(tmp_path: Path) -> None:
    report = run_structure_validation(output_dir=tmp_path, fixture="fake_docking_score")

    assert report.status == "fail"
    assert "no_fake_docking_scores" in _check_ids(report)


def test_fake_binding_site_source_fixture_fails(tmp_path: Path) -> None:
    report = run_structure_validation(output_dir=tmp_path, fixture="fake_binding_site_source")

    assert report.status == "fail"
    assert "binding_site_requires_provenance" in _check_ids(report)


def _check_ids(report: object) -> set[str]:
    return {finding.check_id for finding in report.guardrail_audit.findings}  # type: ignore[attr-defined]
