from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_repair_cli_help_works() -> None:
    runner = CliRunner()

    for args in (
        ["repair", "--help"],
        ["repair", "evaluate", "--help"],
        ["repair", "diagnose", "--help"],
        ["repair", "plan", "--help"],
        ["repair", "execute", "--help"],
        ["repair", "regression", "--help"],
        ["repair", "memory", "--help"],
        ["repair", "report", "--help"],
        ["repair", "eval", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_repair_cli_diagnose_plan_execute_regression_report(tmp_path: Path) -> None:
    runner = CliRunner()
    tool_result = tmp_path / "tool_result.json"
    diagnosis_path = tmp_path / "failure_diagnosis.json"
    plan_path = tmp_path / "repair_plan.json"
    execution_path = tmp_path / "repair_execution.json"
    regression_path = tmp_path / "regression_checks.json"
    report_path = tmp_path / "repair_report.md"
    tool_result.write_text(
        json.dumps(
            {
                "result_id": "tool-result-1",
                "tool_name": "json_writer",
                "status": "failed",
                "error_summary": "JSON parse failed for Codex output.",
            }
        ),
        encoding="utf-8",
    )

    diagnose = runner.invoke(
        app,
        [
            "repair",
            "diagnose",
            "--tool-result",
            str(tool_result),
            "--output",
            str(diagnosis_path),
        ],
    )
    assert diagnose.exit_code == 0, diagnose.output
    assert json.loads(diagnosis_path.read_text())["failure_category"] == "parse_error"

    plan = runner.invoke(
        app,
        [
            "repair",
            "plan",
            "--diagnosis",
            str(diagnosis_path),
            "--mode",
            "safe_only",
            "--output",
            str(plan_path),
        ],
    )
    assert plan.exit_code == 0, plan.output
    plan_payload = json.loads(plan_path.read_text())
    assert plan_payload["validated"] is True
    assert plan_payload["actions"]

    execute = runner.invoke(
        app,
        [
            "repair",
            "execute",
            "--repair-plan",
            str(plan_path),
            "--dry-run",
            "--output",
            str(execution_path),
        ],
    )
    assert execute.exit_code == 0, execute.output
    assert json.loads(execution_path.read_text())["status"] == "queued"

    regression = runner.invoke(
        app,
        [
            "repair",
            "regression",
            "--repair-execution",
            str(execution_path),
            "--output",
            str(regression_path),
        ],
    )
    assert regression.exit_code == 0, regression.output
    regression_payload = json.loads(regression_path.read_text())
    assert regression_payload
    assert all(check["passed"] for check in regression_payload)

    report = runner.invoke(
        app,
        [
            "repair",
            "report",
            "--repair-execution",
            str(execution_path),
            "--output",
            str(report_path),
        ],
    )
    assert report.exit_code == 0, report.output
    markdown = report_path.read_text()
    assert "## Failure Summary" in markdown
    assert "## Regression Checks" in markdown


def test_repair_cli_evaluate_artifact(tmp_path: Path) -> None:
    runner = CliRunner()
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(
        json.dumps(
            {
                "artifact_id": "artifact-1",
                "schema_version": "v1",
                "schema_valid": True,
                "provenance": {"source": "fixture"},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["repair", "evaluate", "--artifact", str(artifact_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["evaluated_object_type"] == "artifact"
    assert payload["passed"] is True
