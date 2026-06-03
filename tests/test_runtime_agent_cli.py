from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_agent_cli_help_works() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0, result.output
    assert "start" in result.output
    assert "execute" in result.output
    assert "approve" in result.output


def test_agent_dry_run_plan_works(tmp_path: Path) -> None:
    output_dir = tmp_path / "agent"

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "start",
            "--goal",
            "Rank Alzheimer disease and create a review workspace",
            "--autonomy",
            "execute_safe_tools",
            "--dry-run",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    plan_path = output_dir / "runtime_action_plan.json"
    assert payload["status"] == "dry_run"
    assert plan_path.exists()
    assert json.loads(plan_path.read_text(encoding="utf-8"))["steps"][0]["tool_name"] == (
        "run_ranking"
    )


def test_agent_execute_safe_tools_works(tmp_path: Path) -> None:
    output_dir = tmp_path / "agent"
    plan_result = CliRunner().invoke(
        app,
        [
            "agent",
            "plan",
            "--goal",
            "Rank Alzheimer disease",
            "--autonomy",
            "execute_safe_tools",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output

    execute = CliRunner().invoke(
        app,
        [
            "agent",
            "execute",
            "--plan",
            str(output_dir / "runtime_action_plan.json"),
            "--autonomy",
            "execute_safe_tools",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert execute.exit_code == 0, execute.output
    payload = json.loads(execute.output)
    assert payload["status"] == "succeeded"
    results = json.loads((output_dir / "runtime_tool_results.json").read_text(encoding="utf-8"))
    assert results[0]["status"] == "succeeded"
    assert results[0]["artifact_ids"]


def test_agent_execute_requires_approval_for_risky_step(tmp_path: Path) -> None:
    output_dir = tmp_path / "agent"
    plan_path = output_dir / "runtime_action_plan.json"
    output_dir.mkdir()
    plan_path.write_text(json.dumps(_external_write_plan()), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "execute",
            "--plan",
            str(plan_path),
            "--autonomy",
            "execute_with_approval",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "approval_required"
    assert (output_dir / "runtime_approval_request.json").exists()


def test_agent_cancellation_works(tmp_path: Path) -> None:
    output_dir = tmp_path / "agent"
    start = CliRunner().invoke(
        app,
        [
            "agent",
            "start",
            "--goal",
            "Rank Alzheimer disease",
            "--dry-run",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert start.exit_code == 0, start.output

    cancel = CliRunner().invoke(
        app,
        ["agent", "cancel", "--output-dir", str(output_dir)],
    )

    assert cancel.exit_code == 0, cancel.output
    payload = json.loads(cancel.output)
    session = json.loads((output_dir / "runtime_session.json").read_text(encoding="utf-8"))
    assert payload["status"] == "cancelled"
    assert session["status"] == "cancelled"


def _external_write_plan() -> dict[str, object]:
    return {
        "plan_id": "plan-risky",
        "session_id": "session-risky",
        "user_goal": "Run external sync.",
        "plan_summary": "Run external integration write.",
        "steps": [
            {
                "step_id": "step-risky",
                "plan_id": "plan-risky",
                "step_index": 0,
                "action_type": "run_sync_write_enabled",
                "tool_name": "run_sync_write_enabled",
                "tool_args": {},
                "requires_approval": True,
                "approval_reason": "External write requires approval.",
                "expected_outputs": [],
                "status": "pending",
                "result_id": None,
                "warnings": [],
                "metadata": {},
            }
        ],
        "required_approvals": ["external_write"],
        "expected_artifacts": [],
        "risk_level": "high",
        "guardrail_warnings": [],
        "created_by": "deterministic_template",
        "validated": True,
        "validation_errors": [],
        "metadata": {
            "tool_specs": {
                "run_sync_write_enabled": {
                    "required_permissions": ["integration:write"],
                    "side_effect_level": "external_write",
                    "policy_tags": ["external_write", "approval_required"],
                }
            }
        },
    }
