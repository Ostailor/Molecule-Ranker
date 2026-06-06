from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.autonomy_validation.performance import (
    V3_PERFORMANCE_JSON,
    V3_PERFORMANCE_MARKDOWN,
    V3PerformanceThresholds,
    evaluate_autonomy_budget_fixture,
    run_v3_performance_gate,
)
from molecule_ranker.cli import app

NOW = datetime(2026, 6, 6, tzinfo=UTC)


def test_v3_performance_report_generated(tmp_path: Path) -> None:
    report = run_v3_performance_gate(output_dir=tmp_path, now=lambda: NOW)

    assert report.status == "pass"
    assert report.metrics["checks_failed"] == 0
    assert (tmp_path / V3_PERFORMANCE_JSON).exists()
    assert (tmp_path / V3_PERFORMANCE_MARKDOWN).exists()

    payload = json.loads((tmp_path / V3_PERFORMANCE_JSON).read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert {check["check_id"] for check in payload["checks"]} == {
        "mocked_full_e2e_runtime",
        "result_bundle_generation_runtime",
        "dashboard_key_page_latency_test_mode",
        "agent_planning_runtime",
        "tool_execution_failure_rate",
        "retry_repair_transient_failure",
        "mocked_scenario_memory_usage",
        "no_runaway_codex_tool_loops",
        "autonomy_budgets_enforced",
    }
    assert "not clinical validation" in (tmp_path / V3_PERFORMANCE_MARKDOWN).read_text(
        encoding="utf-8"
    )


def test_runaway_loop_fixture_fails(tmp_path: Path) -> None:
    report = run_v3_performance_gate(
        output_dir=tmp_path,
        simulate_runaway_loop=True,
        now=lambda: NOW,
    )

    assert report.status == "fail"
    assert "no_runaway_codex_tool_loops" in report.metrics["failed_check_ids"]
    runaway = next(
        check for check in report.checks if check.check_id == "no_runaway_codex_tool_loops"
    )
    assert runaway.passed is False
    assert runaway.observed_value > runaway.threshold


def test_budget_enforcement_fixture_passes() -> None:
    check = evaluate_autonomy_budget_fixture(tool_calls=20, budget_limit=20)

    assert check.passed is True
    assert check.metadata["budget_enforced"] is True
    assert check.observed_value == check.threshold


def test_v3_performance_cli_writes_report(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "v3-performance", "--output-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["metrics"]["checks_failed"] == 0
    assert (tmp_path / V3_PERFORMANCE_JSON).exists()


def test_v3_performance_cli_fails_when_threshold_exceeded(tmp_path: Path) -> None:
    thresholds = V3PerformanceThresholds(mocked_full_e2e_max_seconds=0.001)
    report = run_v3_performance_gate(
        output_dir=tmp_path,
        thresholds=thresholds,
        now=lambda: NOW,
    )

    assert report.status == "fail"
    assert "mocked_full_e2e_runtime" in report.metrics["failed_check_ids"]
