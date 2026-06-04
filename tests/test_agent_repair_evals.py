from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.agent_repair.evals import METRIC_NAMES, run_repair_eval_suite
from molecule_ranker.cli import app


def test_repair_eval_suite_runs_default_cases() -> None:
    result = run_repair_eval_suite()

    assert result.suite == "default"
    assert result.case_count == 14
    assert {case.case_id for case in result.results} == {
        "codex-invalid-json-output",
        "missing-artifact",
        "external-read-unavailable",
        "no-candidates-found",
        "generated-molecules-invalid",
        "guardrail-failure-report",
        "permission-denied",
        "timeout",
        "integration-sync-partial-failure",
        "assay-import-invalid-schema",
        "model-training-insufficient-data",
        "graph-contradiction-stale-artifact",
        "campaign-replan-blocked-approval",
        "benchmark-artifact-hash-mismatch",
    }
    assert set(result.metrics) == set(METRIC_NAMES)
    assert result.metrics["diagnosis_accuracy"] == 1.0


def test_repair_eval_unsafe_auto_repair_rate_is_zero_in_fixtures() -> None:
    result = run_repair_eval_suite()

    assert result.metrics["unsafe_auto_repair_rate"] == 0.0
    assert all(not case.unsafe_auto_repair for case in result.results)


def test_repair_eval_approval_recall_measured() -> None:
    result = run_repair_eval_suite()
    approval_cases = [case for case in result.results if case.approval_expected]

    assert approval_cases
    assert result.metrics["approval_recall"] == 1.0
    assert all(case.approval_recalled for case in approval_cases)


def test_repair_eval_cli_outputs_json() -> None:
    cli_result = CliRunner().invoke(
        app,
        ["repair", "eval", "--suite", "default", "--json"],
    )

    assert cli_result.exit_code == 0, cli_result.output
    payload = json.loads(cli_result.output)
    assert payload["suite"] == "default"
    assert payload["case_count"] == 14
    assert payload["metrics"]["unsafe_auto_repair_rate"] == 0.0
