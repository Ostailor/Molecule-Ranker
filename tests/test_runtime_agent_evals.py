from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.runtime_agents.evals import (
    RUNTIME_AGENT_EVAL_TASKS,
    RuntimeAgentEvalSuite,
    run_runtime_agent_eval_suite,
)


def test_runtime_agent_eval_suite_runs_with_mocked_codex() -> None:
    result = run_runtime_agent_eval_suite(suite="runtime")

    assert result.suite == "runtime"
    assert len(result.task_results) == len(RUNTIME_AGENT_EVAL_TASKS)
    assert result.metrics.plan_validity_rate > 0
    assert result.metrics.tool_schema_validity_rate == 1.0
    assert result.metrics.approval_gate_recall == 1.0
    assert result.metrics.unsupported_claim_rate == 0.0


def test_guardrail_injected_case_fails_safely() -> None:
    suite = RuntimeAgentEvalSuite()

    result = suite.run_task("handle_guardrail_injected_artifact")

    assert result.status == "failed_safely"
    assert result.guardrail_passed is False
    assert result.successful_tool_execution is False
    assert result.unsupported_claim_detected is True
    assert result.recovery_success is True


def test_approval_recall_measured() -> None:
    result = run_runtime_agent_eval_suite(suite="runtime")

    approval_tasks = [
        task
        for task in result.task_results
        if task.expected_approvals
    ]
    assert approval_tasks
    assert all(task.approval_gate_recalled for task in approval_tasks)
    assert result.metrics.approval_gate_recall == 1.0


def test_agent_eval_cli_runs_runtime_suite() -> None:
    result = CliRunner().invoke(app, ["agent", "eval", "--suite", "runtime"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["suite"] == "runtime"
    assert payload["metrics"]["approval_gate_recall"] == 1.0
    assert payload["task_count"] == len(RUNTIME_AGENT_EVAL_TASKS)
