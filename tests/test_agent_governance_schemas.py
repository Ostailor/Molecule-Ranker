from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from molecule_ranker.agent_governance import (
    AgentAutonomyBudget,
    AgentCapabilityGrant,
    AgentCertification,
    AgentGovernancePolicy,
    AgentGovernanceReport,
    AgentIncident,
    AgentPolicyViolation,
    AgentRiskProfile,
    AgentRunControl,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_agent_governance_policy_schema_accepts_valid_payload() -> None:
    policy = AgentGovernancePolicy(
        policy_id="policy-1",
        org_id="org-1",
        project_id="project-1",
        policy_name="Runtime agent governance",
        policy_version="2.6.0",
        applies_to_roles=["ranking-agent"],
        applies_to_agents=["agent-1"],
        max_autonomy_level="execute_with_approval",
        allowed_tool_categories=["ranking", "review"],
        denied_tool_categories=["external_write"],
        allowed_side_effect_levels=["none", "artifact_write"],
        approval_required_actions=["run_campaign_replan"],
        blocked_actions=["approve_own_action"],
        budget_policy_id="budget-policy-1",
        guardrail_profile="strict_scientific",
        incident_policy_id="incident-policy-1",
        enabled=True,
        created_at=NOW,
        updated_at=NOW,
        metadata={"owner": "platform"},
    )

    assert policy.max_autonomy_level == "execute_with_approval"
    assert policy.metadata["owner"] == "platform"


def test_agent_governance_schemas_reject_naive_timestamps() -> None:
    payload = _policy_payload()
    payload["created_at"] = datetime(2026, 6, 5, 12)

    with pytest.raises(ValidationError, match="timezone-aware"):
        AgentGovernancePolicy.model_validate(payload)


def test_agent_capability_grant_schema_enforces_literals() -> None:
    grant = AgentCapabilityGrant(
        grant_id="grant-1",
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        granted_by="admin-1",
        granted_at=NOW,
        expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        status="active",
        metadata={},
    )

    assert grant.agent_type == "runtime_agent"
    assert grant.scope_type == "project"

    invalid = grant.model_dump()
    invalid["status"] = "approved"
    with pytest.raises(ValidationError):
        AgentCapabilityGrant(**invalid)


def test_agent_autonomy_budget_schema_bounds_limits_and_usage_defaults() -> None:
    budget = AgentAutonomyBudget(
        budget_id="budget-1",
        org_id="org-1",
        project_id=None,
        campaign_id="campaign-1",
        agent_id="agent-1",
        period="campaign_lifetime",
        max_tool_calls=100,
        max_codex_tasks=10,
        max_runtime_minutes=60.0,
        max_artifact_writes=20,
        max_db_writes=5,
        max_external_reads=10,
        max_external_writes=0,
        max_generation_jobs=0,
        max_docking_jobs=0,
        max_model_training_jobs=0,
        max_campaign_replans=2,
        max_cost_units=50.0,
        current_usage={"tool_calls": 4},
        reset_at=None,
        enabled=True,
        metadata={},
    )

    assert budget.period == "campaign_lifetime"
    assert budget.current_usage == {"tool_calls": 4}

    invalid = budget.model_dump()
    invalid["max_tool_calls"] = -1
    with pytest.raises(ValidationError):
        AgentAutonomyBudget(**invalid)


def test_risk_profile_and_certification_bound_rates_confidence_and_score() -> None:
    risk = AgentRiskProfile(
        risk_profile_id="risk-1",
        agent_id="agent-1",
        risk_level="high",
        risk_factors=["guardrail failures"],
        recent_guardrail_failures=2,
        recent_policy_violations=1,
        recent_failed_repairs=0,
        recent_human_overrides=3,
        unsafe_action_attempts=1,
        external_write_attempts=1,
        approval_rejection_rate=0.25,
        confidence=0.8,
        computed_at=NOW,
        metadata={},
    )
    certification = AgentCertification(
        certification_id="cert-1",
        agent_id="agent-1",
        certification_type="guardrail",
        certified_autonomy_level="execute_safe_tools",
        evaluation_artifact_ids=["eval-1"],
        passed=True,
        score=0.91,
        certified_by="admin-1",
        certified_at=NOW,
        expires_at=None,
        limitations=["artifact writes only"],
        metadata={},
    )

    assert risk.approval_rejection_rate == 0.25
    assert risk.confidence == 0.8
    assert certification.score == 0.91

    invalid_risk = risk.model_dump()
    invalid_risk["confidence"] = 1.01
    with pytest.raises(ValidationError):
        AgentRiskProfile(**invalid_risk)

    invalid_certification = certification.model_dump()
    invalid_certification["score"] = -0.1
    with pytest.raises(ValidationError):
        AgentCertification(**invalid_certification)


def test_run_control_incident_violation_and_report_schemas() -> None:
    run_control = AgentRunControl(
        control_id="control-1",
        org_id="org-1",
        project_id="project-1",
        agent_id="agent-1",
        control_type="kill_switch",
        reason="Incident response.",
        applied_by="admin-1",
        applied_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        active=True,
        metadata={},
    )
    incident = AgentIncident(
        incident_id="incident-1",
        org_id="org-1",
        project_id="project-1",
        agent_id="agent-1",
        session_id="session-1",
        severity="critical",
        incident_type="approval_bypass_attempt",
        summary="Agent attempted an approval bypass.",
        artifact_ids=["artifact-1"],
        tool_usage_ids=["tool-usage-1"],
        session_ids=["session-1"],
        status="investigating",
        opened_at=NOW,
        resolved_at=None,
        assigned_to="admin-1",
        metadata={},
    )
    violation = AgentPolicyViolation(
        violation_id="violation-1",
        policy_id="policy-1",
        agent_id="agent-1",
        session_id="session-1",
        violation_type="approval_bypass_attempt",
        blocked=True,
        summary="Blocked approval bypass attempt.",
        detected_at=NOW,
        artifact_ids=["artifact-1"],
        metadata={},
    )
    report = AgentGovernanceReport(
        report_id="report-1",
        org_id="org-1",
        project_id="project-1",
        period_start=NOW - timedelta(days=7),
        period_end=NOW,
        agent_count=4,
        active_agent_count=3,
        disabled_agent_count=1,
        total_tool_calls=100,
        total_codex_tasks=20,
        guardrail_failures=2,
        policy_violations=1,
        approval_requests=5,
        approval_rejections=1,
        incidents_opened=1,
        incidents_resolved=0,
        budget_violations=1,
        top_risks=["approval bypass"],
        recommendations=["keep kill switch active"],
        metadata={},
    )

    assert run_control.control_type == "kill_switch"
    assert incident.status == "investigating"
    assert violation.blocked is True
    assert report.disabled_agent_count == 1

    invalid_report = report.model_dump()
    invalid_report["agent_count"] = -1
    with pytest.raises(ValidationError):
        AgentGovernanceReport(**invalid_report)


def _policy_payload() -> dict[str, object]:
    return {
        "policy_id": "policy-1",
        "org_id": "org-1",
        "project_id": "project-1",
        "policy_name": "Runtime agent governance",
        "policy_version": "2.6.0",
        "applies_to_roles": ["ranking-agent"],
        "applies_to_agents": ["agent-1"],
        "max_autonomy_level": "execute_with_approval",
        "allowed_tool_categories": ["ranking"],
        "denied_tool_categories": [],
        "allowed_side_effect_levels": ["none"],
        "approval_required_actions": ["external_write"],
        "blocked_actions": ["approve_own_action"],
        "budget_policy_id": None,
        "guardrail_profile": "strict_scientific",
        "incident_policy_id": None,
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "metadata": {},
    }
