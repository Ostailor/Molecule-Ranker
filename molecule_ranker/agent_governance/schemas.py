from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AgentGovernanceAutonomyLevel = Literal[
    "observe_only",
    "suggest_only",
    "execute_safe_tools",
    "execute_with_approval",
    "supervised_auto",
    "disabled",
]
AgentType = Literal[
    "runtime_agent",
    "subagent",
    "campaign_copilot",
    "tool_agent",
    "codex_worker",
]
AgentCapabilityScopeType = Literal[
    "org",
    "project",
    "campaign",
    "workflow",
    "tool_package",
]
AgentCapabilityGrantStatus = Literal[
    "active",
    "expired",
    "revoked",
    "pending_approval",
]
AgentAutonomyBudgetPeriod = Literal[
    "per_session",
    "daily",
    "weekly",
    "monthly",
    "campaign_lifetime",
]
AgentRiskLevel = Literal["low", "medium", "high", "critical"]
AgentCertificationType = Literal[
    "tool_use",
    "guardrail",
    "autonomy_level",
    "campaign_copilot",
    "external_integration",
    "subagent_role",
    "release_gate",
]
AgentRunControlType = Literal[
    "pause",
    "resume",
    "disable",
    "enable",
    "restrict_autonomy",
    "require_approval_all_actions",
    "kill_switch",
]
AgentIncidentSeverity = Literal["low", "medium", "high", "critical"]
AgentIncidentType = Literal[
    "guardrail_failure",
    "policy_violation",
    "unauthorized_tool_attempt",
    "approval_bypass_attempt",
    "external_write_violation",
    "hallucinated_artifact",
    "unsupported_scientific_claim",
    "secret_exposure_attempt",
    "repeated_failure",
    "unsafe_repair_attempt",
    "unknown",
]
AgentIncidentStatus = Literal[
    "open",
    "triaged",
    "investigating",
    "mitigated",
    "resolved",
    "false_positive",
]


class AgentGovernanceSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class AgentGovernancePolicy(AgentGovernanceSchema):
    policy_id: str
    org_id: str | None
    project_id: str | None
    policy_name: str
    policy_version: str
    applies_to_roles: list[str] = Field(default_factory=list)
    applies_to_agents: list[str] = Field(default_factory=list)
    max_autonomy_level: AgentGovernanceAutonomyLevel
    allowed_tool_categories: list[str] = Field(default_factory=list)
    denied_tool_categories: list[str] = Field(default_factory=list)
    allowed_side_effect_levels: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    budget_policy_id: str | None
    guardrail_profile: str
    incident_policy_id: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCapabilityGrant(AgentGovernanceSchema):
    grant_id: str
    agent_id: str
    agent_type: AgentType
    granted_capability: str
    scope_type: AgentCapabilityScopeType
    scope_id: str | None
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    status: AgentCapabilityGrantStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAutonomyBudget(AgentGovernanceSchema):
    budget_id: str
    org_id: str | None
    project_id: str | None
    campaign_id: str | None
    agent_id: str | None
    period: AgentAutonomyBudgetPeriod
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_codex_tasks: int | None = Field(default=None, ge=0)
    max_runtime_minutes: float | None = Field(default=None, ge=0)
    max_artifact_writes: int | None = Field(default=None, ge=0)
    max_db_writes: int | None = Field(default=None, ge=0)
    max_external_reads: int | None = Field(default=None, ge=0)
    max_external_writes: int | None = Field(default=None, ge=0)
    max_generation_jobs: int | None = Field(default=None, ge=0)
    max_docking_jobs: int | None = Field(default=None, ge=0)
    max_model_training_jobs: int | None = Field(default=None, ge=0)
    max_campaign_replans: int | None = Field(default=None, ge=0)
    max_cost_units: float | None = Field(default=None, ge=0)
    current_usage: dict[str, Any] = Field(default_factory=dict)
    reset_at: datetime | None
    enabled: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRiskProfile(AgentGovernanceSchema):
    risk_profile_id: str
    agent_id: str
    risk_level: AgentRiskLevel
    risk_factors: list[str] = Field(default_factory=list)
    recent_guardrail_failures: int = Field(ge=0)
    recent_policy_violations: int = Field(ge=0)
    recent_failed_repairs: int = Field(ge=0)
    recent_human_overrides: int = Field(ge=0)
    unsafe_action_attempts: int = Field(ge=0)
    external_write_attempts: int = Field(ge=0)
    approval_rejection_rate: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    computed_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCertification(AgentGovernanceSchema):
    certification_id: str
    agent_id: str
    certification_type: AgentCertificationType
    certified_autonomy_level: AgentGovernanceAutonomyLevel
    evaluation_artifact_ids: list[str] = Field(default_factory=list)
    passed: bool
    score: float = Field(ge=0, le=1)
    certified_by: str
    certified_at: datetime
    expires_at: datetime | None
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunControl(AgentGovernanceSchema):
    control_id: str
    org_id: str | None
    project_id: str | None
    agent_id: str | None
    control_type: AgentRunControlType
    reason: str
    applied_by: str
    applied_at: datetime
    expires_at: datetime | None
    active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentIncident(AgentGovernanceSchema):
    incident_id: str
    org_id: str | None
    project_id: str | None
    agent_id: str | None
    session_id: str | None
    severity: AgentIncidentSeverity
    incident_type: AgentIncidentType
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    tool_usage_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    status: AgentIncidentStatus
    opened_at: datetime
    resolved_at: datetime | None
    assigned_to: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPolicyViolation(AgentGovernanceSchema):
    violation_id: str
    policy_id: str
    agent_id: str | None
    session_id: str | None
    violation_type: str
    blocked: bool
    summary: str
    detected_at: datetime
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentGovernanceReport(AgentGovernanceSchema):
    report_id: str
    org_id: str | None
    project_id: str | None
    period_start: datetime
    period_end: datetime
    agent_count: int = Field(ge=0)
    active_agent_count: int = Field(ge=0)
    disabled_agent_count: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    total_codex_tasks: int = Field(ge=0)
    guardrail_failures: int = Field(ge=0)
    policy_violations: int = Field(ge=0)
    approval_requests: int = Field(ge=0)
    approval_rejections: int = Field(ge=0)
    incidents_opened: int = Field(ge=0)
    incidents_resolved: int = Field(ge=0)
    budget_violations: int = Field(ge=0)
    top_risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentAutonomyBudget",
    "AgentAutonomyBudgetPeriod",
    "AgentCapabilityGrant",
    "AgentCapabilityGrantStatus",
    "AgentCapabilityScopeType",
    "AgentCertification",
    "AgentCertificationType",
    "AgentGovernanceAutonomyLevel",
    "AgentGovernancePolicy",
    "AgentGovernanceReport",
    "AgentGovernanceSchema",
    "AgentIncident",
    "AgentIncidentSeverity",
    "AgentIncidentStatus",
    "AgentIncidentType",
    "AgentPolicyViolation",
    "AgentRiskLevel",
    "AgentRiskProfile",
    "AgentRunControl",
    "AgentRunControlType",
    "AgentType",
]
