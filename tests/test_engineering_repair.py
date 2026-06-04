from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.engineering_repair import (
    EngineeringRepairExecutor,
    diagnose_engineering_failures,
    generate_regression_check_plan,
    plan_engineering_repair,
)
from molecule_ranker.engineering_repair.schemas import (
    EngineeringRepairAction,
    EngineeringRepairPlan,
)


def test_failing_test_output_creates_repair_plan(tmp_path: Path) -> None:
    test_output = tmp_path / "test_output.txt"
    test_output.write_text(
        "\n".join(
            [
                "FAILED tests/test_cli.py::test_example - AssertionError: boom",
                "OPENAI_API_KEY=sk-testsecretvalue123456789",
            ]
        )
    )

    report = diagnose_engineering_failures(test_output)
    plan = plan_engineering_repair(report)

    assert report.failures[0].category == "test_failure"
    assert "sk-testsecretvalue" not in report.metadata["raw_excerpt"]
    assert report.redaction_warnings
    assert plan.codex_profile == "engineering"
    assert ["pytest", "-q", "tests/test_cli.py::test_example"] in plan.regression_commands
    assert any(action.metadata.get("codex_task") for action in plan.actions)


def test_engineering_run_repair_dry_run_does_not_change_files(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n")
    plan = EngineeringRepairPlan(
        failure_report_id="failure-1",
        summary="Dry-run regression command.",
        actions=[
            EngineeringRepairAction(
                action_type="run_regression_command",
                summary="Preview pytest command.",
                command=["pytest", "-q", "tests/test_missing.py"],
            )
        ],
        regression_commands=[["pytest", "-q", "tests/test_missing.py"]],
    )

    report = EngineeringRepairExecutor(cwd=tmp_path).run_repair(plan)

    assert report.status == "dry_run"
    assert report.command_results[0].status == "dry_run"
    assert source.read_text() == "VALUE = 1\n"


def test_engineering_forbidden_command_rejected() -> None:
    plan = EngineeringRepairPlan(
        failure_report_id="failure-1",
        summary="Forbidden command.",
        actions=[
            EngineeringRepairAction(
                action_type="run_regression_command",
                summary="Forbidden remote write.",
                command=["git", "push"],
            )
        ],
        regression_commands=[["git", "push"]],
    )

    report = EngineeringRepairExecutor().run_repair(plan)

    assert report.status == "rejected"
    assert report.command_results[0].status == "rejected"
    assert "git push" in (report.command_results[0].rejection_reason or "")


def test_engineering_regression_commands_generated(tmp_path: Path) -> None:
    test_output = tmp_path / "test_output.txt"
    failure_report = tmp_path / "failure.json"
    test_output.write_text("FAILED tests/test_docs.py::test_docs - AssertionError: docs\n")
    report = diagnose_engineering_failures(test_output)
    failure_report.write_text(json.dumps(report.model_dump(mode="json")))

    generated = generate_regression_check_plan(report)
    cli_result = CliRunner().invoke(
        app,
        [
            "engineering",
            "regression-check",
            "--failure-report",
            str(failure_report),
            "--json",
        ],
    )

    assert generated["status"] == "generated"
    assert ["pytest", "-q", "tests/test_docs.py::test_docs"] in generated["commands"]
    assert cli_result.exit_code == 0, cli_result.stdout
    assert "tests/test_docs.py::test_docs" in cli_result.stdout


def test_engineering_cli_diagnose_and_repair_plan(tmp_path: Path) -> None:
    test_output = tmp_path / "test_output.txt"
    failure_report = tmp_path / "failure.json"
    repair_plan = tmp_path / "repair_plan.json"
    test_output.write_text("FAILED tests/test_cli.py::test_example - AssertionError: boom\n")

    diagnose = CliRunner().invoke(
        app,
        [
            "engineering",
            "diagnose",
            "--test-output",
            str(test_output),
            "--output",
            str(failure_report),
            "--json",
        ],
    )
    plan = CliRunner().invoke(
        app,
        [
            "engineering",
            "repair-plan",
            "--failure-report",
            str(failure_report),
            "--output",
            str(repair_plan),
            "--json",
        ],
    )

    assert diagnose.exit_code == 0, diagnose.stdout
    assert plan.exit_code == 0, plan.stdout
    payload = json.loads(repair_plan.read_text())
    assert payload["codex_profile"] == "engineering"
    assert payload["dry_run_by_default"] is True


def test_engineering_cli_run_repair_dry_run(tmp_path: Path) -> None:
    plan_path = tmp_path / "repair_plan.json"
    output_path = tmp_path / "execution.json"
    plan = EngineeringRepairPlan(
        failure_report_id="failure-1",
        summary="CLI dry-run plan.",
        actions=[
            EngineeringRepairAction(
                action_type="run_regression_command",
                summary="Preview pytest command.",
                command=["pytest", "-q", "tests/test_missing.py"],
            )
        ],
        regression_commands=[["pytest", "-q", "tests/test_missing.py"]],
    )
    plan_path.write_text(json.dumps(plan.model_dump(mode="json")))

    result = CliRunner().invoke(
        app,
        [
            "engineering",
            "run-repair",
            "--plan",
            str(plan_path),
            "--dry-run",
            "--output",
            str(output_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(output_path.read_text())
    assert payload["status"] == "dry_run"
    assert payload["command_results"][0]["status"] == "dry_run"
