from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_governance_cli_help_works() -> None:
    result = CliRunner().invoke(app, ["governance", "--help"])

    assert result.exit_code == 0, result.output
    for command in [
        "policy",
        "grant",
        "budget",
        "certify",
        "certification",
        "control",
        "incident",
        "risk",
        "report",
        "simulate",
    ]:
        assert command in result.output


def test_governance_policy_create_list_validate_and_explain() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("policies.json")

        created = runner.invoke(
            app,
            [
                "governance",
                "policy",
                "create",
                "--policy-id",
                "policy-1",
                "--policy-name",
                "Project policy",
                "--org-id",
                "org-1",
                "--denied-tool-category",
                "generation",
                "--state-path",
                str(state_path),
            ],
        )
        listed = runner.invoke(
            app,
            ["governance", "policy", "list", "--state-path", str(state_path)],
        )
        validated = runner.invoke(
            app,
            ["governance", "policy", "validate", "--state-path", str(state_path)],
        )
        explained = runner.invoke(
            app,
            [
                "governance",
                "policy",
                "explain",
                "--agent-id",
                "agent-1",
                "--action",
                "run_generation",
                "--tool-category",
                "generation",
                "--org-id",
                "org-1",
                "--state-path",
                str(state_path),
            ],
        )

        assert created.exit_code == 0, created.output
        assert json.loads(created.output)["policy_id"] == "policy-1"
        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["policy_id"] == "policy-1"
        assert validated.exit_code == 0, validated.output
        assert json.loads(validated.output)["valid"] is True
        assert explained.exit_code == 1, explained.output
        assert json.loads(explained.output)["status"] == "blocked"


def test_governance_budget_create_status_and_reset() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("budgets.json")

        created = runner.invoke(
            app,
            [
                "governance",
                "budget",
                "create",
                "--budget-id",
                "budget-1",
                "--agent-id",
                "agent-1",
                "--period",
                "daily",
                "--max-tool-calls",
                "3",
                "--state-path",
                str(state_path),
            ],
        )
        status = runner.invoke(
            app,
            [
                "governance",
                "budget",
                "status",
                "--budget-id",
                "budget-1",
                "--state-path",
                str(state_path),
            ],
        )
        reset = runner.invoke(
            app,
            [
                "governance",
                "budget",
                "reset",
                "--budget-id",
                "budget-1",
                "--state-path",
                str(state_path),
            ],
        )

        assert created.exit_code == 0, created.output
        assert json.loads(created.output)["budget_id"] == "budget-1"
        assert status.exit_code == 0, status.output
        payload = json.loads(status.output)
        assert payload["budget_id"] == "budget-1"
        assert payload["limits"]["tool_calls"] == 3.0
        assert reset.exit_code == 0, reset.output
        assert json.loads(reset.output)["current_usage"] == {}


def test_governance_risk_cli_outputs_visible_profile() -> None:
    result = CliRunner().invoke(
        app,
        [
            "governance",
            "risk",
            "--agent-id",
            "agent-1",
            "--guardrail-failures",
            "1",
            "--policy-violations",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"]["agent_id"] == "agent-1"
    assert payload["profile"]["metadata"]["risk_score_visible"] is True
