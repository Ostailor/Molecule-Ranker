from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from molecule_ranker.runtime_agents.governance import (
    AgentApprovalPolicy,
    AgentAutonomyBudget,
    AgentCapabilityGrant,
    AgentCertification,
    AgentChangeRequest,
    AgentGovernanceBoard,
    AgentGovernanceController,
    AgentGovernanceError,
    AgentGovernancePolicy,
    AgentIncident,
    AgentKillSwitch,
    AgentPerformanceMetrics,
    AgentRiskProfile,
    AgentRunControl,
    build_agent_governance_report,
    build_agent_governance_report_markdown,
    policy_fingerprint,
)
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec

NOW = datetime(2026, 6, 4, 12, tzinfo=UTC)


def test_active_policy_requires_human_approval_and_keeps_guardrail_floor() -> None:
    policy = _policy()

    assert policy.status == "active"
    assert policy.scientific_guardrail_floor is True
    assert policy_fingerprint(policy)

    with pytest.raises(ValidationError, match="cannot weaken scientific guardrails"):
        _policy(scientific_guardrail_floor=False)

    with pytest.raises(ValidationError, match="Codex cannot approve"):
        _policy(approved_by="codex")

    with pytest.raises(ValidationError, match="cannot modify governance policy"):
        _policy(codex_may_modify_policy=True)


def test_codex_cannot_approve_governance_change_requests() -> None:
    controller = AgentGovernanceController()
    request = AgentChangeRequest(
        change_request_id="change-1",
        agent_id="agent-ranking",
        policy_id="policy-1",
        change_type="autonomy_increase",
        requested_by="codex",
        reason="Request higher autonomy for campaign monitoring.",
        requested_at=NOW,
    )

    with pytest.raises(AgentGovernanceError, match="Codex cannot approve"):
        controller.approve_change_request(
            request,
            decided_by="codex",
            approved=True,
            rationale="Self-approved.",
            decided_at=NOW + timedelta(minutes=1),
        )

    approved = controller.approve_change_request(
        request,
        decided_by="admin-1",
        approved=True,
        rationale="Pilot sponsor approved bounded campaign monitoring.",
        decided_at=NOW + timedelta(minutes=1),
    )

    assert approved.status == "approved"
    assert approved.decided_by == "admin-1"


def test_codex_cannot_self_certify_agents() -> None:
    controller = AgentGovernanceController()
    certification = AgentCertification(
        certification_id="cert-1",
        agent_id="agent-ranking",
        policy_id="policy-1",
        certified_autonomy_level="execute_with_approval",
        certified_capabilities=["run_ranking"],
    )

    with pytest.raises(AgentGovernanceError, match="self-certify"):
        controller.certify_agent(
            certification,
            certified_by="codex",
            evidence_artifact_ids=["eval-report-1"],
            certified_at=NOW,
        )

    certified = controller.certify_agent(
        certification,
        certified_by="admin-1",
        evidence_artifact_ids=["eval-report-1"],
        certified_at=NOW,
    )

    assert certified.status == "certified"
    assert certified.certified_by == "admin-1"
    assert certified.evidence_artifact_ids == ["eval-report-1"]


def test_action_evaluation_enforces_capability_budget_run_control_and_approval() -> None:
    controller = AgentGovernanceController()
    policy = _policy()
    ranking_tool = _tool()
    grant = _grant()

    allowed = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=policy,
        grants=[grant],
        tool=ranking_tool,
        autonomy_level="execute_safe_tools",
        budget=_budget(max_tool_calls=3, consumed_tool_calls=1),
        run_control=AgentRunControl(
            control_id="control-1",
            agent_id="agent-ranking",
            status="active",
            reason="Normal operations.",
            updated_by="admin-1",
            updated_at=NOW,
        ),
        now=NOW,
    )

    assert allowed.allowed is True
    assert allowed.boundary_proof["policy_id"] == policy.policy_id
    assert allowed.boundary_proof["matching_grant_ids"] == [grant.grant_id]

    exhausted = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=policy,
        grants=[grant],
        tool=ranking_tool,
        autonomy_level="execute_safe_tools",
        budget=_budget(max_tool_calls=1, consumed_tool_calls=1),
        now=NOW,
    )

    assert exhausted.status == "blocked"
    assert {violation.code for violation in exhausted.violations} == {
        "budget_tool_calls_exceeded"
    }

    paused = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=policy,
        grants=[grant],
        tool=ranking_tool,
        autonomy_level="execute_safe_tools",
        run_control=AgentRunControl(
            control_id="control-2",
            agent_id="agent-ranking",
            status="paused",
            reason="Risk increased.",
            updated_by="admin-1",
            updated_at=NOW,
        ),
        now=NOW,
    )

    assert paused.status == "blocked"
    assert "agent_paused" in {violation.code for violation in paused.violations}

    external_write = _tool(
        tool_name="run_sync_write_enabled",
        category="integration",
        permission="integration:sync",
        side_effect_level="external_write",
        policy_tags=["external_write"],
    )
    approval_required = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=policy,
        grants=[_grant(tool_categories=["integration"], permissions=["integration:sync"])],
        tool=external_write,
        autonomy_level="execute_with_approval",
        now=NOW,
    )

    assert approval_required.status == "requires_approval"
    assert approval_required.required_approvals == ["integration_sync"]


def test_risk_drift_and_kill_switches_block_autonomous_actions() -> None:
    controller = AgentGovernanceController()
    policy = _policy(prompt_hashes={"planner": "approved"}, tool_manifest_hashes={"run": "hash"})

    drift = controller.detect_drift(
        agent_id="agent-ranking",
        policy=policy,
        observed_prompt_hashes={"planner": "changed"},
        observed_tool_manifest_hashes={"run": "changed"},
    )
    assert {violation.code for violation in drift} == {
        "prompt_drift",
        "tool_manifest_drift",
    }

    decision = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=policy,
        grants=[_grant()],
        tool=_tool(),
        autonomy_level="execute_safe_tools",
        campaign_id="campaign-1",
        risk_profile=AgentRiskProfile(
            agent_id="agent-ranking",
            risk_level="critical",
            risk_score=0.95,
            factors=["policy drift", "guardrail failures"],
            policy_drift_detected=True,
            pause_recommended=True,
            evaluated_at=NOW,
        ),
        kill_switches=[
            AgentKillSwitch(
                kill_switch_id="kill-1",
                scope="campaign",
                scope_id="campaign-1",
                active=True,
                reason="Incident response.",
                activated_by="codex",
                activated_at=NOW,
            )
        ],
        now=NOW,
    )

    assert decision.status == "blocked"
    assert {
        "policy_drift_detected",
        "risk_pause_required",
        "kill_switch_active",
    }.issubset({violation.code for violation in decision.violations})

    with pytest.raises(ValidationError, match="cannot disable governance kill switches"):
        AgentKillSwitch(
            kill_switch_id="kill-2",
            scope="agent",
            scope_id="agent-ranking",
            active=False,
            reason="Deactivate.",
            activated_by="admin-1",
            activated_at=NOW,
            deactivated_by="codex",
            deactivated_at=NOW,
        )


def test_governance_report_includes_incidents_guardrail_failures_and_boundary_proofs() -> None:
    controller = AgentGovernanceController()
    decision = controller.evaluate_action(
        agent_id="agent-ranking",
        policy=_policy(),
        grants=[_grant()],
        tool=_tool(),
        autonomy_level="execute_safe_tools",
        now=NOW,
    )
    incident = AgentIncident(
        incident_id="incident-1",
        agent_id="agent-ranking",
        severity="high",
        status="investigating",
        title="Guardrail failure spike",
        summary="Agent exceeded guardrail failure threshold.",
        guardrail_failure=True,
        detected_at=NOW,
        detected_by="codex",
    )

    report = build_agent_governance_report(
        org_id="org-1",
        audience="admin",
        generated_by="admin-1",
        policies=[_policy()],
        grants=[_grant()],
        budgets=[
            _budget(subject_type="agent", subject_id="agent-ranking"),
            _budget(
                budget_id="budget-campaign",
                subject_type="campaign",
                subject_id="campaign-1",
            ),
        ],
        certifications=[
            AgentCertification(
                certification_id="cert-1",
                agent_id="agent-ranking",
                policy_id="policy-1",
                certified_autonomy_level="execute_safe_tools",
                certified_capabilities=["run_ranking"],
                status="certified",
                evidence_artifact_ids=["eval-report-1"],
                certified_by="admin-1",
                certified_at=NOW,
            )
        ],
        run_controls=[
            AgentRunControl(
                control_id="control-1",
                agent_id="agent-ranking",
                status="active",
                reason="Normal operations.",
                updated_by="admin-1",
                updated_at=NOW,
            )
        ],
        incidents=[incident],
        violations=[],
        change_requests=[],
        kill_switches=[],
        performance_metrics=[
            AgentPerformanceMetrics(
                agent_id="agent-ranking",
                run_count=10,
                success_rate=0.9,
                reliability_score=0.88,
                guardrail_failure_rate=0.1,
                human_override_rate=0.2,
                average_runtime_minutes=3.5,
            )
        ],
        decisions=[decision],
        generated_at=NOW,
    )
    markdown = build_agent_governance_report_markdown(report)

    assert report.summary["guardrail_failure_count"] == 1
    assert report.summary["certified_agent_count"] == 1
    assert report.boundary_proofs[0]["tool_name"] == "run_ranking"
    assert "incident-1" in markdown
    assert "Guardrail failure spike" in markdown
    assert "No medical advice." in markdown
    with pytest.raises(AgentGovernanceError, match="cannot be hidden"):
        controller.hide_incident(incident, actor="codex")


def test_governance_board_and_approval_policy_models_capture_admin_controls() -> None:
    board = AgentGovernanceBoard(
        board_id="board-1",
        org_id="org-1",
        admin_user_ids=["admin-1"],
        pilot_sponsor_user_ids=["sponsor-1"],
        reviewer_user_ids=["reviewer-1"],
        quorum=1,
        created_at=NOW,
    )
    approval_policy = AgentApprovalPolicy(
        approval_policy_id="approval-policy-1",
        policy_id="policy-1",
        approval_type="policy_override",
        required_role="platform_admin",
        human_only=True,
        applies_to_autonomy_levels=["execute_with_approval", "full_auto_restricted"],
    )

    assert board.admin_user_ids == ["admin-1"]
    assert approval_policy.human_only is True
    assert approval_policy.approval_type == "policy_override"


def _policy(**overrides: object) -> AgentGovernancePolicy:
    payload = {
        "policy_id": "policy-1",
        "org_id": "org-1",
        "name": "Default governed runtime agent policy",
        "version": "2.7.0",
        "status": "active",
        "max_autonomy_level": "execute_with_approval",
        "allowed_agent_ids": ["agent-ranking"],
        "allowed_tool_categories": ["ranking", "integration"],
        "allowed_permissions": ["ranking:run", "integration:sync"],
        "approval_required_for": ["external_write", "policy_override"],
        "approved_by": "admin-1",
        "approved_at": NOW,
        "created_at": NOW,
    }
    payload.update(overrides)
    return AgentGovernancePolicy.model_validate(payload)


def _grant(
    *,
    tool_categories: list[str] | None = None,
    permissions: list[str] | None = None,
) -> AgentCapabilityGrant:
    return AgentCapabilityGrant(
        grant_id="grant-1",
        policy_id="policy-1",
        agent_id="agent-ranking",
        allowed_tool_categories=tool_categories or ["ranking"],
        allowed_permissions=permissions or ["ranking:run"],
        max_autonomy_level="execute_with_approval",
        status="active",
        approved_by="admin-1",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )


def _budget(
    *,
    budget_id: str = "budget-agent",
    subject_type: str = "agent",
    subject_id: str = "agent-ranking",
    max_tool_calls: int = 3,
    consumed_tool_calls: int = 0,
) -> AgentAutonomyBudget:
    return AgentAutonomyBudget(
        budget_id=budget_id,
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=subject_id,
        period_start=NOW,
        period_end=NOW + timedelta(days=30),
        max_tool_calls=max_tool_calls,
        max_runtime_minutes=120,
        max_cost_usd=100,
        max_external_writes=1,
        max_artifact_writes=10,
        consumed_tool_calls=consumed_tool_calls,
        approved_by="admin-1",
        approved_at=NOW,
    )


def _tool(
    *,
    tool_name: str = "run_ranking",
    category: str = "ranking",
    permission: str = "ranking:run",
    side_effect_level: str = "artifact_write",
    policy_tags: list[str] | None = None,
) -> RuntimeToolSpec:
    return RuntimeToolSpec.model_validate(
        {
            "tool_name": tool_name,
            "category": category,
            "description": "Governed deterministic test tool.",
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {"type": "object", "additionalProperties": True},
            "required_permissions": [permission],
            "policy_tags": policy_tags or [],
            "side_effect_level": side_effect_level,
            "requires_approval_by_default": False,
            "idempotent": True,
        }
    )
