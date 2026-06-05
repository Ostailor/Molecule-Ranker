from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.approvals import approval_type_for_tool
from molecule_ranker.runtime_agents.schemas import (
    ApprovalType,
    AutonomyLevel,
    RiskLevel,
    RuntimeAgentSchema,
    RuntimeToolSpec,
)

AgentGovernancePolicyStatus = Literal["draft", "active", "retired"]
AgentGrantStatus = Literal["pending", "active", "revoked", "expired"]
AgentBudgetSubjectType = Literal["agent", "campaign"]
AgentCertificationStatus = Literal["pending", "certified", "expired", "revoked"]
AgentRunStatus = Literal["active", "paused", "disabled"]
AgentIncidentSeverity = Literal["low", "medium", "high", "critical"]
AgentIncidentStatus = Literal["open", "investigating", "resolved"]
AgentViolationSeverity = Literal["warning", "block", "critical"]
AgentChangeType = Literal[
    "autonomy_increase",
    "policy_change",
    "policy_override",
    "capability_grant",
    "budget_change",
    "certification",
    "kill_switch_disable",
]
AgentChangeRequestStatus = Literal["pending", "approved", "rejected", "expired"]
KillSwitchScope = Literal["agent", "campaign", "org"]
GovernanceDecisionStatus = Literal["allowed", "blocked", "requires_approval"]

AUTONOMY_ORDER: dict[AutonomyLevel, int] = {
    "observe_only": 0,
    "suggest_only": 1,
    "execute_safe_tools": 2,
    "execute_with_approval": 3,
    "full_auto_restricted": 4,
}

HUMAN_ONLY_GOVERNANCE_CHANGES = {
    "autonomy_increase",
    "policy_change",
    "policy_override",
    "capability_grant",
    "budget_change",
    "certification",
    "kill_switch_disable",
}
DEFAULT_HUMAN_ONLY_APPROVALS: tuple[ApprovalType, ...] = (
    "broad_codex_access",
    "campaign_advance",
    "codex_full_auto",
    "destructive_action",
    "external_write",
    "generated_molecule_export",
    "integration_sync",
    "policy_override",
    "stage_gate",
)


class AgentGovernanceError(ValueError):
    """Raised when a governance action violates V2.6 policy."""


class AgentGovernancePolicy(RuntimeAgentSchema):
    policy_id: str
    org_id: str
    name: str
    version: str
    status: AgentGovernancePolicyStatus = "draft"
    max_autonomy_level: AutonomyLevel = "execute_with_approval"
    allowed_agent_ids: list[str] = Field(default_factory=list)
    allowed_tool_names: list[str] = Field(default_factory=list)
    allowed_tool_categories: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    approval_required_for: list[ApprovalType] = Field(default_factory=list)
    human_only_approval_types: list[ApprovalType] = Field(
        default_factory=lambda: list(DEFAULT_HUMAN_ONLY_APPROVALS)
    )
    scientific_guardrail_floor: bool = True
    codex_may_modify_policy: bool = False
    policy_hash: str | None = None
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    tool_manifest_hashes: dict[str, str] = Field(default_factory=dict)
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "allowed_agent_ids",
        "allowed_tool_names",
        "allowed_tool_categories",
        "allowed_permissions",
        "approval_required_for",
        "human_only_approval_types",
    )
    @classmethod
    def require_non_empty_list_items(cls, value: list[str]) -> list[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("governance policy list values must be non-empty")
        return value

    @model_validator(mode="after")
    def require_human_approved_active_policy(self) -> AgentGovernancePolicy:
        if not self.scientific_guardrail_floor:
            raise ValueError("governance policy cannot weaken scientific guardrails")
        if self.codex_may_modify_policy:
            raise ValueError("Codex cannot modify governance policy without approval")
        if self.status == "active":
            if not self.approved_by or self.approved_at is None:
                raise ValueError("active governance policies require human/admin approval")
            if _is_codex_actor(self.approved_by):
                raise ValueError("Codex cannot approve governance policy activation")
        return self


class AgentCapabilityGrant(RuntimeAgentSchema):
    grant_id: str
    policy_id: str
    agent_id: str
    allowed_tool_names: list[str] = Field(default_factory=list)
    allowed_tool_categories: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    max_autonomy_level: AutonomyLevel = "execute_safe_tools"
    status: AgentGrantStatus = "pending"
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_human_approved_active_grant(self) -> AgentCapabilityGrant:
        if self.status == "active":
            if not self.approved_by or self.approved_at is None:
                raise ValueError("active capability grants require human/admin approval")
            if _is_codex_actor(self.approved_by):
                raise ValueError("Codex cannot approve capability grants")
        return self


class AgentAutonomyBudget(RuntimeAgentSchema):
    budget_id: str
    subject_type: AgentBudgetSubjectType
    subject_id: str
    period_start: datetime
    period_end: datetime
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_runtime_minutes: float | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_external_writes: int | None = Field(default=None, ge=0)
    max_artifact_writes: int | None = Field(default=None, ge=0)
    consumed_tool_calls: int = Field(default=0, ge=0)
    consumed_runtime_minutes: float = Field(default=0, ge=0)
    consumed_cost_usd: float = Field(default=0, ge=0)
    consumed_external_writes: int = Field(default=0, ge=0)
    consumed_artifact_writes: int = Field(default=0, ge=0)
    approved_by: str
    approved_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_valid_budget(self) -> AgentAutonomyBudget:
        if self.period_end <= self.period_start:
            raise ValueError("budget period_end must be after period_start")
        if _is_codex_actor(self.approved_by):
            raise ValueError("Codex cannot approve autonomy budgets")
        return self


class AgentRiskProfile(RuntimeAgentSchema):
    agent_id: str
    risk_level: RiskLevel
    risk_score: float = Field(ge=0, le=1)
    factors: list[str] = Field(default_factory=list)
    reliability_score: float | None = Field(default=None, ge=0, le=1)
    guardrail_failure_rate: float | None = Field(default=None, ge=0, le=1)
    human_override_rate: float | None = Field(default=None, ge=0, le=1)
    policy_drift_detected: bool = False
    pause_recommended: bool = False
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCertification(RuntimeAgentSchema):
    certification_id: str
    agent_id: str
    policy_id: str
    certified_autonomy_level: AutonomyLevel
    certified_capabilities: list[str] = Field(default_factory=list)
    status: AgentCertificationStatus = "pending"
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    certified_by: str | None = None
    certified_at: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_independent_certification(self) -> AgentCertification:
        if self.status == "certified":
            if not self.certified_by or self.certified_at is None:
                raise ValueError("certified agents require human/admin certification")
            if _is_codex_actor(self.certified_by) or self.certified_by == self.agent_id:
                raise ValueError("Codex cannot self-certify agents")
            if not self.evidence_artifact_ids:
                raise ValueError("agent certification requires evidence artifacts")
        return self


class AgentRunControl(RuntimeAgentSchema):
    control_id: str
    agent_id: str
    status: AgentRunStatus
    reason: str
    updated_by: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentIncident(RuntimeAgentSchema):
    incident_id: str
    agent_id: str
    severity: AgentIncidentSeverity
    status: AgentIncidentStatus = "open"
    title: str
    summary: str
    guardrail_failure: bool = False
    policy_violation_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detected_by: str = "codex"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPolicyViolation(RuntimeAgentSchema):
    violation_id: str
    agent_id: str
    policy_id: str | None
    code: str
    severity: AgentViolationSeverity
    message: str
    tool_name: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentChangeRequest(RuntimeAgentSchema):
    change_request_id: str
    agent_id: str | None = None
    campaign_id: str | None = None
    policy_id: str | None = None
    change_type: AgentChangeType
    requested_by: str
    reason: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AgentChangeRequestStatus = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_human_approval_for_sensitive_changes(self) -> AgentChangeRequest:
        if self.status == "approved" and self.change_type in HUMAN_ONLY_GOVERNANCE_CHANGES:
            if not self.decided_by:
                raise ValueError("approved governance changes require a human/admin decision")
            if _is_codex_actor(self.decided_by):
                raise ValueError("Codex cannot approve governance changes")
        return self


class AgentApprovalPolicy(RuntimeAgentSchema):
    approval_policy_id: str
    policy_id: str
    approval_type: ApprovalType
    required_role: str = "admin"
    human_only: bool = True
    applies_to_autonomy_levels: list[AutonomyLevel] = Field(default_factory=list)
    min_risk_level: RiskLevel | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentKillSwitch(RuntimeAgentSchema):
    kill_switch_id: str
    scope: KillSwitchScope
    scope_id: str
    active: bool
    reason: str
    activated_by: str
    activated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deactivated_by: str | None = None
    deactivated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_human_deactivation(self) -> AgentKillSwitch:
        if not self.active and self.deactivated_by and _is_codex_actor(self.deactivated_by):
            raise ValueError("Codex cannot disable governance kill switches")
        return self


class AgentGovernanceBoard(RuntimeAgentSchema):
    board_id: str
    org_id: str
    admin_user_ids: list[str]
    pilot_sponsor_user_ids: list[str] = Field(default_factory=list)
    reviewer_user_ids: list[str] = Field(default_factory=list)
    quorum: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_admins_for_board(self) -> AgentGovernanceBoard:
        if not self.admin_user_ids:
            raise ValueError("governance board requires at least one admin")
        if self.quorum > len(set(self.admin_user_ids + self.reviewer_user_ids)):
            raise ValueError("governance board quorum exceeds available decision makers")
        return self


class AgentGovernanceDecision(BaseModel):
    agent_id: str
    status: GovernanceDecisionStatus
    reasons: list[str] = Field(default_factory=list)
    required_approvals: list[ApprovalType] = Field(default_factory=list)
    violations: list[AgentPolicyViolation] = Field(default_factory=list)
    boundary_proof: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


class AgentPerformanceMetrics(BaseModel):
    agent_id: str
    run_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    reliability_score: float = Field(ge=0, le=1)
    guardrail_failure_rate: float = Field(ge=0, le=1)
    human_override_rate: float = Field(ge=0, le=1)
    average_runtime_minutes: float = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentGovernanceReport(RuntimeAgentSchema):
    report_id: str
    org_id: str
    audience: Literal["admin", "pilot_sponsor"]
    generated_at: datetime
    generated_by: str
    policies: list[AgentGovernancePolicy] = Field(default_factory=list)
    grants: list[AgentCapabilityGrant] = Field(default_factory=list)
    budgets: list[AgentAutonomyBudget] = Field(default_factory=list)
    certifications: list[AgentCertification] = Field(default_factory=list)
    run_controls: list[AgentRunControl] = Field(default_factory=list)
    incidents: list[AgentIncident] = Field(default_factory=list)
    violations: list[AgentPolicyViolation] = Field(default_factory=list)
    change_requests: list[AgentChangeRequest] = Field(default_factory=list)
    kill_switches: list[AgentKillSwitch] = Field(default_factory=list)
    performance_metrics: list[AgentPerformanceMetrics] = Field(default_factory=list)
    boundary_proofs: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    disclaimers: list[str] = Field(default_factory=lambda: GOVERNANCE_REPORT_DISCLAIMERS.copy())


GOVERNANCE_REPORT_DISCLAIMERS = [
    "Governance reports are administrative control artifacts.",
    "Codex output is not biomedical evidence.",
    "No medical advice.",
    "No lab protocols.",
    "No synthesis instructions.",
    "No dosing or patient guidance.",
    "Incidents, guardrail failures, and policy violations must not be hidden.",
]


class AgentGovernanceController:
    """Deterministic V2.6 governance checks for Codex runtime agents."""

    def evaluate_action(
        self,
        *,
        agent_id: str,
        policy: AgentGovernancePolicy,
        grants: list[AgentCapabilityGrant],
        tool: RuntimeToolSpec,
        autonomy_level: AutonomyLevel,
        budget: AgentAutonomyBudget | None = None,
        risk_profile: AgentRiskProfile | None = None,
        run_control: AgentRunControl | None = None,
        kill_switches: list[AgentKillSwitch] | None = None,
        approvals: set[ApprovalType] | list[ApprovalType] | None = None,
        campaign_id: str | None = None,
        usage: dict[str, float | int] | None = None,
        now: datetime | None = None,
    ) -> AgentGovernanceDecision:
        current_time = now or datetime.now(UTC)
        approved = set(approvals or [])
        reasons: list[str] = []
        required_approvals: list[ApprovalType] = []
        violations: list[AgentPolicyViolation] = []

        if policy.status != "active":
            violations.append(
                _violation(agent_id, policy.policy_id, "inactive_policy", "Policy is not active.")
            )
        if policy.allowed_agent_ids and agent_id not in policy.allowed_agent_ids:
            violations.append(
                _violation(
                    agent_id,
                    policy.policy_id,
                    "agent_not_allowed",
                    "Agent is outside policy allowlist.",
                )
            )
        if _autonomy_gt(autonomy_level, policy.max_autonomy_level):
            required_approvals.append("codex_full_auto")
            violations.append(
                _violation(
                    agent_id,
                    policy.policy_id,
                    "autonomy_exceeds_policy",
                    "Requested autonomy exceeds governance policy.",
                )
            )

        active_grants = [
            grant
            for grant in grants
            if grant.agent_id == agent_id
            and grant.policy_id == policy.policy_id
            and grant.status == "active"
            and (grant.expires_at is None or grant.expires_at >= current_time)
        ]
        matching_grants = [
            grant
            for grant in active_grants
            if _grant_allows_tool(grant, tool)
            and not _autonomy_gt(autonomy_level, grant.max_autonomy_level)
        ]
        if not matching_grants:
            violations.append(
                _violation(
                    agent_id,
                    policy.policy_id,
                    "capability_not_granted",
                    f"Agent is not granted capability for {tool.tool_name}.",
                    tool_name=tool.tool_name,
                )
            )

        if not _policy_allows_tool(policy, tool):
            violations.append(
                _violation(
                    agent_id,
                    policy.policy_id,
                    "tool_outside_policy_boundary",
                    f"Tool is outside approved policy boundary: {tool.tool_name}.",
                    tool_name=tool.tool_name,
                )
            )

        approval_type = approval_type_for_tool(tool)
        if approval_type is not None and (
            approval_type in policy.approval_required_for
            or approval_type in policy.human_only_approval_types
        ):
            if approval_type not in approved:
                required_approvals.append(approval_type)
                reasons.append(f"{approval_type} approval is required.")

        if budget is not None:
            budget_violations = _budget_violations(budget, tool, usage or {})
            for code, message in budget_violations:
                violations.append(
                    _violation(agent_id, policy.policy_id, code, message, tool_name=tool.tool_name)
                )
        if risk_profile is not None:
            if risk_profile.policy_drift_detected:
                violations.append(
                    _violation(
                        agent_id,
                        policy.policy_id,
                        "policy_drift_detected",
                        "Policy, prompt, or tool drift requires investigation.",
                    )
                )
            if risk_profile.pause_recommended or risk_profile.risk_level == "critical":
                violations.append(
                    _violation(
                        agent_id,
                        policy.policy_id,
                        "risk_pause_required",
                        "Agent risk profile requires pause or disablement.",
                    )
                )
        if run_control is not None and run_control.status != "active":
            violations.append(
                _violation(
                    agent_id,
                    policy.policy_id,
                    f"agent_{run_control.status}",
                    f"Agent run control is {run_control.status}: {run_control.reason}",
                )
            )
        for switch in kill_switches or []:
            if switch.active and _kill_switch_applies(
                switch,
                agent_id=agent_id,
                org_id=policy.org_id,
                campaign_id=campaign_id,
            ):
                violations.append(
                    _violation(
                        agent_id,
                        policy.policy_id,
                        "kill_switch_active",
                        f"Kill switch {switch.kill_switch_id} is active.",
                    )
                )

        boundary_proof = {
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "policy_hash": policy.policy_hash or policy_fingerprint(policy),
            "agent_id": agent_id,
            "autonomy_level": autonomy_level,
            "tool_name": tool.tool_name,
            "tool_category": tool.category,
            "tool_side_effect_level": tool.side_effect_level,
            "tool_required_permissions": tool.required_permissions,
            "campaign_id": campaign_id,
            "matching_grant_ids": [grant.grant_id for grant in matching_grants],
            "approved_approval_types": sorted(approved),
            "required_approval_types": sorted(set(required_approvals)),
        }
        if violations:
            return AgentGovernanceDecision(
                agent_id=agent_id,
                status="blocked",
                reasons=reasons or [violation.message for violation in violations],
                required_approvals=sorted(set(required_approvals)),
                violations=violations,
                boundary_proof=boundary_proof,
            )
        if required_approvals:
            return AgentGovernanceDecision(
                agent_id=agent_id,
                status="requires_approval",
                reasons=reasons,
                required_approvals=sorted(set(required_approvals)),
                boundary_proof=boundary_proof,
            )
        return AgentGovernanceDecision(
            agent_id=agent_id,
            status="allowed",
            reasons=["Action remains inside approved governance boundaries."],
            boundary_proof=boundary_proof,
        )

    def approve_change_request(
        self,
        request: AgentChangeRequest,
        *,
        decided_by: str,
        approved: bool,
        rationale: str,
        decided_at: datetime | None = None,
    ) -> AgentChangeRequest:
        if request.status != "pending":
            raise AgentGovernanceError(
                f"Only pending governance change requests can be decided; got {request.status}."
            )
        if approved and request.change_type in HUMAN_ONLY_GOVERNANCE_CHANGES:
            if _is_codex_actor(decided_by):
                raise AgentGovernanceError(
                    "Codex cannot approve autonomy increases, policy overrides, "
                    "policy changes, certifications, grants, budgets, or kill-switch disablement."
                )
        return request.model_copy(
            update={
                "status": "approved" if approved else "rejected",
                "decided_by": decided_by,
                "decided_at": decided_at or datetime.now(UTC),
                "decision_rationale": rationale,
            }
        )

    def detect_drift(
        self,
        *,
        agent_id: str,
        policy: AgentGovernancePolicy,
        observed_prompt_hashes: dict[str, str] | None = None,
        observed_tool_manifest_hashes: dict[str, str] | None = None,
    ) -> list[AgentPolicyViolation]:
        violations: list[AgentPolicyViolation] = []
        for prompt_id, expected_hash in policy.prompt_hashes.items():
            if (observed_prompt_hashes or {}).get(prompt_id) != expected_hash:
                violations.append(
                    _violation(
                        agent_id,
                        policy.policy_id,
                        "prompt_drift",
                        f"Prompt drift detected for {prompt_id}.",
                    )
                )
        for tool_name, expected_hash in policy.tool_manifest_hashes.items():
            if (observed_tool_manifest_hashes or {}).get(tool_name) != expected_hash:
                violations.append(
                    _violation(
                        agent_id,
                        policy.policy_id,
                        "tool_manifest_drift",
                        f"Tool manifest drift detected for {tool_name}.",
                        tool_name=tool_name,
                    )
                )
        return violations

    def certify_agent(
        self,
        certification: AgentCertification,
        *,
        certified_by: str,
        evidence_artifact_ids: list[str],
        certified_at: datetime | None = None,
    ) -> AgentCertification:
        if _is_codex_actor(certified_by) or certified_by == certification.agent_id:
            raise AgentGovernanceError("Codex cannot self-certify agents.")
        return certification.model_copy(
            update={
                "status": "certified",
                "certified_by": certified_by,
                "certified_at": certified_at or datetime.now(UTC),
                "evidence_artifact_ids": evidence_artifact_ids,
            }
        )

    def hide_incident(self, incident: AgentIncident, *, actor: str) -> None:
        raise AgentGovernanceError(
            f"Incidents and guardrail failures cannot be hidden by {actor}: "
            f"{incident.incident_id}."
        )


def build_agent_governance_report(
    *,
    org_id: str,
    audience: Literal["admin", "pilot_sponsor"],
    generated_by: str,
    policies: list[AgentGovernancePolicy],
    grants: list[AgentCapabilityGrant],
    budgets: list[AgentAutonomyBudget],
    certifications: list[AgentCertification],
    run_controls: list[AgentRunControl],
    incidents: list[AgentIncident],
    violations: list[AgentPolicyViolation],
    change_requests: list[AgentChangeRequest],
    kill_switches: list[AgentKillSwitch],
    performance_metrics: list[AgentPerformanceMetrics],
    decisions: list[AgentGovernanceDecision] | None = None,
    generated_at: datetime | None = None,
) -> AgentGovernanceReport:
    all_violations = list(violations)
    for decision in decisions or []:
        all_violations.extend(decision.violations)
    guardrail_failures = sum(1 for incident in incidents if incident.guardrail_failure)
    guardrail_failures += sum(
        1 for violation in all_violations if "guardrail" in violation.code.lower()
    )
    summary = {
        "policy_count": len(policies),
        "active_policy_count": sum(1 for policy in policies if policy.status == "active"),
        "grant_count": len(grants),
        "active_grant_count": sum(1 for grant in grants if grant.status == "active"),
        "certified_agent_count": sum(
            1 for certification in certifications if certification.status == "certified"
        ),
        "paused_or_disabled_agents": sum(
            1 for control in run_controls if control.status in {"paused", "disabled"}
        ),
        "open_incident_count": sum(1 for incident in incidents if incident.status != "resolved"),
        "guardrail_failure_count": guardrail_failures,
        "policy_violation_count": len(all_violations),
        "pending_change_request_count": sum(
            1 for request in change_requests if request.status == "pending"
        ),
        "active_kill_switch_count": sum(1 for switch in kill_switches if switch.active),
        "blocked_action_count": sum(
            1 for decision in decisions or [] if decision.status == "blocked"
        ),
        "approval_required_action_count": sum(
            1 for decision in decisions or [] if decision.status == "requires_approval"
        ),
    }
    return AgentGovernanceReport(
        report_id=f"agent-governance-report-{uuid4().hex[:12]}",
        org_id=org_id,
        audience=audience,
        generated_at=generated_at or datetime.now(UTC),
        generated_by=generated_by,
        policies=policies,
        grants=grants,
        budgets=budgets,
        certifications=certifications,
        run_controls=run_controls,
        incidents=incidents,
        violations=all_violations,
        change_requests=change_requests,
        kill_switches=kill_switches,
        performance_metrics=performance_metrics,
        boundary_proofs=[decision.boundary_proof for decision in decisions or []],
        summary=summary,
    )


def build_agent_governance_report_markdown(report: AgentGovernanceReport) -> str:
    incident_lines = [
        f"- {incident.incident_id}: {incident.severity} {incident.status} "
        f"guardrail_failure={incident.guardrail_failure}; {incident.title}"
        for incident in report.incidents
    ] or ["- None."]
    violation_lines = [
        f"- {violation.violation_id}: {violation.severity} {violation.code}; "
        f"{violation.message}"
        for violation in report.violations
    ] or ["- None."]
    proof_lines = [
        f"- {proof.get('agent_id')} used `{proof.get('tool_name')}` under "
        f"policy `{proof.get('policy_id')}` with grants "
        f"{proof.get('matching_grant_ids', [])}."
        for proof in report.boundary_proofs
    ] or ["- None."]
    lines = [
        "# Agent Governance Report",
        "",
        *[f"- {item}" for item in report.disclaimers],
        "",
        "## Summary",
        *[f"- {key}: {value}" for key, value in sorted(report.summary.items())],
        "",
        "## Incidents",
        *incident_lines,
        "",
        "## Policy Violations",
        *violation_lines,
        "",
        "## Boundary Proofs",
        *proof_lines,
        "",
    ]
    return "\n".join(lines)


def policy_fingerprint(policy: AgentGovernancePolicy) -> str:
    payload = policy.model_dump(
        mode="json",
        exclude={"policy_hash", "metadata", "approved_at", "created_at"},
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _budget_violations(
    budget: AgentAutonomyBudget,
    tool: RuntimeToolSpec,
    usage: dict[str, float | int],
) -> list[tuple[str, str]]:
    tool_calls = int(usage.get("tool_calls", 1))
    runtime_minutes = float(usage.get("runtime_minutes", 0))
    cost_usd = float(usage.get("cost_usd", 0))
    external_writes = int(
        usage.get("external_writes", 1 if tool.side_effect_level == "external_write" else 0)
    )
    artifact_writes = int(
        usage.get("artifact_writes", 1 if tool.side_effect_level == "artifact_write" else 0)
    )
    checks = [
        (
            "budget_tool_calls_exceeded",
            budget.max_tool_calls,
            budget.consumed_tool_calls,
            tool_calls,
            "tool-call budget exceeded",
        ),
        (
            "budget_runtime_exceeded",
            budget.max_runtime_minutes,
            budget.consumed_runtime_minutes,
            runtime_minutes,
            "runtime budget exceeded",
        ),
        (
            "budget_cost_exceeded",
            budget.max_cost_usd,
            budget.consumed_cost_usd,
            cost_usd,
            "cost budget exceeded",
        ),
        (
            "budget_external_writes_exceeded",
            budget.max_external_writes,
            budget.consumed_external_writes,
            external_writes,
            "external-write budget exceeded",
        ),
        (
            "budget_artifact_writes_exceeded",
            budget.max_artifact_writes,
            budget.consumed_artifact_writes,
            artifact_writes,
            "artifact-write budget exceeded",
        ),
    ]
    return [
        (code, message)
        for code, limit, consumed, requested, message in checks
        if limit is not None and consumed + requested > limit
    ]


def _grant_allows_tool(grant: AgentCapabilityGrant, tool: RuntimeToolSpec) -> bool:
    if grant.allowed_tool_names and tool.tool_name not in grant.allowed_tool_names:
        return False
    if grant.allowed_tool_categories and tool.category not in grant.allowed_tool_categories:
        return False
    if grant.allowed_permissions and not set(tool.required_permissions).issubset(
        set(grant.allowed_permissions)
    ):
        return False
    return bool(
        grant.allowed_tool_names or grant.allowed_tool_categories or grant.allowed_permissions
    )


def _policy_allows_tool(policy: AgentGovernancePolicy, tool: RuntimeToolSpec) -> bool:
    if policy.allowed_tool_names and tool.tool_name not in policy.allowed_tool_names:
        return False
    if policy.allowed_tool_categories and tool.category not in policy.allowed_tool_categories:
        return False
    if policy.allowed_permissions and not set(tool.required_permissions).issubset(
        set(policy.allowed_permissions)
    ):
        return False
    return True


def _kill_switch_applies(
    switch: AgentKillSwitch,
    *,
    agent_id: str,
    org_id: str,
    campaign_id: str | None,
) -> bool:
    return (
        (switch.scope == "agent" and switch.scope_id == agent_id)
        or (switch.scope == "org" and switch.scope_id == org_id)
        or (switch.scope == "campaign" and switch.scope_id == campaign_id)
    )


def _autonomy_gt(left: AutonomyLevel, right: AutonomyLevel) -> bool:
    return AUTONOMY_ORDER[left] > AUTONOMY_ORDER[right]


def _violation(
    agent_id: str,
    policy_id: str | None,
    code: str,
    message: str,
    *,
    tool_name: str | None = None,
) -> AgentPolicyViolation:
    return AgentPolicyViolation(
        violation_id=f"agent-policy-violation-{uuid4().hex[:12]}",
        agent_id=agent_id,
        policy_id=policy_id,
        code=code,
        severity="block",
        message=message,
        tool_name=tool_name,
    )


def _is_codex_actor(actor: str) -> bool:
    return actor.strip().lower() in {"codex", "codex_cli", "codex-runtime-agent"}


__all__ = [
    "AgentApprovalPolicy",
    "AgentAutonomyBudget",
    "AgentCapabilityGrant",
    "AgentCertification",
    "AgentChangeRequest",
    "AgentGovernanceBoard",
    "AgentGovernanceController",
    "AgentGovernanceDecision",
    "AgentGovernanceError",
    "AgentGovernancePolicy",
    "AgentGovernanceReport",
    "AgentIncident",
    "AgentKillSwitch",
    "AgentPerformanceMetrics",
    "AgentPolicyViolation",
    "AgentRiskProfile",
    "AgentRunControl",
    "GOVERNANCE_REPORT_DISCLAIMERS",
    "build_agent_governance_report",
    "build_agent_governance_report_markdown",
    "policy_fingerprint",
]
