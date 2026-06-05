from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.policies import AUTONOMY_ORDER
from molecule_ranker.agent_governance.run_control import AgentRunControlManager
from molecule_ranker.agent_governance.schemas import (
    AgentGovernanceAutonomyLevel,
    AgentIncident,
    AgentRiskLevel,
    AgentRiskProfile,
    AgentRunControl,
)

RiskAction = Literal[
    "normal",
    "closer_monitoring",
    "require_approval_more_actions",
    "pause_agent_admin_review",
]
RiskActorType = Literal["human", "admin", "service_account", "system", "codex", "agent"]
ToolPackageRiskLevel = Literal["low", "medium", "high", "critical"]

RISK_LEVEL_THRESHOLDS: list[tuple[float, AgentRiskLevel]] = [
    (0.75, "critical"),
    (0.50, "high"),
    (0.25, "medium"),
    (0.0, "low"),
]
RISK_ACTIONS: dict[AgentRiskLevel, RiskAction] = {
    "low": "normal",
    "medium": "closer_monitoring",
    "high": "require_approval_more_actions",
    "critical": "pause_agent_admin_review",
}
RISK_AUTONOMY_CAPS: dict[AgentRiskLevel, AgentGovernanceAutonomyLevel] = {
    "low": "supervised_auto",
    "medium": "execute_with_approval",
    "high": "suggest_only",
    "critical": "observe_only",
}
TOOL_PACKAGE_RISK_WEIGHTS: dict[ToolPackageRiskLevel, float] = {
    "low": 0.0,
    "medium": 0.08,
    "high": 0.18,
    "critical": 0.35,
}
AUTONOMY_RISK_WEIGHTS: dict[AgentGovernanceAutonomyLevel, float] = {
    "disabled": 0.0,
    "observe_only": 0.0,
    "suggest_only": 0.02,
    "execute_safe_tools": 0.06,
    "execute_with_approval": 0.10,
    "supervised_auto": 0.16,
}
SIDE_EFFECT_RISK_WEIGHTS: dict[str, float] = {
    "none": 0.0,
    "artifact_write": 0.03,
    "db_write": 0.08,
    "external_read": 0.04,
    "external_write": 0.12,
}
HIGH_INCIDENT_STATUSES = {"open", "triaged", "investigating"}
HUMAN_LOWERING_ACTORS = {"human", "admin", "service_account", "system"}
CODEX_ACTOR_IDS = {"codex", "codex_cli", "codex-runtime-agent", "codex_worker"}


class AgentRiskInputs(BaseModel):
    agent_id: str
    org_id: str | None = None
    project_id: str | None = None
    guardrail_failures: int = Field(default=0, ge=0)
    policy_violations: int = Field(default=0, ge=0)
    unauthorized_tool_attempts: int = Field(default=0, ge=0)
    approval_rejection_rate: float = Field(default=0.0, ge=0, le=1)
    failed_repairs: int = Field(default=0, ge=0)
    repeated_failures: int = Field(default=0, ge=0)
    external_write_attempts: int = Field(default=0, ge=0)
    generated_advancement_attempts: int = Field(default=0, ge=0)
    hallucinated_artifact_attempts: int = Field(default=0, ge=0)
    secret_exposure_attempts: int = Field(default=0, ge=0)
    unresolved_incidents: int = Field(default=0, ge=0)
    incidents: list[AgentIncident] = Field(default_factory=list)
    tool_package_risk_levels: list[ToolPackageRiskLevel] = Field(default_factory=list)
    autonomy_level: AgentGovernanceAutonomyLevel = "observe_only"
    side_effect_usage: dict[str, int] = Field(default_factory=dict)
    recent_human_overrides: int = Field(default=0, ge=0)
    clean_eval_passes: int = Field(default=0, ge=0)
    human_review_completed: bool = False
    resolved_incidents: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskScoreAuthorization(BaseModel):
    actor_id: str
    actor_type: RiskActorType
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRiskDecision(BaseModel):
    profile: AgentRiskProfile
    risk_score: float = Field(ge=0, le=1)
    raw_risk_score: float = Field(ge=0, le=1)
    risk_action: RiskAction
    allowed_autonomy_cap: AgentGovernanceAutonomyLevel
    requires_run_control: bool
    reasons: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRiskScorer:
    """Compute visible agent risk scores and recommended governance actions."""

    def score_agent(
        self,
        inputs: AgentRiskInputs,
        *,
        previous_profile: AgentRiskProfile | None = None,
        authorization: RiskScoreAuthorization | None = None,
        computed_at: datetime | None = None,
    ) -> AgentRiskDecision:
        now = computed_at or datetime.now(UTC)
        components = _score_components(inputs)
        raw_score = _clamp(sum(components.values()))
        adjusted_score, lowering_reasons = _apply_lowering_rules(
            raw_score=raw_score,
            previous_profile=previous_profile,
            inputs=inputs,
            authorization=authorization,
            computed_at=now,
        )
        risk_level = _risk_level(adjusted_score)
        risk_action = RISK_ACTIONS[risk_level]
        autonomy_cap = RISK_AUTONOMY_CAPS[risk_level]
        risk_factors = _risk_factors(inputs, components, lowering_reasons)
        confidence = _confidence(inputs)
        unsafe_action_attempts = (
            inputs.unauthorized_tool_attempts
            + inputs.generated_advancement_attempts
            + inputs.hallucinated_artifact_attempts
            + inputs.secret_exposure_attempts
        )
        profile = AgentRiskProfile(
            risk_profile_id=f"agent-risk-profile-{uuid4().hex[:12]}",
            agent_id=inputs.agent_id,
            risk_level=risk_level,
            risk_factors=risk_factors,
            recent_guardrail_failures=inputs.guardrail_failures,
            recent_policy_violations=inputs.policy_violations,
            recent_failed_repairs=inputs.failed_repairs,
            recent_human_overrides=inputs.recent_human_overrides,
            unsafe_action_attempts=unsafe_action_attempts,
            external_write_attempts=inputs.external_write_attempts,
            approval_rejection_rate=inputs.approval_rejection_rate,
            confidence=confidence,
            computed_at=now,
            metadata={
                **inputs.metadata,
                "risk_score": adjusted_score,
                "raw_risk_score": raw_score,
                "risk_score_visible": True,
                "risk_action": risk_action,
                "allowed_autonomy_cap": autonomy_cap,
                "score_components": components,
                "generated_advancement_attempts": inputs.generated_advancement_attempts,
                "hallucinated_artifact_attempts": inputs.hallucinated_artifact_attempts,
                "secret_exposure_attempts": inputs.secret_exposure_attempts,
                "unresolved_incidents": _unresolved_incident_count(inputs),
                "tool_package_risk_levels": inputs.tool_package_risk_levels,
                "side_effect_usage": inputs.side_effect_usage,
                "lowering_reasons": lowering_reasons,
            },
        )
        return AgentRiskDecision(
            profile=profile,
            risk_score=adjusted_score,
            raw_risk_score=raw_score,
            risk_action=risk_action,
            allowed_autonomy_cap=autonomy_cap,
            requires_run_control=risk_level == "critical",
            reasons=_decision_reasons(profile),
            score_components=components,
            metadata={
                "risk_score_visible": True,
                "risk_affects_allowed_autonomy": True,
                "risk_lowering_reasons": lowering_reasons,
            },
        )

    def autonomy_allowed(
        self,
        risk_profile: AgentRiskProfile,
        requested_autonomy_level: AgentGovernanceAutonomyLevel,
    ) -> bool:
        cap = self.autonomy_cap_for_risk(risk_profile)
        return AUTONOMY_ORDER[requested_autonomy_level] <= AUTONOMY_ORDER[cap]

    def autonomy_cap_for_risk(
        self,
        risk_profile: AgentRiskProfile,
    ) -> AgentGovernanceAutonomyLevel:
        metadata_cap = risk_profile.metadata.get("allowed_autonomy_cap")
        if isinstance(metadata_cap, str) and metadata_cap in AUTONOMY_ORDER:
            return cast(AgentGovernanceAutonomyLevel, metadata_cap)
        return RISK_AUTONOMY_CAPS[risk_profile.risk_level]

    def apply_recommended_run_control(
        self,
        decision: AgentRiskDecision,
        manager: AgentRunControlManager,
        *,
        applied_by: str = "risk-engine",
        org_id: str | None = None,
        project_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentRunControl | None:
        if not decision.requires_run_control:
            return None
        return manager.apply_control(
            control_type="pause",
            reason="Critical agent risk requires pause and admin review.",
            applied_by=applied_by,
            org_id=org_id,
            project_id=project_id,
            agent_id=decision.profile.agent_id,
            applied_at=now or decision.profile.computed_at,
            metadata={
                "risk_profile_id": decision.profile.risk_profile_id,
                "risk_score": decision.risk_score,
                "risk_level": decision.profile.risk_level,
                "requires_admin_review": True,
            },
        )


def _score_components(inputs: AgentRiskInputs) -> dict[str, float]:
    unresolved_incidents = _unresolved_incident_count(inputs)
    incident_severity_score = _incident_severity_score(inputs.incidents)
    tool_package_score = min(
        sum(TOOL_PACKAGE_RISK_WEIGHTS[level] for level in inputs.tool_package_risk_levels),
        0.50,
    )
    side_effect_score = min(
        sum(
            SIDE_EFFECT_RISK_WEIGHTS.get(side_effect, 0.05) * max(count, 0)
            for side_effect, count in inputs.side_effect_usage.items()
        ),
        0.35,
    )
    return {
        "guardrail_failures": min(inputs.guardrail_failures * 0.18, 0.54),
        "policy_violations": min(inputs.policy_violations * 0.12, 0.36),
        "unauthorized_tool_attempts": min(
            inputs.unauthorized_tool_attempts * 0.10,
            0.30,
        ),
        "approval_rejection_rate": inputs.approval_rejection_rate * 0.25,
        "failed_repairs": min(inputs.failed_repairs * 0.08, 0.24),
        "repeated_failures": min(inputs.repeated_failures * 0.08, 0.24),
        "external_write_attempts": min(inputs.external_write_attempts * 0.12, 0.36),
        "generated_advancement_attempts": min(
            inputs.generated_advancement_attempts * 0.10,
            0.30,
        ),
        "hallucinated_artifact_attempts": min(
            inputs.hallucinated_artifact_attempts * 0.18,
            0.54,
        ),
        "secret_exposure_attempts": min(inputs.secret_exposure_attempts * 0.25, 0.75),
        "unresolved_incidents": min(unresolved_incidents * 0.15, 0.45),
        "incident_severity": incident_severity_score,
        "tool_package_risk_levels": tool_package_score,
        "autonomy_level": AUTONOMY_RISK_WEIGHTS[inputs.autonomy_level],
        "side_effect_usage": side_effect_score,
    }


def _apply_lowering_rules(
    *,
    raw_score: float,
    previous_profile: AgentRiskProfile | None,
    inputs: AgentRiskInputs,
    authorization: RiskScoreAuthorization | None,
    computed_at: datetime,
) -> tuple[float, list[str]]:
    if previous_profile is None:
        return raw_score, []
    previous_score = _previous_risk_score(previous_profile)
    if raw_score >= previous_score:
        return raw_score, []
    if authorization is not None and _is_codex_actor(authorization):
        return previous_score, ["Codex cannot lower agent risk scores."]
    if authorization is not None and authorization.actor_type not in HUMAN_LOWERING_ACTORS:
        return previous_score, ["Risk lowering requires human/admin/service review."]
    if not _lowering_evidence_present(
        inputs=inputs,
        previous_profile=previous_profile,
        computed_at=computed_at,
    ):
        return previous_score, [
            "Risk can lower only through time, clean evals, human review, and resolved incidents."
        ]
    elapsed_days = max((computed_at - previous_profile.computed_at).days, 1)
    max_drop = min(
        0.08 + inputs.clean_eval_passes * 0.03 + inputs.resolved_incidents * 0.02,
        0.10 + elapsed_days * 0.03,
        0.25,
    )
    lowered_score = max(raw_score, previous_score - max_drop)
    return _clamp(lowered_score), ["Risk lowered gradually after verified remediation."]


def _lowering_evidence_present(
    *,
    inputs: AgentRiskInputs,
    previous_profile: AgentRiskProfile,
    computed_at: datetime,
) -> bool:
    return (
        (computed_at - previous_profile.computed_at).total_seconds() >= 24 * 60 * 60
        and inputs.clean_eval_passes > 0
        and inputs.human_review_completed
        and inputs.resolved_incidents > 0
    )


def _is_codex_actor(authorization: RiskScoreAuthorization) -> bool:
    return (
        authorization.actor_type == "codex"
        or authorization.actor_id.strip().lower() in CODEX_ACTOR_IDS
    )


def _previous_risk_score(profile: AgentRiskProfile) -> float:
    score = profile.metadata.get("risk_score")
    if isinstance(score, int | float):
        return _clamp(float(score))
    return {
        "low": 0.15,
        "medium": 0.35,
        "high": 0.60,
        "critical": 0.85,
    }[profile.risk_level]


def _risk_level(score: float) -> AgentRiskLevel:
    for threshold, level in RISK_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "low"


def _risk_factors(
    inputs: AgentRiskInputs,
    components: dict[str, float],
    lowering_reasons: list[str],
) -> list[str]:
    factors = [
        key
        for key, value in components.items()
        if value > 0 and key not in {"autonomy_level"}
    ]
    if AUTONOMY_RISK_WEIGHTS[inputs.autonomy_level] > 0:
        factors.append(f"autonomy_level:{inputs.autonomy_level}")
    factors.extend(lowering_reasons)
    return factors


def _decision_reasons(profile: AgentRiskProfile) -> list[str]:
    action = profile.metadata.get("risk_action", "normal")
    return [
        f"Risk level is {profile.risk_level}.",
        f"Recommended action: {action}.",
        f"Allowed autonomy cap: {profile.metadata.get('allowed_autonomy_cap')}.",
    ]


def _confidence(inputs: AgentRiskInputs) -> float:
    evidence_count = (
        inputs.guardrail_failures
        + inputs.policy_violations
        + inputs.unauthorized_tool_attempts
        + inputs.failed_repairs
        + inputs.repeated_failures
        + inputs.external_write_attempts
        + inputs.generated_advancement_attempts
        + inputs.hallucinated_artifact_attempts
        + inputs.secret_exposure_attempts
        + _unresolved_incident_count(inputs)
        + len(inputs.tool_package_risk_levels)
        + sum(max(value, 0) for value in inputs.side_effect_usage.values())
    )
    return _clamp(0.55 + min(evidence_count, 10) * 0.04)


def _unresolved_incident_count(inputs: AgentRiskInputs) -> int:
    unresolved_from_incidents = sum(
        1 for incident in inputs.incidents if incident.status in HIGH_INCIDENT_STATUSES
    )
    return inputs.unresolved_incidents + unresolved_from_incidents


def _incident_severity_score(incidents: list[AgentIncident]) -> float:
    score = 0.0
    for incident in incidents:
        if incident.status not in HIGH_INCIDENT_STATUSES:
            continue
        if incident.severity == "critical":
            score += 0.45
        elif incident.severity == "high":
            score += 0.30
        elif incident.severity == "medium":
            score += 0.12
        elif incident.severity == "low":
            score += 0.04
    return min(score, 0.60)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


__all__ = [
    "AgentRiskDecision",
    "AgentRiskInputs",
    "AgentRiskProfile",
    "AgentRiskScorer",
    "RiskScoreAuthorization",
]
