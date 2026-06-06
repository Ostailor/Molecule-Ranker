from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker import __version__
from molecule_ranker.cli import app
from molecule_ranker.v3_readiness import (
    AutonomyValidationSuite,
    build_v3_demo_project,
    run_v3_readiness_suite,
)


def test_autonomy_validation_suite_produces_v3_readiness_report() -> None:
    report = run_v3_readiness_suite()

    assert report.version == __version__ == "2.9.0"
    assert report.status == "pass"
    assert report.release_candidate.status == "pass"
    assert report.final_dashboard["title"] == "Final V3 Readiness Dashboard"
    assert report.final_dashboard["report_type"] == (
        "software_autonomy_validation_not_clinical_validation"
    )
    assert {scenario.scenario_id for scenario in report.scenario_results} == {
        "small_molecule_full_loop",
        "biologics_full_loop",
        "dry_run_integration_loop",
    }
    assert all(certification.status == "pass" for certification in report.result_certifications)
    assert all(test.status == "pass" for test in report.autonomy_boundary_tests)
    assert report.agent_reliability_scorecard.external_write_escape_rate == 0
    assert report.agent_reliability_scorecard.generated_overclaim_rate == 0


def test_v3_demo_project_does_not_fabricate_scientific_assets() -> None:
    project = build_v3_demo_project()

    assert project.contains_scientific_evidence is False
    assert project.contains_generated_molecules is False
    assert project.contains_generated_antibody_sequences is False
    assert project.contains_external_approvals is False
    assert "No fabricated molecules" in " ".join(project.limitations)


def test_v3_readiness_cli_writes_artifacts(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["v3", "readiness", "--output-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["version"] == "2.9.0"
    assert (tmp_path / "v3_readiness_report.json").exists()
    assert (tmp_path / "v3_readiness_dashboard.json").exists()
    assert (tmp_path / "v3_release_candidate.json").exists()
    assert (tmp_path / "v3_residual_risk_register.json").exists()


def test_validate_v3_readiness_cli_runs() -> None:
    result = CliRunner().invoke(app, ["validate", "v3-readiness", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["release_candidate"]["status"] == "pass"


def test_autonomy_validation_suite_certifies_no_write_escape() -> None:
    report = AutonomyValidationSuite().run()

    dry_run = next(
        scenario
        for scenario in report.scenario_results
        if scenario.scenario_id == "dry_run_integration_loop"
    )
    assert dry_run.planned_external_writes > 0
    assert dry_run.external_writes_performed == 0
    assert all(cert.no_external_write_escape for cert in report.result_certifications)
