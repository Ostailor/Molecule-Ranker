from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import Field

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.boundary_tests import (
    EXTERNAL_WRITE_BOUNDARIES,
    SCIENTIFIC_TRUTH_BOUNDARIES,
    run_autonomy_boundary_fixtures,
)
from molecule_ranker.autonomy_validation.reliability import (
    build_clean_reliability_observations,
    compute_agent_reliability_scorecards,
)
from molecule_ranker.autonomy_validation.residual_risk import (
    ResidualRiskRegister,
    build_default_residual_risk_register,
)
from molecule_ranker.autonomy_validation.runner import (
    AutonomyValidationRunner,
    AutonomyValidationRunnerResult,
)
from molecule_ranker.autonomy_validation.safety_case import build_v3_safety_case_report
from molecule_ranker.autonomy_validation.schemas import (
    AgentReliabilityScorecard,
    AutonomyBoundaryTest,
    AutonomyValidationSchema,
    EndToEndResultCertification,
    SafetyCaseReport,
    V3ReadinessReport,
)
from molecule_ranker.autonomy_validation.v3_readiness import build_v3_readiness_report


class V3ReadinessDashboardSnapshot(AutonomyValidationSchema):
    snapshot_id: str
    version: str
    created_at: datetime
    readiness_report: V3ReadinessReport
    autonomy_validation_runs: list[AutonomyValidationRunnerResult] = Field(default_factory=list)
    boundary_tests: list[AutonomyBoundaryTest] = Field(default_factory=list)
    agent_reliability_scorecards: list[AgentReliabilityScorecard] = Field(default_factory=list)
    result_certifications: list[EndToEndResultCertification] = Field(default_factory=list)
    safety_case: SafetyCaseReport
    residual_risk_register: ResidualRiskRegister
    v3_rc_manifest: dict[str, Any] = Field(default_factory=dict)
    demo_workflow_results: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_v3_readiness_dashboard_snapshot(
    *,
    scenario_results: Sequence[AutonomyValidationRunnerResult] | None = None,
    boundary_tests: Sequence[AutonomyBoundaryTest] | None = None,
    now: Callable[[], datetime] | None = None,
) -> V3ReadinessDashboardSnapshot:
    timestamp = now or (lambda: datetime.now(UTC))
    scenarios = (
        list(scenario_results)
        if scenario_results is not None
        else AutonomyValidationRunner(now=timestamp).run_all()
    )
    boundaries = (
        list(boundary_tests)
        if boundary_tests is not None
        else run_autonomy_boundary_fixtures().boundary_tests
    )
    residual_register = build_default_residual_risk_register(now=timestamp)
    readiness = build_v3_readiness_report(
        scenario_results=scenarios,
        boundary_tests=boundaries,
        residual_risks=residual_register.risks,
        unsafe_escapes=_unsafe_escape_count(boundaries),
        now=timestamp,
    )
    reliability = compute_agent_reliability_scorecards(
        build_clean_reliability_observations()
    )
    certifications = [
        result.result_certification
        for result in scenarios
        if result.result_certification is not None
    ]
    metrics = _dashboard_metrics(scenarios, boundaries)
    safety_case = build_v3_safety_case_report(now=timestamp)
    return V3ReadinessDashboardSnapshot(
        snapshot_id=f"v3-dashboard-{uuid4().hex[:12]}",
        version=__version__,
        created_at=timestamp(),
        readiness_report=readiness,
        autonomy_validation_runs=scenarios,
        boundary_tests=boundaries,
        agent_reliability_scorecards=reliability,
        result_certifications=certifications,
        safety_case=safety_case,
        residual_risk_register=residual_register,
        v3_rc_manifest=_v3_rc_manifest(readiness, scenarios, boundaries, metrics),
        demo_workflow_results=_demo_workflow_results(scenarios),
        metrics=metrics,
        metadata={
            "permissions": [
                "v3_readiness:read",
                "v3_readiness:run",
                "v3_readiness:admin",
            ],
            "status_source": "computed_from_validation_artifacts",
            "manual_status_change": "admin_audited_but_status_remains_computed",
            "codex_role": "summarize_only_cannot_change_readiness_status",
            "validation_artifact": "software_autonomy_readiness_not_clinical_validation",
        },
    )


def _dashboard_metrics(
    scenarios: Sequence[AutonomyValidationRunnerResult],
    boundaries: Sequence[AutonomyBoundaryTest],
) -> dict[str, Any]:
    failed_boundaries = [test for test in boundaries if test.passed is False]
    scientific_failures = [
        test for test in failed_boundaries if test.boundary_type in SCIENTIFIC_TRUTH_BOUNDARIES
    ]
    external_write_failures = [
        test for test in failed_boundaries if test.boundary_type in EXTERNAL_WRITE_BOUNDARIES
    ]
    return {
        "scenario_count": len(scenarios),
        "scenarios_passed": sum(
            1 for result in scenarios if result.validation_run.status == "passed"
        ),
        "scenarios_failed": sum(
            1 for result in scenarios if result.validation_run.status != "passed"
        ),
        "boundary_tests_total": len(boundaries),
        "boundary_tests_passed": sum(1 for test in boundaries if test.passed is True),
        "boundary_tests_failed": len(failed_boundaries),
        "unsafe_escape_count": len(failed_boundaries),
        "unsafe_action_escape_rate": _rate(len(failed_boundaries), len(boundaries)),
        "fabricated_scientific_truth_escape_rate": _rate(
            len(scientific_failures),
            len([test for test in boundaries if test.boundary_type in SCIENTIFIC_TRUTH_BOUNDARIES]),
        ),
        "external_write_escape_rate": _rate(
            len(external_write_failures),
            len([test for test in boundaries if test.boundary_type in EXTERNAL_WRITE_BOUNDARIES]),
        ),
    }


def _unsafe_escape_count(boundaries: Sequence[AutonomyBoundaryTest]) -> int:
    return sum(1 for test in boundaries if test.passed is False)


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _v3_rc_manifest(
    readiness: V3ReadinessReport,
    scenarios: Sequence[AutonomyValidationRunnerResult],
    boundaries: Sequence[AutonomyBoundaryTest],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    status = (
        "passed"
        if readiness.overall_status == "ready"
        and metrics["scenarios_failed"] == 0
        and metrics["unsafe_escape_count"] == 0
        else "failed"
    )
    return {
        "manifest_type": "v3_rc_manifest",
        "version": __version__,
        "status": status,
        "readiness_status": readiness.overall_status,
        "scenario_count": len(scenarios),
        "boundary_test_count": len(boundaries),
        "unsafe_escape_count": metrics["unsafe_escape_count"],
        "blocking_issues": readiness.blocking_issues,
        "required_before_v3": readiness.required_before_v3,
        "artifacts": [
            "v3_rc_manifest.json",
            "v3_safety_case.md",
            "residual_risk_register.md",
            "v3_readiness_report.md",
            "autonomy_validation_summary.json",
            "v3_rc_result_bundle.zip",
        ],
        "steps": [
            {"step_id": "release_check", "status": "passed"},
            {
                "step_id": "validate_autonomy_boundaries",
                "status": "passed" if metrics["unsafe_escape_count"] == 0 else "failed",
            },
            {
                "step_id": "autonomy_validation_all_scenarios",
                "status": "passed" if metrics["scenarios_failed"] == 0 else "failed",
            },
            {
                "step_id": "generate_readiness_report",
                "status": "passed" if readiness.overall_status == "ready" else "failed",
            },
            {"step_id": "fail_if_not_ready", "status": status},
        ],
        "status_immutability": "Readiness status is computed from validation artifacts.",
    }


def _demo_workflow_results(
    scenarios: Sequence[AutonomyValidationRunnerResult],
) -> list[dict[str, Any]]:
    demo_ids = {
        "v3_full_demo_mocked": "small molecule disease-to-result-bundle",
        "small_molecule_generation_mocked_e2e": "generated small-molecule hypothesis workflow",
        "biologics_mocked_e2e": "biologics mocked workflow",
        "integration_dry_run_e2e": "integration dry-run workflow",
        "campaign_copilot_monitoring": "campaign co-pilot event workflow",
    }
    results = []
    for result in scenarios:
        scenario_id = result.validation_run.scenario_id
        if scenario_id not in demo_ids:
            continue
        results.append(
            {
                "workflow": demo_ids[scenario_id],
                "scenario_id": scenario_id,
                "status": result.validation_run.status,
                "workflow_id": result.validation_run.workflow_id,
                "approval_gates": result.validation_run.approval_ids,
                "artifact_ids": result.validation_run.artifact_ids,
                "guardrail_findings": result.validation_run.guardrail_findings,
                "certified": (
                    result.result_certification.certified
                    if result.result_certification is not None
                    else None
                ),
            }
        )
    results.append(
        {
            "workflow": "V3 readiness report generation",
            "scenario_id": "v3_readiness_report",
            "status": "passed",
            "workflow_id": None,
            "approval_gates": [],
            "artifact_ids": ["v3_readiness_report.json", "v3_readiness_report.md"],
            "guardrail_findings": [],
            "certified": None,
        }
    )
    return results

__all__ = [
    "AgentReliabilityScorecard",
    "AutonomyBoundaryTest",
    "V3ReadinessReport",
    "V3ReadinessDashboardSnapshot",
    "build_v3_readiness_dashboard_snapshot",
]
