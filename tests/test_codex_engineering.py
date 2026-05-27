from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.codex_engineering import (
    CodexEngineeringRunner,
    build_engineering_task,
    build_test_loop_task,
)
from molecule_ranker.codex_engineering.prompts import ENGINEERING_GUARDRAILS


def test_engineering_prompts_include_guardrails(tmp_path: Path) -> None:
    task = build_engineering_task(
        task_type="implementation_planning",
        goal="Plan a small CLI refactor.",
        working_directory=tmp_path,
    )

    prompt = task.prompt
    assert "codebase engineering work only" in prompt
    assert "Do not fabricate biomedical data" in prompt
    assert "Do not edit files unless explicit apply mode is enabled" in prompt
    assert "Do not include secrets" in prompt
    assert all(guardrail in prompt for guardrail in ENGINEERING_GUARDRAILS)


def test_engineering_dry_run_does_not_edit_files(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n")
    original = source.read_text()
    output_path = tmp_path / "plan.json"

    result = CliRunner().invoke(
        app,
        [
            "codex",
            "engineering-plan",
            "--goal",
            "Plan changing VALUE to 2.",
            "--cwd",
            str(tmp_path),
            "--output",
            str(output_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert source.read_text() == original
    saved = json.loads(output_path.read_text())
    assert saved["status"] == "succeeded"
    assert saved["output_json"]["dry_run"] is True


def test_engineering_forbidden_commands_rejected(tmp_path: Path) -> None:
    task = build_engineering_task(
        task_type="implementation_planning",
        goal="Run git push and rm -rf the old files.",
        working_directory=tmp_path,
    )

    result = CodexEngineeringRunner(working_directory=tmp_path).run(task)

    assert result.status == "guardrail_failed"
    assert any("git push" in warning for warning in result.guardrail_warnings)
    assert any("rm -rf" in warning for warning in result.guardrail_warnings)


def test_engineering_test_loop_prompt_built_from_test_output(tmp_path: Path) -> None:
    test_output = tmp_path / "test_output.txt"
    test_output.write_text("FAILED tests/test_cli.py::test_example - AssertionError: boom\n")

    task = build_test_loop_task(test_output, working_directory=tmp_path)

    assert task.task_type == "engineering_test_loop"
    assert str(test_output.resolve()) in task.input_artifact_paths
    assert "AssertionError: boom" in task.prompt
    assert "Analyze the supplied test output" in task.prompt
