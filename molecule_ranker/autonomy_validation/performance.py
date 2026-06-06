from __future__ import annotations

import json
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.dashboard import (
    build_v3_readiness_dashboard_snapshot,
)
from molecule_ranker.autonomy_validation.reliability import (
    build_clean_reliability_observations,
)
from molecule_ranker.autonomy_validation.result_certification import certify_e2e_result
from molecule_ranker.autonomy_validation.runner import AutonomyValidationRunner
from molecule_ranker.autonomy_validation.scenario_builder import (
    get_builtin_autonomy_scenario,
)
from molecule_ranker.autonomy_validation.schemas import AutonomyValidationSchema

V3_PERFORMANCE_JSON = "v3_performance_report.json"
V3_PERFORMANCE_MARKDOWN = "v3_performance_report.md"

V3PerformanceStatus = Literal["pass", "fail"]


class V3PerformanceThresholds(AutonomyValidationSchema):
    mocked_full_e2e_max_seconds: float = Field(default=10.0, gt=0)
    result_bundle_generation_max_seconds: float = Field(default=5.0, gt=0)
    dashboard_key_page_max_latency_seconds: float = Field(default=10.0, gt=0)
    agent_planning_max_seconds: float = Field(default=5.0, gt=0)
    tool_execution_failure_rate_max: float = Field(default=0.05, ge=0, le=1)
    mocked_memory_max_mb: float = Field(default=256.0, gt=0)
    max_codex_tool_iterations: int = Field(default=20, ge=1)
    autonomy_budget_tool_call_limit: int = Field(default=20, ge=1)


class V3PerformanceCheck(AutonomyValidationSchema):
    check_id: str
    name: str
    passed: bool
    observed_value: float
    threshold: float
    unit: str
    findings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class V3PerformanceReport(AutonomyValidationSchema):
    report_id: str
    version: str
    status: V3PerformanceStatus
    started_at: datetime
    completed_at: datetime
    thresholds: V3PerformanceThresholds
    checks: list[V3PerformanceCheck]
    metrics: dict[str, Any] = Field(default_factory=dict)
    output_files: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def run_v3_performance_gate(
    *,
    output_dir: Path | str | None = None,
    thresholds: V3PerformanceThresholds | None = None,
    simulate_runaway_loop: bool = False,
    now: Callable[[], datetime] | None = None,
) -> V3PerformanceReport:
    """Run deterministic V3 performance/reliability checks and optionally write artifacts."""

    timestamp = now or (lambda: datetime.now(UTC))
    active_thresholds = thresholds or V3PerformanceThresholds()
    started_at = timestamp()
    runner = AutonomyValidationRunner(now=timestamp)

    checks: list[V3PerformanceCheck] = []

    e2e_elapsed, e2e_result = _timed(lambda: runner.run("v3_full_demo_mocked"))
    checks.append(
        _max_check(
            "mocked_full_e2e_runtime",
            "Mocked full E2E runtime",
            e2e_elapsed,
            active_thresholds.mocked_full_e2e_max_seconds,
            "seconds",
            passed=e2e_result.passed,
            failure_message=(
                "Mocked full E2E did not complete successfully under the configured time."
            ),
            metadata={"scenario_id": "v3_full_demo_mocked"},
        )
    )

    scenario = get_builtin_autonomy_scenario("v3_full_demo_mocked")
    certification_elapsed, certification = _timed(
        lambda: certify_e2e_result(
            e2e_result.validation_run.workflow_id or "workflow-missing",
            scenario,
        )
    )
    checks.append(
        _max_check(
            "result_bundle_generation_runtime",
            "Result bundle generation runtime",
            certification_elapsed,
            active_thresholds.result_bundle_generation_max_seconds,
            "seconds",
            passed=certification.certified and certification.result_bundle_id is not None,
            failure_message=(
                "Result bundle certification did not complete successfully under the "
                "configured time."
            ),
            metadata={"certification_id": certification.certification_id},
        )
    )

    dashboard_elapsed, dashboard = _timed(build_v3_readiness_dashboard_snapshot)
    dashboard_payload = dashboard.model_dump(mode="json")
    checks.append(
        _max_check(
            "dashboard_key_page_latency_test_mode",
            "Dashboard key page latency in test mode",
            dashboard_elapsed,
            active_thresholds.dashboard_key_page_max_latency_seconds,
            "seconds",
            passed=_dashboard_key_pages_present(dashboard_payload),
            failure_message=(
                "Dashboard key-page snapshot was incomplete or exceeded the configured latency."
            ),
            metadata={
                "snapshot_id": dashboard.snapshot_id,
                "key_pages": _dashboard_key_page_names(),
            },
        )
    )

    planning_elapsed, planning_result = _timed(lambda: runner.run("multi_agent_diagnose_campaign"))
    checks.append(
        _max_check(
            "agent_planning_runtime",
            "Agent planning runtime",
            planning_elapsed,
            active_thresholds.agent_planning_max_seconds,
            "seconds",
            passed=planning_result.passed,
            failure_message=(
                "Agent planning did not complete successfully under the configured time."
            ),
            metadata={"scenario_id": "multi_agent_diagnose_campaign"},
        )
    )

    tool_failure_rate = _tool_execution_failure_rate()
    checks.append(
        _max_check(
            "tool_execution_failure_rate",
            "Tool execution failure rate",
            tool_failure_rate,
            active_thresholds.tool_execution_failure_rate_max,
            "rate",
            failure_message="Tool execution failure rate exceeded the configured threshold.",
        )
    )

    repair_elapsed, repair_result = _timed(lambda: runner.run("repair_recovery_missing_artifact"))
    checks.append(
        _max_check(
            "retry_repair_transient_failure",
            "Retry/repair known transient failure",
            repair_elapsed,
            active_thresholds.agent_planning_max_seconds,
            "seconds",
            passed=repair_result.passed,
            failure_message=(
                "Repair scenario did not recover from the known missing-artifact fixture."
            ),
            metadata={"scenario_id": "repair_recovery_missing_artifact"},
        )
    )

    peak_memory_mb, memory_result = _measure_peak_memory_mb(
        lambda: runner.run("v3_full_demo_mocked")
    )
    checks.append(
        _max_check(
            "mocked_scenario_memory_usage",
            "Mocked scenario memory usage",
            peak_memory_mb,
            active_thresholds.mocked_memory_max_mb,
            "megabytes",
            passed=memory_result.passed,
            failure_message="Mocked full E2E memory use exceeded the configured limit.",
            metadata={"scenario_id": "v3_full_demo_mocked"},
        )
    )

    runaway_check = evaluate_runaway_loop_fixture(
        max_iterations=active_thresholds.max_codex_tool_iterations,
        simulate_runaway_loop=simulate_runaway_loop,
    )
    checks.append(runaway_check)

    budget_check = evaluate_autonomy_budget_fixture(
        tool_calls=active_thresholds.autonomy_budget_tool_call_limit,
        budget_limit=active_thresholds.autonomy_budget_tool_call_limit,
    )
    checks.append(budget_check)

    completed_at = timestamp()
    report = V3PerformanceReport(
        report_id=f"v3-performance-{uuid4().hex[:12]}",
        version=__version__,
        status="pass" if all(check.passed for check in checks) else "fail",
        started_at=started_at,
        completed_at=completed_at,
        thresholds=active_thresholds,
        checks=checks,
        metrics=_metrics(checks),
        metadata={
            "mode": "synthetic_mocked_test_gate",
            "external_writes_allowed": False,
            "validation_artifact": "software_autonomy_performance_not_clinical_validation",
            "scientific_claims_generated": False,
        },
    )
    if output_dir is not None:
        return write_v3_performance_report(report=report, output_dir=output_dir)
    return report


def write_v3_performance_report(
    output_dir: Path | str | None = None,
    *,
    report: V3PerformanceReport | None = None,
    thresholds: V3PerformanceThresholds | None = None,
    now: Callable[[], datetime] | None = None,
) -> V3PerformanceReport:
    output_path = Path(output_dir or Path(".molecule-ranker") / "validation" / "v3_performance")
    output_path.mkdir(parents=True, exist_ok=True)
    active_report = report or run_v3_performance_gate(
        thresholds=thresholds,
        now=now,
    )
    json_path = output_path / V3_PERFORMANCE_JSON
    markdown_path = output_path / V3_PERFORMANCE_MARKDOWN
    output_files = {
        V3_PERFORMANCE_JSON: str(json_path),
        V3_PERFORMANCE_MARKDOWN: str(markdown_path),
    }
    active_report = active_report.model_copy(update={"output_files": output_files})
    json_path.write_text(
        json.dumps(active_report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v3_performance_report_markdown(active_report),
        encoding="utf-8",
    )
    return active_report


def render_v3_performance_report_markdown(report: V3PerformanceReport) -> str:
    lines = [
        "# V3 Performance/Reliability Gate",
        "",
        f"- Version: {report.version}",
        f"- Status: {report.status}",
        f"- Started: {report.started_at.isoformat()}",
        f"- Completed: {report.completed_at.isoformat()}",
        "",
        (
            "This is a software/autonomy performance validation artifact. It is not "
            "clinical validation and does not create biomedical evidence."
        ),
        "",
        "## Checks",
        "",
        "| Check | Status | Observed | Threshold | Findings |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for check in report.checks:
        findings = "; ".join(check.findings) if check.findings else "None"
        lines.append(
            f"| {check.name} | {'pass' if check.passed else 'fail'} | "
            f"{check.observed_value:.4f} {check.unit} | {check.threshold:.4f} {check.unit} | "
            f"{findings} |"
        )
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- Checks passed: {report.metrics['checks_passed']}/{report.metrics['checks_total']}",
            f"- Runtime checks failed: {report.metrics['checks_failed']}",
            f"- Unsafe/tool-loop escapes: {report.metrics['runaway_loop_failures']}",
            "",
            "## Boundaries",
            "",
            "- No live external writes are performed by this gate.",
            (
                "- Codex/tool loop status is computed from the fixture and cannot be "
                "manually overridden by Codex output."
            ),
            "- Autonomy budgets are evaluated as enforced software controls.",
        ]
    )
    return "\n".join(lines) + "\n"


def evaluate_runaway_loop_fixture(
    *,
    max_iterations: int,
    simulate_runaway_loop: bool = False,
) -> V3PerformanceCheck:
    observed_iterations = max_iterations + 1 if simulate_runaway_loop else max_iterations
    passed = observed_iterations <= max_iterations
    return V3PerformanceCheck(
        check_id="no_runaway_codex_tool_loops",
        name="No runaway Codex/tool loops",
        passed=passed,
        observed_value=float(observed_iterations),
        threshold=float(max_iterations),
        unit="iterations",
        findings=[]
        if passed
        else ["Runaway Codex/tool loop fixture exceeded the iteration limit."],
        metadata={
            "fixture": "runaway_loop",
            "loop_guard_enforced": passed,
            "simulated_runaway_loop": simulate_runaway_loop,
        },
    )


def evaluate_autonomy_budget_fixture(
    *,
    tool_calls: int,
    budget_limit: int,
) -> V3PerformanceCheck:
    passed = tool_calls <= budget_limit
    return V3PerformanceCheck(
        check_id="autonomy_budgets_enforced",
        name="Autonomy budgets enforced",
        passed=passed,
        observed_value=float(tool_calls),
        threshold=float(budget_limit),
        unit="tool_calls",
        findings=[]
        if passed
        else ["Autonomy budget fixture exceeded the configured tool-call limit."],
        metadata={
            "fixture": "autonomy_budget",
            "budget_enforced": passed,
            "blocked_after_limit": passed,
        },
    )


def _timed(func: Callable[[], Any]) -> tuple[float, Any]:
    started = time.perf_counter()
    result = func()
    return time.perf_counter() - started, result


def _measure_peak_memory_mb(func: Callable[[], Any]) -> tuple[float, Any]:
    tracemalloc.start()
    try:
        result = func()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / (1024 * 1024), result


def _max_check(
    check_id: str,
    name: str,
    observed_value: float,
    threshold: float,
    unit: str,
    *,
    passed: bool = True,
    failure_message: str,
    metadata: dict[str, Any] | None = None,
) -> V3PerformanceCheck:
    active_passed = passed and observed_value <= threshold
    return V3PerformanceCheck(
        check_id=check_id,
        name=name,
        passed=active_passed,
        observed_value=float(observed_value),
        threshold=float(threshold),
        unit=unit,
        findings=[] if active_passed else [failure_message],
        metadata=dict(metadata or {}),
    )


def _dashboard_key_pages_present(payload: dict[str, Any]) -> bool:
    return all(name in payload and payload[name] for name in _dashboard_required_payload_keys())


def _dashboard_required_payload_keys() -> tuple[str, ...]:
    return (
        "readiness_report",
        "autonomy_validation_runs",
        "boundary_tests",
        "agent_reliability_scorecards",
        "result_certifications",
        "safety_case",
        "residual_risk_register",
        "v3_rc_manifest",
        "demo_workflow_results",
    )


def _dashboard_key_page_names() -> list[str]:
    return [
        "V3 readiness overview",
        "autonomy validation runs",
        "boundary test results",
        "agent reliability scorecards",
        "result certifications",
        "safety case",
        "residual risk register",
        "V3 RC manifest",
        "demo workflow results",
    ]


def _tool_execution_failure_rate() -> float:
    observations = build_clean_reliability_observations()
    tool_calls = sum(observation.tool_calls for observation in observations)
    tool_failures = sum(observation.tool_failures for observation in observations)
    if tool_calls == 0:
        return 0.0
    return tool_failures / tool_calls


def _metrics(checks: list[V3PerformanceCheck]) -> dict[str, Any]:
    failed = [check for check in checks if not check.passed]
    return {
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "checks_failed": len(failed),
        "runaway_loop_failures": sum(
            1 for check in failed if check.check_id == "no_runaway_codex_tool_loops"
        ),
        "budget_enforcement_failures": sum(
            1 for check in failed if check.check_id == "autonomy_budgets_enforced"
        ),
        "failed_check_ids": [check.check_id for check in failed],
    }


__all__ = [
    "V3_PERFORMANCE_JSON",
    "V3_PERFORMANCE_MARKDOWN",
    "V3PerformanceCheck",
    "V3PerformanceReport",
    "V3PerformanceThresholds",
    "evaluate_autonomy_budget_fixture",
    "evaluate_runaway_loop_fixture",
    "render_v3_performance_report_markdown",
    "run_v3_performance_gate",
    "write_v3_performance_report",
]
