from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.autonomy_validation.boundary_tests import (
    build_autonomy_boundary_fixtures,
    run_autonomy_boundary_fixtures,
)
from molecule_ranker.cli import app


def test_all_boundary_fixtures_run() -> None:
    fixtures = build_autonomy_boundary_fixtures()
    result = run_autonomy_boundary_fixtures()

    assert len(fixtures) == 23
    assert len(result.boundary_tests) == len(fixtures)
    assert all(test.passed is True for test in result.boundary_tests)
    assert result.passed is True


def test_boundary_fixture_escape_rates_are_zero() -> None:
    result = run_autonomy_boundary_fixtures()

    assert result.unsafe_action_escape_rate == 0
    assert result.fabricated_scientific_truth_escape_rate == 0
    assert result.external_write_escape_rate == 0


def test_clean_safe_boundary_scenario_passes() -> None:
    result = run_autonomy_boundary_fixtures()

    assert result.clean_safe_scenario.passed is True
    assert result.clean_safe_scenario.findings == []


def test_validate_autonomy_boundaries_cli() -> None:
    result = CliRunner().invoke(app, ["validate", "autonomy-boundaries", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["total_boundary_fixtures"] == 23
    assert payload["unsafe_action_escape_rate"] == 0
