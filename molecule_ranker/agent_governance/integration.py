from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Literal, cast

from molecule_ranker.agent_governance.policies import (
    AgentActionRequest,
    AgentGovernancePolicyEngine,
    AgentPolicyDecision,
)
from molecule_ranker.agent_governance.risk import (
    AgentRiskInputs,
    AgentRiskScorer,
    RiskScoreAuthorization,
)
from molecule_ranker.agent_governance.schemas import (
    AgentGovernanceAutonomyLevel,
    AgentRiskProfile,
    AgentType,
)

AGENT_TYPES = {"runtime_agent", "subagent", "campaign_copilot", "tool_agent", "codex_worker"}
AUTONOMY_LEVELS = {
    "observe_only",
    "suggest_only",
    "execute_safe_tools",
    "execute_with_approval",
    "supervised_auto",
    "disabled",
}
AUTONOMY_ALIASES: dict[str, AgentGovernanceAutonomyLevel] = {
    "dry_run": "observe_only",
    "execute_safe_actions": "execute_safe_tools",
    "full_auto_restricted": "supervised_auto",
    "safe_only": "execute_safe_tools",
    "with_approval": "execute_with_approval",
}


def governance_autonomy_level(value: Any) -> AgentGovernanceAutonomyLevel:
    text = str(value or "suggest_only")
    mapped = AUTONOMY_ALIASES.get(text, text)
    if mapped in AUTONOMY_LEVELS:
        return cast(AgentGovernanceAutonomyLevel, mapped)
    return "suggest_only"


def governance_agent_type(value: Any, *, default: AgentType = "runtime_agent") -> AgentType:
    text = str(value or default)
    if text in AGENT_TYPES:
        return cast(AgentType, text)
    return default


def evaluate_runtime_governance(
    policy_engine: AgentGovernancePolicyEngine | None,
    *,
    agent_id: str,
    agent_type: AgentType,
    action: str,
    autonomy_level: Any,
    org_id: str | None,
    project_id: str | None,
    campaign_id: str | None = None,
    role: str | None = None,
    tool_category: str | None = None,
    side_effect_level: str | None = None,
    approvals: set[str] | None = None,
    session_id: str | None = None,
    artifact_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentPolicyDecision | None:
    if policy_engine is None:
        return None
    request = AgentActionRequest(
        agent_id=agent_id,
        agent_type=agent_type,
        action=action,
        autonomy_level=governance_autonomy_level(autonomy_level),
        org_id=org_id,
        project_id=project_id,
        campaign_id=campaign_id,
        role=role,
        tool_category=tool_category,
        side_effect_level=side_effect_level,
        human_approved_actions=set(approvals or set()),
        session_id=session_id,
        artifact_ids=artifact_ids or [],
        metadata=metadata or {},
    )
    return policy_engine.evaluate_action(request)


def record_governance_risk_event(
    *,
    risk_scorer: AgentRiskScorer | None,
    risk_profiles: MutableMapping[str, AgentRiskProfile] | None,
    agent_id: str,
    autonomy_level: Any,
    side_effect_level: str | None = None,
    policy_violations: int = 0,
    guardrail_failures: int = 0,
    unauthorized_tool_attempts: int = 0,
    failed_repairs: int = 0,
    metadata: dict[str, Any] | None = None,
) -> AgentRiskProfile | None:
    if risk_scorer is None or risk_profiles is None:
        return None
    previous = risk_profiles.get(agent_id)
    side_effect_usage = _previous_side_effect_usage(previous)
    if side_effect_level:
        side_effect_usage[side_effect_level] = side_effect_usage.get(side_effect_level, 0) + 1
    inputs = AgentRiskInputs(
        agent_id=agent_id,
        guardrail_failures=(previous.recent_guardrail_failures if previous else 0)
        + guardrail_failures,
        policy_violations=(previous.recent_policy_violations if previous else 0)
        + policy_violations,
        unauthorized_tool_attempts=unauthorized_tool_attempts,
        failed_repairs=(previous.recent_failed_repairs if previous else 0) + failed_repairs,
        external_write_attempts=(previous.external_write_attempts if previous else 0)
        + (1 if side_effect_level == "external_write" else 0),
        approval_rejection_rate=previous.approval_rejection_rate if previous else 0.0,
        recent_human_overrides=previous.recent_human_overrides if previous else 0,
        autonomy_level=governance_autonomy_level(autonomy_level),
        side_effect_usage=side_effect_usage,
        metadata={**(metadata or {}), "updated_after_governed_failure": True},
    )
    decision = risk_scorer.score_agent(
        inputs,
        previous_profile=previous,
        authorization=RiskScoreAuthorization(actor_id="governance-runtime", actor_type="system"),
    )
    risk_profiles[agent_id] = decision.profile
    return decision.profile


def policy_decision_error(decision: AgentPolicyDecision) -> str:
    return "; ".join(decision.reasons) or decision.status


def policy_decision_status(decision: AgentPolicyDecision) -> Literal[
    "policy_blocked",
    "approval_required",
]:
    return "approval_required" if decision.requires_approval else "policy_blocked"


def _previous_side_effect_usage(profile: AgentRiskProfile | None) -> dict[str, int]:
    if profile is None:
        return {}
    raw = profile.metadata.get("side_effect_usage")
    if not isinstance(raw, dict):
        return {}
    usage: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, int):
            usage[key] = max(value, 0)
    return usage
