from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AutonomousWorkflowScenarioType = Literal[
    "small_molecule_e2e",
    "generated_molecule_e2e",
    "biologics_e2e",
    "campaign_copilot",
    "integration_sync",
    "multi_agent_ops",
    "repair_recovery",
    "governance_boundary",
    "red_team",
    "v3_demo",
]
AutonomyWorkflowMode = Literal[
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]
AutonomyValidationRunStatus = Literal[
    "queued",
    "running",
    "passed",
    "failed",
    "partially_passed",
    "cancelled",
]
AutonomyBoundaryType = Literal[
    "evidence_fabrication",
    "assay_result_fabrication",
    "citation_fabrication",
    "molecule_fabrication",
    "antibody_sequence_fabrication",
    "external_record_fabrication",
    "external_write_without_approval",
    "approval_bypass",
    "stage_gate_bypass",
    "generated_advancement_without_review",
    "generated_molecule_advancement_without_review",
    "generated_antibody_advancement_without_review",
    "medical_advice",
    "lab_protocol",
    "synthesis_instruction",
    "expression_purification_immunization_protocol",
    "dosing_guidance",
    "dosing_patient_guidance",
    "secret_exfiltration",
    "unauthorized_tool",
    "unauthorized_artifact",
    "policy_override",
    "codex_self_certification",
    "codex_self_approval",
    "failed_qc_treated_as_evidence",
]
AutonomyBoundaryExpectedOutcome = Literal[
    "blocked",
    "approval_required",
    "safe_refusal",
    "quarantine",
    "escalation",
]
AutonomyRiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
ResultCertificationLevel = Literal[
    "mocked_validated",
    "dry_run_validated",
    "read_only_live_validated",
    "write_approved_live_validated",
    "failed",
]
ResidualRiskLikelihood = Literal[
    "rare",
    "unlikely",
    "possible",
    "likely",
    "unknown",
]
ResidualRiskStatus = Literal[
    "open",
    "mitigated",
    "accepted",
    "deferred",
]
V3ReadinessStatus = Literal[
    "ready",
    "ready_with_warnings",
    "not_ready",
]


class AutonomyValidationSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class AutonomousWorkflowScenario(AutonomyValidationSchema):
    scenario_id: str
    name: str
    description: str
    scenario_type: AutonomousWorkflowScenarioType
    mode: AutonomyWorkflowMode
    user_goal: str
    expected_artifacts: list[str] = Field(default_factory=list)
    expected_approval_gates: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)
    required_guardrails: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AutonomyValidationRun(AutonomyValidationSchema):
    validation_run_id: str
    scenario_id: str
    started_at: datetime
    completed_at: datetime | None
    status: AutonomyValidationRunStatus
    workflow_id: str | None
    runtime_session_ids: list[str] = Field(default_factory=list)
    subagent_session_ids: list[str] = Field(default_factory=list)
    copilot_session_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    guardrail_findings: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AutonomyBoundaryTest(AutonomyValidationSchema):
    boundary_test_id: str
    name: str
    boundary_type: AutonomyBoundaryType
    prompt_or_event: str
    expected_outcome: AutonomyBoundaryExpectedOutcome
    passed: bool | None
    findings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentReliabilityScorecard(AutonomyValidationSchema):
    scorecard_id: str
    agent_id: str | None
    agent_type: str
    period_start: datetime
    period_end: datetime
    total_sessions: int = Field(ge=0)
    successful_sessions: int = Field(ge=0)
    failed_sessions: int = Field(ge=0)
    guardrail_failures: int = Field(ge=0)
    policy_violations: int = Field(ge=0)
    approval_bypass_attempts: int = Field(ge=0)
    unsafe_action_attempts: int = Field(ge=0)
    tool_success_rate: float = Field(ge=0, le=1)
    repair_success_rate: float = Field(ge=0, le=1)
    approval_recall: float = Field(ge=0, le=1)
    artifact_grounding_rate: float = Field(ge=0, le=1)
    unsupported_claim_rate: float = Field(ge=0, le=1)
    reliability_score: float = Field(ge=0, le=1)
    risk_level: AutonomyRiskLevel
    recommendations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndResultCertification(AutonomyValidationSchema):
    certification_id: str
    workflow_id: str
    result_bundle_id: str | None
    scenario_id: str
    certified: bool
    certification_level: ResultCertificationLevel
    required_artifacts_present: bool
    artifact_contracts_valid: bool
    lineage_complete: bool
    guardrails_passed: bool
    approvals_satisfied: bool
    scientific_boundaries_passed: bool
    integration_boundaries_passed: bool
    reproducibility_manifest_valid: bool
    limitations: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    certified_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SafetyCaseReport(AutonomyValidationSchema):
    safety_case_id: str
    version: str
    scope: str
    claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    unresolved_findings: list[str] = Field(default_factory=list)
    conclusion: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResidualRisk(AutonomyValidationSchema):
    risk_id: str
    risk_type: str
    description: str
    severity: AutonomyRiskLevel
    likelihood: ResidualRiskLikelihood
    mitigation: str
    owner_role: str | None
    status: ResidualRiskStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class V3ReadinessReport(AutonomyValidationSchema):
    report_id: str
    created_at: datetime
    version: str
    overall_status: V3ReadinessStatus
    passed_scenarios: int = Field(ge=0)
    failed_scenarios: int = Field(ge=0)
    boundary_tests_passed: int = Field(ge=0)
    boundary_tests_failed: int = Field(ge=0)
    blocking_issues: list[str] = Field(default_factory=list)
    residual_risks: list[ResidualRisk] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    required_before_v3: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentReliabilityScorecard",
    "AutonomyBoundaryExpectedOutcome",
    "AutonomyBoundaryTest",
    "AutonomyBoundaryType",
    "AutonomyRiskLevel",
    "AutonomyValidationRun",
    "AutonomyValidationRunStatus",
    "AutonomyValidationSchema",
    "AutonomousWorkflowScenario",
    "AutonomousWorkflowScenarioType",
    "AutonomyWorkflowMode",
    "EndToEndResultCertification",
    "ResidualRisk",
    "ResidualRiskLikelihood",
    "ResidualRiskStatus",
    "ResultCertificationLevel",
    "SafetyCaseReport",
    "V3ReadinessReport",
    "V3ReadinessStatus",
]
