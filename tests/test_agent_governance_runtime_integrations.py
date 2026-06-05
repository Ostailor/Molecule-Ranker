from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.agent_governance.budgets import AgentAutonomyBudgetManager
from molecule_ranker.agent_governance.certification import AgentCertificationManager
from molecule_ranker.agent_governance.policies import (
    AgentGovernancePolicyEngine,
    default_platform_policy,
)
from molecule_ranker.agent_governance.risk import AgentRiskScorer
from molecule_ranker.agent_governance.run_control import AgentRunControlManager
from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentGovernancePolicy,
    AgentRunControl,
)
from molecule_ranker.agent_repair.executor import RepairExecutor
from molecule_ranker.agent_repair.schemas import RepairAction, RepairPlan
from molecule_ranker.copilot.action_queue import CoPilotActionQueue
from molecule_ranker.copilot.schemas import CoPilotAction
from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor
from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.subagents.coordinator import MultiAgentCoordinator, SubagentPolicyError
from molecule_ranker.tool_ecosystem.mcp_gateway import InternalMCPGateway, MCPGatewayContext

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_runtime_action_blocked_by_governance_and_updates_risk() -> None:
    registry = RuntimeToolRegistry.default()
    risk_profiles = {}
    executor = RuntimeActionExecutor(
        registry=registry,
        tool_handlers={"run_ranking": lambda _step, _spec: {"summary": "unused"}},
        governance_policy_engine=AgentGovernancePolicyEngine(
            org_policies=[_policy("org-deny-ranking", denied_tool_categories=["ranking"])]
        ),
        governance_risk_scorer=AgentRiskScorer(),
        governance_risk_profiles=risk_profiles,
    )

    result = executor.execute(
        _runtime_plan("run_ranking", registry=registry),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
    )

    assert result.status == "policy_blocked"
    assert result.results[0].status == "policy_blocked"
    assert risk_profiles["runtime-agent-1"].recent_policy_violations == 1


def test_subagent_blocked_without_certification() -> None:
    coordinator = MultiAgentCoordinator(
        certification_manager=AgentCertificationManager(certifications=[])
    )

    with pytest.raises(SubagentPolicyError, match="certification"):
        coordinator.coordinate(user_goal="rank molecules for review")


def test_copilot_blocked_by_budget() -> None:
    queue = CoPilotActionQueue(
        autonomy_budget_manager=AgentAutonomyBudgetManager(
            budgets=[
                AgentAutonomyBudget(
                    budget_id="budget-1",
                    org_id=None,
                    project_id=None,
                    campaign_id="campaign-1",
                    agent_id=None,
                    period="per_session",
                    max_tool_calls=0,
                    max_codex_tasks=None,
                    max_runtime_minutes=None,
                    max_artifact_writes=None,
                    max_db_writes=None,
                    max_external_reads=None,
                    max_external_writes=None,
                    max_generation_jobs=None,
                    max_docking_jobs=None,
                    max_model_training_jobs=None,
                    max_campaign_replans=None,
                    max_cost_units=None,
                    current_usage={},
                    reset_at=None,
                    enabled=True,
                    metadata={},
                )
            ]
        ),
        autonomy_budget_id="budget-1",
    )
    action = _copilot_action()
    queue.queue_action(action)

    results = queue.execute_eligible_safe_actions()

    assert results[0].status == "blocked_by_policy"
    assert "Budget limit" in results[0].summary


def test_repair_blocked_by_run_control() -> None:
    run_controls = AgentRunControlManager(
        controls=[
            AgentRunControl(
                control_id="pause-repair",
                org_id=None,
                project_id=None,
                agent_id="repair-executor",
                control_type="pause",
                reason="Incident response.",
                applied_by="admin-1",
                applied_at=NOW,
                expires_at=None,
                active=True,
                metadata={},
            )
        ]
    )
    executor = RepairExecutor(governance_run_control_manager=run_controls)

    execution = executor.execute(_repair_plan(), mode="execute_safe_repairs")

    assert execution.status == "guardrail_blocked"
    assert any("paused" in warning.lower() for warning in execution.warnings)


def test_mcp_tool_blocked_by_governance() -> None:
    gateway = InternalMCPGateway(
        governance_policy_engine=AgentGovernancePolicyEngine(
            org_policies=[_policy("org-deny-ranking", denied_tool_categories=["ranking"])]
        )
    )

    result = gateway.tools_call(
        "run_ranking",
        {},
        MCPGatewayContext(
            user_id="user-1",
            org_id="org-1",
            project_id="project-1",
            user_permissions={"run:create"},
            actor="codex",
        ),
    )

    assert result.status == "policy_blocked"
    assert "Tool category is denied" in (result.error_summary or "")


def test_risk_profile_updates_after_policy_violation() -> None:
    registry = RuntimeToolRegistry.default()
    risk_profiles = {}
    executor = RuntimeActionExecutor(
        registry=registry,
        governance_policy_engine=AgentGovernancePolicyEngine(
            org_policies=[_policy("org-deny-ranking", denied_tool_categories=["ranking"])]
        ),
        governance_risk_scorer=AgentRiskScorer(),
        governance_risk_profiles=risk_profiles,
    )

    executor.execute(
        _runtime_plan("run_ranking", registry=registry),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
    )

    profile = risk_profiles["runtime-agent-1"]
    assert profile.recent_policy_violations == 1
    assert profile.metadata["risk_score_visible"] is True


def _runtime_plan(tool_name: str, *, registry: RuntimeToolRegistry) -> RuntimeActionPlan:
    spec = registry.require(tool_name)
    step = RuntimeActionStep(
        step_id="step-1",
        plan_id="plan-1",
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args={},
        requires_approval=False,
        status="pending",
    )
    return RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Run governed action.",
        plan_summary="Governed runtime action.",
        steps=[step],
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                tool_name: {
                    "required_permissions": spec.required_permissions,
                    "side_effect_level": spec.side_effect_level,
                    "policy_tags": spec.policy_tags,
                }
            },
            "runtime_context": {
                "agent_id": "runtime-agent-1",
                "agent_type": "runtime_agent",
                "org_id": "org-1",
                "project_id": "project-1",
                "autonomy_level": "execute_safe_tools",
                "user_permissions": spec.required_permissions,
            },
        },
    )


def _copilot_action() -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id="copilot-action-1",
        campaign_id="campaign-1",
        trigger_id="trigger-1",
        action_type="create_review_request",
        tool_name="run_ranking",
        tool_args={},
        side_effect_level="none",
        risk_level="low",
        requires_approval=False,
        approval_reason=None,
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata={},
    )


def _repair_plan() -> RepairPlan:
    return RepairPlan(
        repair_plan_id="repair-plan-1",
        diagnosis_id="diagnosis-1",
        session_id="session-1",
        plan_summary="Repair operational workflow failure.",
        actions=[
            RepairAction(
                repair_action_id="repair-action-1",
                action_type="revalidate_artifact",
                target_object_type="workflow",
                target_object_id="workflow-1",
                tool_name=None,
                tool_args={"target_id": "workflow-1"},
                expected_effect="Revalidate deterministic artifact.",
                side_effect_level="none",
                requires_approval=False,
                approval_reason=None,
                risk_level="low",
                metadata={},
            )
        ],
        expected_artifacts=[],
        rollback_plan=[],
        requires_human_approval=False,
        scientific_guardrails=["Do not create scientific evidence."],
        validated=True,
        validation_errors=[],
        created_by="deterministic",
        created_at=NOW,
        metadata={},
    )


def _policy(policy_id: str, *, denied_tool_categories: list[str]) -> AgentGovernancePolicy:
    base = default_platform_policy().model_dump()
    base.update(
        {
            "policy_id": policy_id,
            "org_id": "org-1",
            "project_id": None,
            "policy_name": policy_id,
            "policy_version": "2.6.0",
            "denied_tool_categories": denied_tool_categories,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    return AgentGovernancePolicy.model_validate(base)
