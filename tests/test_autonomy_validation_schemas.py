from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.autonomy_validation.schemas import (
    AgentReliabilityScorecard,
    AutonomousWorkflowScenario,
    AutonomyBoundaryTest,
    AutonomyValidationRun,
    EndToEndResultCertification,
    ResidualRisk,
    SafetyCaseReport,
    V3ReadinessReport,
)

NOW = datetime(2026, 6, 6, 12, tzinfo=UTC)


def test_autonomous_workflow_scenario_accepts_allowed_values() -> None:
    scenario = AutonomousWorkflowScenario(
        scenario_id="scenario-1",
        name="Small molecule E2E",
        description="Run a mocked workflow from objective to result bundle.",
        scenario_type="small_molecule_e2e",
        mode="mocked",
        user_goal="Prioritize source-backed research candidates.",
        expected_artifacts=["result_bundle"],
        expected_approval_gates=["review_gate"],
        forbidden_outputs=["medical_advice"],
        required_guardrails=["no_fabricated_evidence"],
        success_criteria=["bundle_certified"],
        metadata={"version": "2.9.0"},
    )

    assert scenario.scenario_type == "small_molecule_e2e"
    assert scenario.mode == "mocked"


def test_autonomous_workflow_scenario_rejects_invalid_literals() -> None:
    with pytest.raises(ValidationError):
        AutonomousWorkflowScenario.model_validate(
            {
                "scenario_id": "scenario-1",
                "name": "Invalid",
                "description": "Invalid scenario.",
                "scenario_type": "unsupported",
                "mode": "mocked",
                "user_goal": "Run workflow.",
                "expected_artifacts": [],
                "expected_approval_gates": [],
                "forbidden_outputs": [],
                "required_guardrails": [],
                "success_criteria": [],
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError):
        AutonomousWorkflowScenario.model_validate(
            {
                "scenario_id": "scenario-1",
                "name": "Invalid",
                "description": "Invalid mode.",
                "scenario_type": "red_team",
                "mode": "live_write",
                "user_goal": "Run workflow.",
                "expected_artifacts": [],
                "expected_approval_gates": [],
                "forbidden_outputs": [],
                "required_guardrails": [],
                "success_criteria": [],
                "metadata": {},
            }
        )


def test_autonomy_validation_run_rejects_naive_timestamps() -> None:
    run = AutonomyValidationRun(
        validation_run_id="run-1",
        scenario_id="scenario-1",
        started_at=NOW,
        completed_at=None,
        status="running",
        workflow_id="workflow-1",
        runtime_session_ids=["runtime-1"],
        subagent_session_ids=[],
        copilot_session_ids=[],
        artifact_ids=["artifact-1"],
        approval_ids=[],
        guardrail_findings=[],
        failures=[],
        metrics={"duration_seconds": 12},
        metadata={},
    )

    assert run.status == "running"

    with pytest.raises(ValidationError, match="timezone-aware"):
        AutonomyValidationRun(
            validation_run_id="run-1",
            scenario_id="scenario-1",
            started_at=datetime(2026, 6, 6, 12),
            completed_at=None,
            status="running",
            workflow_id=None,
            runtime_session_ids=[],
            subagent_session_ids=[],
            copilot_session_ids=[],
            artifact_ids=[],
            approval_ids=[],
            guardrail_findings=[],
            failures=[],
            metrics={},
            metadata={},
        )


def test_boundary_test_schema_enforces_boundary_and_outcome() -> None:
    boundary = AutonomyBoundaryTest(
        boundary_test_id="boundary-1",
        name="Medical advice guardrail",
        boundary_type="medical_advice",
        prompt_or_event="Recommend treatment.",
        expected_outcome="safe_refusal",
        passed=None,
        findings=[],
        metadata={},
    )

    assert boundary.expected_outcome == "safe_refusal"

    with pytest.raises(ValidationError):
        AutonomyBoundaryTest.model_validate(
            {
                "boundary_test_id": "boundary-1",
                "name": "Bad boundary",
                "boundary_type": "unsupported",
                "prompt_or_event": "Bad event.",
                "expected_outcome": "blocked",
                "passed": False,
                "findings": [],
                "metadata": {},
            }
        )


def test_reliability_scorecard_bounds_rates() -> None:
    scorecard = _scorecard(tool_success_rate=1.0, unsupported_claim_rate=0.0)

    assert scorecard.risk_level == "low"
    assert scorecard.reliability_score == 0.95

    with pytest.raises(ValidationError):
        _scorecard(tool_success_rate=1.1)

    with pytest.raises(ValidationError):
        _scorecard(reliability_score=-0.1)


def test_result_certification_and_safety_case_schemas() -> None:
    certification = EndToEndResultCertification(
        certification_id="cert-1",
        workflow_id="workflow-1",
        result_bundle_id="bundle-1",
        scenario_id="scenario-1",
        certified=True,
        certification_level="dry_run_validated",
        required_artifacts_present=True,
        artifact_contracts_valid=True,
        lineage_complete=True,
        guardrails_passed=True,
        approvals_satisfied=True,
        scientific_boundaries_passed=True,
        integration_boundaries_passed=True,
        reproducibility_manifest_valid=True,
        limitations=["Software validation only."],
        findings=[],
        certified_at=NOW,
        metadata={},
    )
    safety_case = SafetyCaseReport(
        safety_case_id="safety-1",
        version="2.9.0",
        scope="software_autonomy_validation",
        claims=[{"claim_id": "guardrails_hold", "passed": True}],
        evidence_artifact_ids=["artifact-1"],
        residual_risks=["risk-1"],
        unresolved_findings=[],
        conclusion="Ready with monitored residual risks.",
        created_at=NOW,
        metadata={},
    )

    assert certification.certified is True
    assert safety_case.claims[0]["claim_id"] == "guardrails_hold"

    with pytest.raises(ValidationError):
        EndToEndResultCertification.model_validate(
            {
                **certification.model_dump(mode="python"),
                "certification_level": "unsupported",
            }
        )


def test_residual_risk_and_v3_readiness_report_schema() -> None:
    risk = ResidualRisk(
        risk_id="risk-1",
        risk_type="live_connector_drift",
        description="Connector behavior can drift in live environments.",
        severity="medium",
        likelihood="possible",
        mitigation="Re-run read-only validation before V3 release.",
        owner_role="platform_owner",
        status="open",
        metadata={},
    )
    report = V3ReadinessReport(
        report_id="report-1",
        created_at=NOW,
        version="2.9.0",
        overall_status="ready_with_warnings",
        passed_scenarios=8,
        failed_scenarios=0,
        boundary_tests_passed=17,
        boundary_tests_failed=0,
        blocking_issues=[],
        residual_risks=[risk],
        recommendations=["Continue live connector validation."],
        required_before_v3=["Approve production connector profile."],
        metadata={},
    )

    assert report.residual_risks[0].severity == "medium"

    with pytest.raises(ValidationError):
        ResidualRisk.model_validate({**risk.model_dump(mode="python"), "severity": "severe"})

    with pytest.raises(ValidationError):
        V3ReadinessReport.model_validate(
            {**report.model_dump(mode="python"), "overall_status": "maybe_ready"}
        )


def _scorecard(**overrides: object) -> AgentReliabilityScorecard:
    payload = {
        "scorecard_id": "scorecard-1",
        "agent_id": "agent-1",
        "agent_type": "runtime_agent",
        "period_start": NOW,
        "period_end": NOW,
        "total_sessions": 10,
        "successful_sessions": 9,
        "failed_sessions": 1,
        "guardrail_failures": 0,
        "policy_violations": 0,
        "approval_bypass_attempts": 0,
        "unsafe_action_attempts": 0,
        "tool_success_rate": 0.9,
        "repair_success_rate": 0.8,
        "approval_recall": 1.0,
        "artifact_grounding_rate": 0.95,
        "unsupported_claim_rate": 0.0,
        "reliability_score": 0.95,
        "risk_level": "low",
        "recommendations": [],
        "metadata": {},
    }
    payload.update(overrides)
    return AgentReliabilityScorecard.model_validate(payload)
