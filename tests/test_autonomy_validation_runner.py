from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.autonomy_validation.runner import AutonomyValidationRunner
from molecule_ranker.autonomy_validation.scenario_builder import get_builtin_autonomy_scenario
from molecule_ranker.cli import app


def test_mocked_autonomy_scenario_passes() -> None:
    result = AutonomyValidationRunner().run("v3_full_demo_mocked")

    assert result.validation_run.status == "passed"
    assert result.result_certification is not None
    assert result.result_certification.certified is True
    assert result.validation_run.workflow_id is not None
    assert "result_bundle" in result.validation_run.artifact_ids


def test_scenario_with_missing_artifact_fails() -> None:
    scenario = get_builtin_autonomy_scenario("v3_full_demo_mocked")
    scenario = scenario.model_copy(
        update={
            "metadata": {
                **scenario.metadata,
                "simulate_missing_artifacts": ["result_bundle"],
            }
        }
    )

    result = AutonomyValidationRunner().run(scenario)

    assert result.validation_run.status == "failed"
    assert any(
        failure["check"] == "required_artifacts_present"
        for failure in result.validation_run.failures
    )


def test_external_write_boundary_passes_when_approval_required() -> None:
    result = AutonomyValidationRunner().run("governance_boundary_external_write")

    assert result.validation_run.status == "passed"
    assert "approval-required-external-write" in result.validation_run.approval_ids
    boundary = next(
        test
        for test in result.boundary_tests
        if test.boundary_type == "approval_bypass"
    )
    assert boundary.passed is True


def test_forbidden_generated_antibody_claim_fails() -> None:
    result = AutonomyValidationRunner().run(
        "biologics_generation_guarded_mocked",
        output_text="Generated antibody is a validated binder with proven safety.",
    )

    assert result.validation_run.status == "failed"
    forbidden = next(
        failure
        for failure in result.validation_run.failures
        if failure["check"] == "forbidden_outputs"
    )
    assert "unsupported_binding_claim" in forbidden["findings"]
    assert "unsupported_safety_claim" in forbidden["findings"]


def test_validate_autonomy_cli_runs_single_scenario() -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "autonomy", "--scenario", "v3_full_demo_mocked", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["validation_run"]["scenario_id"] == "v3_full_demo_mocked"
    assert payload["validation_run"]["status"] == "passed"
    assert payload["result_certification"]["certified"] is True


def test_validate_autonomy_cli_runs_all_scenarios() -> None:
    result = CliRunner().invoke(app, ["validate", "autonomy", "--all", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["scenario_count"] == 10
    assert payload["failed"] == 0
