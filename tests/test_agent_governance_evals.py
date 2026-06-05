from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.agent_governance.evals import run_governance_eval_suite
from molecule_ranker.cli import app


def test_governance_red_team_cases_blocked() -> None:
    report = run_governance_eval_suite(suite="default")

    red_team = [result for result in report.results if result.unsafe]
    assert len(red_team) == 12
    assert all(result.blocked for result in red_team)
    assert all(result.passed for result in red_team)
    assert report.metrics.unsafe_action_escape_rate == 0
    assert report.status == "pass"


def test_governance_safe_actions_allowed() -> None:
    report = run_governance_eval_suite(suite="default")

    safe = [result for result in report.results if not result.unsafe]
    assert safe
    assert all(result.outcome == "allowed" for result in safe)
    assert report.metrics.false_positive_rate == 0


def test_governance_eval_cli_runs_default_suite() -> None:
    result = CliRunner().invoke(app, ["governance", "eval", "--suite", "default"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["suite"] == "default"
    assert payload["status"] == "pass"
    assert payload["metrics"]["unsafe_action_escape_rate"] == 0


def test_validate_agent_governance_cli_runs_suite() -> None:
    result = CliRunner().invoke(app, ["validate", "agent-governance", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["blocked_red_team_count"] == payload["red_team_case_count"]
