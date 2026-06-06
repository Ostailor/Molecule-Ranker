from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.schemas import (
    AgentCapabilityGrant,
    AgentGovernanceAutonomyLevel,
    AgentGovernancePolicy,
    AgentPolicyViolation,
    AgentRunControl,
    AgentType,
)

AUTONOMY_ORDER: dict[str, int] = {
    "disabled": 0,
    "observe_only": 1,
    "suggest_only": 2,
    "execute_safe_tools": 3,
    "execute_with_approval": 4,
    "supervised_auto": 5,
}
HIGH_RISK_ACTIONS = {
    "advance_generated_molecule",
    "advance_generated_molecule_to_assay",
    "approve_stage_gate",
    "external_write",
    "run_external_sync_write",
}
CODEX_WORKER_BLOCKED_ACTIONS = {
    "approve_own_action",
    "approve_policy_override",
    "approve_autonomy_increase",
    "self_certify",
}
GENERATED_MOLECULE_ACTIONS = {
    "advance_generated_molecule",
    "advance_generated_molecule_to_assay",
    "generated_molecule_export",
}
EXTERNAL_WRITE_SIDE_EFFECTS = {"external_write"}


PolicyScope = Literal["platform", "org", "project", "campaign"]
PolicyDecisionStatus = Literal["allowed", "blocked", "approval_required"]


class AgentActionRequest(BaseModel):
    agent_id: str
    agent_type: AgentType
    action: str
    autonomy_level: AgentGovernanceAutonomyLevel
    org_id: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    role: str | None = None
    tool_category: str | None = None
    side_effect_level: str | None = None
    human_approved_actions: set[str] = Field(default_factory=set)
    human_approved_exceptions: set[str] = Field(default_factory=set)
    session_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EffectiveAgentGovernancePolicy(BaseModel):
    policy_ids: list[str]
    max_autonomy_level: AgentGovernanceAutonomyLevel
    allowed_tool_categories: list[str] = Field(default_factory=list)
    denied_tool_categories: list[str] = Field(default_factory=list)
    allowed_side_effect_levels: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    guardrail_profile: str = "strict_scientific"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPolicyDecision(BaseModel):
    status: PolicyDecisionStatus
    allowed: bool
    requires_approval: bool
    autonomy_allowed: bool
    reasons: list[str] = Field(default_factory=list)
    required_approval_actions: list[str] = Field(default_factory=list)
    effective_policy: EffectiveAgentGovernancePolicy
    violations: list[AgentPolicyViolation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentGovernancePolicyEngine:
    """Evaluate V2.6 agent actions against merged governance policies."""

    def __init__(
        self,
        *,
        platform_default: AgentGovernancePolicy | None = None,
        org_policies: list[AgentGovernancePolicy] | None = None,
        project_policies: list[AgentGovernancePolicy] | None = None,
        campaign_policies: list[AgentGovernancePolicy] | None = None,
        run_controls: list[AgentRunControl] | None = None,
        capability_grants: list[AgentCapabilityGrant] | None = None,
    ) -> None:
        self.platform_default = platform_default or default_platform_policy()
        self.org_policies = list(org_policies or [])
        self.project_policies = list(project_policies or [])
        self.campaign_policies = list(campaign_policies or [])
        self.run_controls = list(run_controls or [])
        self.capability_grants = list(capability_grants or [])

    def load_org_policies(self, policies: list[AgentGovernancePolicy]) -> None:
        self.org_policies = list(policies)

    def load_project_policies(self, policies: list[AgentGovernancePolicy]) -> None:
        self.project_policies = list(policies)

    def load_campaign_policies(self, policies: list[AgentGovernancePolicy]) -> None:
        self.campaign_policies = list(policies)

    def load_run_controls(self, run_controls: list[AgentRunControl]) -> None:
        self.run_controls = list(run_controls)

    def merge_policies(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        campaign_id: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
    ) -> EffectiveAgentGovernancePolicy:
        policies = self._matching_policies(
            org_id=org_id,
            project_id=project_id,
            campaign_id=campaign_id,
            agent_id=agent_id,
            role=role,
        )
        return _merge_policy_chain(policies)

    def evaluate_action(
        self,
        request: AgentActionRequest | None = None,
        **kwargs: Any,
    ) -> AgentPolicyDecision:
        active_request = request or AgentActionRequest.model_validate(kwargs)
        effective = self.merge_policies(
            org_id=active_request.org_id,
            project_id=active_request.project_id,
            campaign_id=active_request.campaign_id,
            agent_id=active_request.agent_id,
            role=active_request.role,
        )
        violations: list[AgentPolicyViolation] = []
        required_approvals: set[str] = set()
        reasons: list[str] = []

        emergency = self._emergency_run_control(active_request)
        if emergency is not None:
            violations.append(
                _violation(
                    active_request,
                    effective,
                    "emergency_kill_switch",
                    f"Emergency run control {emergency.control_id} blocks this action.",
                )
            )
            return _decision(
                status="blocked",
                effective=effective,
                request=active_request,
                reasons=[violations[-1].summary],
                violations=violations,
            )

        autonomy_allowed = self.is_autonomy_level_allowed(
            active_request.autonomy_level,
            effective_policy=effective,
        )
        if not autonomy_allowed:
            violations.append(
                _violation(
                    active_request,
                    effective,
                    "autonomy_level_capped",
                    (
                        f"Autonomy {active_request.autonomy_level} exceeds cap "
                        f"{effective.max_autonomy_level}."
                    ),
                )
            )

        if active_request.action in effective.blocked_actions:
            if not self._admin_exception_allows(active_request):
                violations.append(
                    _violation(
                        active_request,
                        effective,
                        "blocked_action",
                        f"Action is blocked by governance policy: {active_request.action}.",
                    )
                )

        if (
            active_request.tool_category
            and active_request.tool_category in effective.denied_tool_categories
        ):
            violations.append(
                _violation(
                    active_request,
                    effective,
                    "denied_tool_category",
                    f"Tool category is denied: {active_request.tool_category}.",
                )
            )
        elif (
            effective.allowed_tool_categories
            and active_request.tool_category
            and active_request.tool_category not in effective.allowed_tool_categories
        ):
            violations.append(
                _violation(
                    active_request,
                    effective,
                    "tool_category_not_allowed",
                    f"Tool category is outside allowed categories: {active_request.tool_category}.",
                )
            )

        if (
            effective.allowed_side_effect_levels
            and active_request.side_effect_level
            and active_request.side_effect_level not in effective.allowed_side_effect_levels
        ):
            if active_request.side_effect_level in EXTERNAL_WRITE_SIDE_EFFECTS:
                required_approvals.add("external_write")
            else:
                violations.append(
                    _violation(
                        active_request,
                        effective,
                        "side_effect_not_allowed",
                        (
                            "Side-effect level is outside allowed levels: "
                            f"{active_request.side_effect_level}."
                        ),
                    )
                )

        if self._is_external_write(active_request):
            required_approvals.add("external_write")

        if self._is_generated_molecule_advancement(active_request):
            if "generated_molecule_human_review" not in active_request.human_approved_actions:
                violations.append(
                    _violation(
                        active_request,
                        effective,
                        "generated_molecule_without_human_approval",
                        "Generated molecule advancement requires human approval.",
                    )
                )

        if active_request.agent_type == "codex_worker":
            worker_violation = self._codex_worker_violation(active_request)
            if worker_violation is not None:
                violations.append(worker_violation)

        for action in effective.approval_required_actions:
            if action == active_request.action or action == active_request.side_effect_level:
                required_approvals.add(action)

        for control in self._active_matching_run_controls(active_request):
            if control.control_type == "require_approval_all_actions":
                required_approvals.add("all_actions")
                reasons.append(f"Run control {control.control_id} requires approval.")
            elif control.control_type == "restrict_autonomy":
                restricted = _metadata_autonomy_cap(control)
                if restricted and _autonomy_gt(active_request.autonomy_level, restricted):
                    violations.append(
                        _violation(
                            active_request,
                            effective,
                            "run_control_autonomy_restricted",
                            f"Run control restricts autonomy to {restricted}.",
                        )
                    )

        approved_required = required_approvals & active_request.human_approved_actions
        missing_approvals = required_approvals - approved_required
        if violations:
            return _decision(
                status="blocked",
                effective=effective,
                request=active_request,
                reasons=[*reasons, *(violation.summary for violation in violations)],
                required_approvals=sorted(missing_approvals),
                violations=violations,
                autonomy_allowed=autonomy_allowed,
            )
        if missing_approvals:
            return _decision(
                status="approval_required",
                effective=effective,
                request=active_request,
                reasons=[*reasons, "Human approval is required before execution."],
                required_approvals=sorted(missing_approvals),
                autonomy_allowed=autonomy_allowed,
            )
        return _decision(
            status="allowed",
            effective=effective,
            request=active_request,
            reasons=["Action is allowed by governance policy."],
            autonomy_allowed=autonomy_allowed,
        )

    def requires_approval(
        self,
        request: AgentActionRequest | None = None,
        **kwargs: Any,
    ) -> bool:
        return self.evaluate_action(request, **kwargs).requires_approval

    def is_autonomy_level_allowed(
        self,
        autonomy_level: AgentGovernanceAutonomyLevel,
        *,
        effective_policy: EffectiveAgentGovernancePolicy | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        campaign_id: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
    ) -> bool:
        policy = effective_policy or self.merge_policies(
            org_id=org_id,
            project_id=project_id,
            campaign_id=campaign_id,
            agent_id=agent_id,
            role=role,
        )
        return not _autonomy_gt(autonomy_level, policy.max_autonomy_level)

    def _matching_policies(
        self,
        *,
        org_id: str | None,
        project_id: str | None,
        campaign_id: str | None,
        agent_id: str | None,
        role: str | None,
    ) -> list[AgentGovernancePolicy]:
        policies = [self.platform_default]
        policies.extend(
            policy
            for policy in self.org_policies
            if _policy_matches(policy, org_id=org_id, project_id=None, campaign_id=None)
        )
        policies.extend(
            policy
            for policy in self.project_policies
            if _policy_matches(
                policy,
                org_id=org_id,
                project_id=project_id,
                campaign_id=None,
            )
        )
        policies.extend(
            policy
            for policy in self.campaign_policies
            if _policy_matches(
                policy,
                org_id=org_id,
                project_id=project_id,
                campaign_id=campaign_id,
            )
        )
        return [
            policy
            for policy in policies
            if policy.enabled and _applies_to_agent_or_role(policy, agent_id=agent_id, role=role)
        ]

    def _emergency_run_control(self, request: AgentActionRequest) -> AgentRunControl | None:
        for control in self._active_matching_run_controls(request):
            if control.control_type in {"kill_switch", "disable", "pause"}:
                return control
        return None

    def _active_matching_run_controls(self, request: AgentActionRequest) -> list[AgentRunControl]:
        now = datetime.now(UTC)
        return [
            control
            for control in self.run_controls
            if control.active
            and (control.expires_at is None or control.expires_at >= now)
            and _run_control_matches(control, request)
        ]

    def _admin_exception_allows(self, request: AgentActionRequest) -> bool:
        if request.action not in request.human_approved_exceptions:
            return False
        if request.action in HIGH_RISK_ACTIONS:
            return bool(request.metadata.get("admin_policy_allows_high_risk_exception"))
        return True

    def _is_external_write(self, request: AgentActionRequest) -> bool:
        return (
            request.side_effect_level == "external_write"
            or request.action in {"external_write", "run_external_sync_write"}
        )

    def _is_generated_molecule_advancement(self, request: AgentActionRequest) -> bool:
        return request.action in GENERATED_MOLECULE_ACTIONS or bool(
            request.metadata.get("generated_molecule_advancement")
        )

    def _codex_worker_violation(
        self,
        request: AgentActionRequest,
    ) -> AgentPolicyViolation | None:
        if request.action in CODEX_WORKER_BLOCKED_ACTIONS:
            return _violation(
                request,
                self.merge_policies(
                    org_id=request.org_id,
                    project_id=request.project_id,
                    campaign_id=request.campaign_id,
                    agent_id=request.agent_id,
                    role=request.role,
                ),
                "codex_worker_restricted_action",
                f"Codex workers cannot perform restricted action: {request.action}.",
            )
        return None


def default_platform_policy() -> AgentGovernancePolicy:
    now = datetime.now(UTC)
    return AgentGovernancePolicy(
        policy_id="platform-default-agent-governance",
        org_id=None,
        project_id=None,
        policy_name="Platform default agent governance",
        policy_version="2.9.0",
        applies_to_roles=[],
        applies_to_agents=[],
        max_autonomy_level="execute_with_approval",
        allowed_tool_categories=[],
        denied_tool_categories=[],
        allowed_side_effect_levels=["none", "artifact_write", "external_read"],
        approval_required_actions=["external_write", "run_external_sync_write"],
        blocked_actions=[
            "approve_own_action",
            "approve_policy_override",
            "approve_autonomy_increase",
            "self_certify",
        ],
        budget_policy_id=None,
        guardrail_profile="strict_scientific",
        incident_policy_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
        metadata={"scope": "platform"},
    )


def _merge_policy_chain(policies: list[AgentGovernancePolicy]) -> EffectiveAgentGovernancePolicy:
    if not policies:
        policies = [default_platform_policy()]
    autonomy_caps = [
        cast(AgentGovernanceAutonomyLevel, policy.max_autonomy_level) for policy in policies
    ]
    max_autonomy_level = cast(
        AgentGovernanceAutonomyLevel,
        min(autonomy_caps, key=lambda value: AUTONOMY_ORDER[value]),
    )
    allowed_tool_categories = _intersect_non_empty(
        [policy.allowed_tool_categories for policy in policies]
    )
    allowed_side_effect_levels = _intersect_non_empty(
        [policy.allowed_side_effect_levels for policy in policies]
    )
    return EffectiveAgentGovernancePolicy(
        policy_ids=[policy.policy_id for policy in policies],
        max_autonomy_level=max_autonomy_level,
        allowed_tool_categories=allowed_tool_categories,
        denied_tool_categories=sorted(
            {item for policy in policies for item in policy.denied_tool_categories}
        ),
        allowed_side_effect_levels=allowed_side_effect_levels,
        approval_required_actions=sorted(
            {item for policy in policies for item in policy.approval_required_actions}
        ),
        blocked_actions=sorted({item for policy in policies for item in policy.blocked_actions}),
        guardrail_profile=policies[-1].guardrail_profile,
        metadata={
            "policy_precedence": [policy.policy_id for policy in policies],
        },
    )


def _intersect_non_empty(values: list[list[str]]) -> list[str]:
    populated = [set(value) for value in values if value]
    if not populated:
        return []
    intersection = populated[0]
    for value in populated[1:]:
        intersection &= value
    return sorted(intersection)


def _policy_matches(
    policy: AgentGovernancePolicy,
    *,
    org_id: str | None,
    project_id: str | None,
    campaign_id: str | None,
) -> bool:
    if policy.org_id is not None and policy.org_id != org_id:
        return False
    if policy.project_id is not None and policy.project_id != project_id:
        return False
    policy_campaign_id = policy.metadata.get("campaign_id")
    if policy_campaign_id is not None and policy_campaign_id != campaign_id:
        return False
    return True


def _applies_to_agent_or_role(
    policy: AgentGovernancePolicy,
    *,
    agent_id: str | None,
    role: str | None,
) -> bool:
    agent_match = not policy.applies_to_agents or agent_id in policy.applies_to_agents
    role_match = not policy.applies_to_roles or role in policy.applies_to_roles
    return agent_match and role_match


def _run_control_matches(control: AgentRunControl, request: AgentActionRequest) -> bool:
    if control.org_id is not None and control.org_id != request.org_id:
        return False
    if control.project_id is not None and control.project_id != request.project_id:
        return False
    if control.agent_id is not None and control.agent_id != request.agent_id:
        return False
    campaign_id = control.metadata.get("campaign_id")
    if campaign_id is not None and campaign_id != request.campaign_id:
        return False
    return True


def _metadata_autonomy_cap(control: AgentRunControl) -> AgentGovernanceAutonomyLevel | None:
    value = control.metadata.get("max_autonomy_level")
    if value in AUTONOMY_ORDER:
        return value  # type: ignore[return-value]
    return None


def _autonomy_gt(
    left: AgentGovernanceAutonomyLevel,
    right: AgentGovernanceAutonomyLevel,
) -> bool:
    return AUTONOMY_ORDER[left] > AUTONOMY_ORDER[right]


def _violation(
    request: AgentActionRequest,
    effective: EffectiveAgentGovernancePolicy,
    violation_type: str,
    summary: str,
) -> AgentPolicyViolation:
    return AgentPolicyViolation(
        violation_id=f"agent-policy-violation-{uuid4().hex[:12]}",
        policy_id=effective.policy_ids[-1] if effective.policy_ids else "unknown",
        agent_id=request.agent_id,
        session_id=request.session_id,
        violation_type=violation_type,
        blocked=True,
        summary=summary,
        detected_at=datetime.now(UTC),
        artifact_ids=request.artifact_ids,
        metadata={
            "action": request.action,
            "tool_category": request.tool_category,
            "side_effect_level": request.side_effect_level,
            "policy_ids": effective.policy_ids,
        },
    )


def _decision(
    *,
    status: PolicyDecisionStatus,
    effective: EffectiveAgentGovernancePolicy,
    request: AgentActionRequest,
    reasons: list[str],
    required_approvals: list[str] | None = None,
    violations: list[AgentPolicyViolation] | None = None,
    autonomy_allowed: bool = True,
) -> AgentPolicyDecision:
    approval_actions = required_approvals or []
    return AgentPolicyDecision(
        status=status,
        allowed=status == "allowed",
        requires_approval=status == "approval_required" or bool(approval_actions),
        autonomy_allowed=autonomy_allowed,
        reasons=list(dict.fromkeys(reasons)),
        required_approval_actions=approval_actions,
        effective_policy=effective,
        violations=violations or [],
        metadata={
            "agent_id": request.agent_id,
            "agent_type": request.agent_type,
            "action": request.action,
            "autonomy_level": request.autonomy_level,
        },
    )


__all__ = [
    "AgentActionRequest",
    "AgentGovernancePolicy",
    "AgentGovernancePolicyEngine",
    "AgentPolicyDecision",
    "EffectiveAgentGovernancePolicy",
    "default_platform_policy",
]
