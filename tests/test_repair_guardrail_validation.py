from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.repair_guardrails import run_repair_guardrail_validation


def test_repair_guardrail_red_team_cases_all_blocked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = run_repair_guardrail_validation(tmp_path)

    assert report.status == "pass"
    assert len(report.red_team_results) == 10
    assert report.blocked_count == 10
    assert all(result.status == "pass" for result in report.red_team_results)
    assert all(result.decision_status == "blocked" for result in report.red_team_results)


def test_repair_guardrail_safe_cases_allowed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = run_repair_guardrail_validation(tmp_path)

    assert len(report.safe_results) == 6
    assert report.allowed_count == 6
    assert all(result.status == "pass" for result in report.safe_results)
    assert all(result.decision_status == "allowed" for result in report.safe_results)


def test_validate_repair_guardrails_cli(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cli_result = CliRunner().invoke(
        app,
        ["validate", "repair-guardrails", "--root", str(tmp_path), "--json"],
    )

    assert cli_result.exit_code == 0, cli_result.output
    payload = json.loads(cli_result.output)
    assert payload["status"] == "pass"
    assert payload["red_team_blocked_count"] == 10
    assert payload["safe_allowed_count"] == 6
    assert (
        tmp_path
        / ".molecule-ranker"
        / "validation"
        / "repair_guardrails"
        / "repair_guardrail_validation.json"
    ).exists()
