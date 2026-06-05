from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.budgets import (
    AgentAutonomyBudgetManager,
    AgentBudgetDecision,
    BudgetImpact,
)
from molecule_ranker.agent_governance.policies import (
    AUTONOMY_ORDER,
    AgentActionRequest,
    AgentGovernancePolicyEngine,
    AgentPolicyDecision,
)
from molecule_ranker.agent_governance.risk import RISK_AUTONOMY_CAPS
from molecule_ranker.agent_governance.run_control import (
    AgentRunControlManager,
    RunControlRequest,
)
from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentGovernanceAutonomyLevel,
    AgentGovernancePolicy,
    AgentRiskProfile,
    AgentRunControl,
    AgentType,
)

SimulationStatus = Literal["allowed", "approval_required", "blocked"]

GENERATED_ADVANCEMENT_APPROVAL = "generated_molecule_human_review"


class AgentPolicySimulationRequest(BaseModel):
    agent_id: str
    agent_type: AgentType = "runtime_agent"
    role: str | None = None
    profile: str | None = None
    autonomy_level: AgentGovernanceAutonomyLevel = "suggest_only"
    tool: str
    action: str | None = None
    tool_category: str | None = None
    side_effect_level: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    budget_impact: BudgetImpact = Field(default_factory=BudgetImpact)
    budgets: list[AgentAutonomyBudget] = Field(default_factory=list)
    risk_profile: AgentRiskProfile | None = None
    proposed_policy: AgentGovernancePolicy | None = None
    active_policies: list[AgentGovernancePolicy] = Field(default_factory=list)
    run_controls: list[AgentRunControl] = Field(default_factory=list)
    human_approved_actions: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPolicySimulationResult(BaseModel):
    status: SimulationStatus
    allowed: bool
    approval_required: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    budget_impact: dict[str, Any] = Field(default_factory=dict)
    risk_impact: dict[str, Any] = Field(default_factory=dict)
    policy_trace: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPolicySimulator:
    """Preflight whether an agent action would pass governance controls."""

    def simulate(
        self,
        request: AgentPolicySimulationRequest | None = None,
        **kwargs: Any,
    ) -> AgentPolicySimulationResult:
        active_request = request or AgentPolicySimulationRequest.model_validate(kwargs)
        trace: list[dict[str, Any]] = []
        blocked_reasons: list[str] = []
        approvals: set[str] = set()
        permissions = _required_permissions(active_request)

        run_control_decision = AgentRunControlManager(
            controls=active_request.run_controls
        ).evaluate(_run_control_request(active_request))
        trace.append(
            {
                "step": "run_control",
                "status": run_control_decision.status,
                "reasons": run_control_decision.reasons,
                "active_control_ids": [
                    control.control_id for control in run_control_decision.active_controls
                ],
            }
        )
        if not run_control_decision.allowed and not run_control_decision.requires_approval:
            blocked_reasons.extend(run_control_decision.reasons)
        elif run_control_decision.requires_approval:
            approvals.add("run_control_all_actions")

        policy_decision = _policy_engine(active_request).evaluate_action(
            _policy_action_request(active_request)
        )
        _apply_policy_decision(
            policy_decision,
            blocked_reasons=blocked_reasons,
            approvals=approvals,
        )
        trace.append(
            {
                "step": "policy",
                "status": policy_decision.status,
                "reasons": policy_decision.reasons,
                "required_approvals": policy_decision.required_approval_actions,
                "violations": [
                    violation.model_dump(mode="json")
                    for violation in policy_decision.violations
                ],
                "effective_policy": policy_decision.effective_policy.model_dump(mode="json"),
            }
        )

        budget_decision = _budget_decision(active_request)
        if budget_decision is not None:
            trace.append(
                {
                    "step": "budget",
                    "status": budget_decision.status,
                    "reasons": budget_decision.reasons,
                    "exceeded_dimensions": budget_decision.exceeded_dimensions,
                    "required_approvals": budget_decision.required_approvals,
                    "budget_id": budget_decision.budget.budget_id,
                }
            )
            if budget_decision.status == "blocked":
                blocked_reasons.extend(budget_decision.reasons)
            elif budget_decision.requires_approval:
                approvals.update(budget_decision.required_approvals)
        else:
            trace.append({"step": "budget", "status": "not_configured", "reasons": []})

        risk_impact = _risk_impact(active_request)
        trace.append({"step": "risk", **risk_impact})
        if risk_impact.get("status") == "blocked":
            blocked_reasons.append(str(risk_impact["reason"]))
        elif risk_impact.get("approval_required"):
            approvals.add("risk_review")

        blocked_reasons = list(dict.fromkeys(blocked_reasons))
        required_approvals = sorted(approvals)
        status: SimulationStatus
        if blocked_reasons:
            status = "blocked"
        elif required_approvals:
            status = "approval_required"
        else:
            status = "allowed"
        return AgentPolicySimulationResult(
            status=status,
            allowed=status == "allowed",
            approval_required=status == "approval_required",
            blocked_reasons=blocked_reasons,
            required_permissions=permissions,
            required_approvals=required_approvals,
            budget_impact=active_request.budget_impact.model_dump(mode="json"),
            risk_impact=risk_impact,
            policy_trace=trace,
            metadata={
                "agent_id": active_request.agent_id,
                "tool": active_request.tool,
                "action": _action(active_request),
                "simulated_at": datetime.now(UTC).isoformat(),
            },
        )


def simulate_agent_action(
    request: AgentPolicySimulationRequest | None = None,
    **kwargs: Any,
) -> AgentPolicySimulationResult:
    return AgentPolicySimulator().simulate(request, **kwargs)


def _policy_engine(request: AgentPolicySimulationRequest) -> AgentGovernancePolicyEngine:
    org_policies: list[AgentGovernancePolicy] = []
    project_policies: list[AgentGovernancePolicy] = []
    campaign_policies: list[AgentGovernancePolicy] = []
    policies = list(request.active_policies)
    if request.proposed_policy is not None:
        policies.append(request.proposed_policy)
    for policy in policies:
        if policy.metadata.get("campaign_id") is not None:
            campaign_policies.append(policy)
        elif policy.project_id is not None:
            project_policies.append(policy)
        else:
            org_policies.append(policy)
    return AgentGovernancePolicyEngine(
        org_policies=org_policies,
        project_policies=project_policies,
        campaign_policies=campaign_policies,
        run_controls=request.run_controls,
    )


def _policy_action_request(request: AgentPolicySimulationRequest) -> AgentActionRequest:
    metadata = {
        **request.metadata,
        "tool": request.tool,
        "profile": request.profile,
    }
    if _is_generated_advancement(request):
        metadata["generated_molecule_advancement"] = True
    return AgentActionRequest(
        agent_id=request.agent_id,
        agent_type=request.agent_type,
        role=request.role or request.profile,
        autonomy_level=request.autonomy_level,
        action=_action(request),
        tool_category=request.tool_category,
        side_effect_level=request.side_effect_level,
        org_id=request.org_id,
        project_id=request.project_id,
        campaign_id=request.campaign_id,
        human_approved_actions=request.human_approved_actions,
        metadata=metadata,
    )


def _run_control_request(request: AgentPolicySimulationRequest) -> RunControlRequest:
    return RunControlRequest(
        agent_id=request.agent_id,
        agent_type=request.agent_type,
        org_id=request.org_id,
        project_id=request.project_id,
        campaign_id=request.campaign_id,
        action=_action(request),
        autonomy_level=request.autonomy_level,
        side_effect_level=request.side_effect_level,
        workflow_type="generated_molecule" if _is_generated_advancement(request) else None,
        metadata={
            **request.metadata,
            "generated_molecule_workflow": _is_generated_advancement(request),
        },
    )


def _apply_policy_decision(
    decision: AgentPolicyDecision,
    *,
    blocked_reasons: list[str],
    approvals: set[str],
) -> None:
    approvals.update(decision.required_approval_actions)
    hard_violations = []
    for violation in decision.violations:
        if violation.violation_type == "generated_molecule_without_human_approval":
            approvals.add(GENERATED_ADVANCEMENT_APPROVAL)
        else:
            hard_violations.append(violation)
    blocked_reasons.extend(violation.summary for violation in hard_violations)


def _budget_decision(request: AgentPolicySimulationRequest) -> AgentBudgetDecision | None:
    budget = _matching_budget(request)
    if budget is None:
        return None
    return AgentAutonomyBudgetManager(budgets=request.budgets).check_budget(
        budget.budget_id,
        request.budget_impact,
    )


def _matching_budget(request: AgentPolicySimulationRequest) -> AgentAutonomyBudget | None:
    candidates = [
        budget
        for budget in request.budgets
        if _budget_matches(budget, request)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=_budget_specificity, reverse=True)[0]


def _budget_matches(
    budget: AgentAutonomyBudget,
    request: AgentPolicySimulationRequest,
) -> bool:
    if budget.agent_id is not None and budget.agent_id != request.agent_id:
        return False
    if budget.campaign_id is not None and budget.campaign_id != request.campaign_id:
        return False
    if budget.project_id is not None and budget.project_id != request.project_id:
        return False
    if budget.org_id is not None and budget.org_id != request.org_id:
        return False
    return True


def _budget_specificity(budget: AgentAutonomyBudget) -> int:
    return sum(
        value is not None
        for value in [budget.org_id, budget.project_id, budget.campaign_id, budget.agent_id]
    )


def _risk_impact(request: AgentPolicySimulationRequest) -> dict[str, Any]:
    profile = request.risk_profile
    if profile is None:
        return {"status": "not_configured", "approval_required": False}
    cap = RISK_AUTONOMY_CAPS[profile.risk_level]
    exceeds_cap = AUTONOMY_ORDER[request.autonomy_level] > AUTONOMY_ORDER[cap]
    impact = {
        "status": "allowed",
        "risk_level": profile.risk_level,
        "allowed_autonomy_cap": cap,
        "approval_required": False,
        "risk_factors": profile.risk_factors,
    }
    if profile.risk_level == "critical":
        impact.update(
            {
                "status": "blocked",
                "reason": "Critical risk requires pausing the agent and admin review.",
            }
        )
    elif profile.risk_level == "high" or exceeds_cap:
        impact.update(
            {
                "status": "approval_required",
                "approval_required": True,
                "reason": f"Risk level {profile.risk_level} caps autonomy at {cap}.",
            }
        )
    return impact


def _required_permissions(request: AgentPolicySimulationRequest) -> list[str]:
    permissions = {"governance:read", "agent:execute"}
    action = _action(request)
    if request.side_effect_level == "external_write" or "external" in action:
        permissions.update({"governance:approve", "integration:write"})
    if _is_generated_advancement(request):
        permissions.update({"governance:approve", "campaign:approve"})
    if request.tool_category:
        permissions.add(f"tool_category:{request.tool_category}")
    return sorted(permissions)


def _action(request: AgentPolicySimulationRequest) -> str:
    return request.action or request.tool


def _is_generated_advancement(request: AgentPolicySimulationRequest) -> bool:
    action = _action(request)
    return (
        "advance_generated_molecule" in action
        or request.metadata.get("generated_molecule_advancement") is True
    )


__all__ = [
    "AgentPolicySimulationRequest",
    "AgentPolicySimulationResult",
    "AgentPolicySimulator",
    "simulate_agent_action",
]
