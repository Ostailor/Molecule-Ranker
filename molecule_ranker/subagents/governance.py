from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentTask,
)

GovernanceStatus = Literal["allowed", "blocked", "paused", "requires_human_review"]
RetentionItemType = Literal["session", "transcript"]

HIGH_RISK_TOOLS = {
    "run_sync_write_enabled": "external_write",
    "advance_generated_molecule_to_assay": "generated_molecule_advancement",
    "approve_campaign": "campaign_approval",
    "approve_stage_gate": "stage_gate_decision",
    "delete_artifact": "destructive_action",
}
HUMAN_APPROVAL_ACTIONS = {
    "external_write",
    "generated_molecule_advancement",
    "campaign_approval",
    "stage_gate_decision",
    "destructive_action",
}


class SubagentGovernanceError(ValueError):
    """Raised when a governance policy cannot be applied."""


class SubagentPolicyOverride(BaseModel):
    override_id: str
    action: str
    approved_by: str
    reason: str
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expires_at")
    @classmethod
    def require_timezone_aware_expiration(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must be timezone-aware")
        return value

    def active(self, *, now: datetime | None = None) -> bool:
        current_time = now or _now()
        return self.expires_at is None or self.expires_at >= current_time


class SubagentGovernancePolicy(BaseModel):
    role_permissions: dict[str, list[str]] = Field(default_factory=dict)
    max_concurrent_subagents: int = Field(default=8, ge=1)
    max_codex_tasks_per_session: int = Field(default=32, ge=1)
    max_tool_calls_per_subagent: int = Field(default=16, ge=1)
    budget_limits: dict[str, float] = Field(default_factory=dict)
    approval_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"high": 0.0, "critical": 0.0}
    )
    policy_overrides: list[SubagentPolicyOverride] = Field(default_factory=list)
    session_retention_days: int = Field(default=90, ge=1)
    transcript_retention_days: int = Field(default=30, ge=1)
    human_escalation_rules: list[str] = Field(
        default_factory=lambda: [
            "unresolved_disagreement",
            "external_write",
            "generated_molecule_advancement",
            "campaign_or_stage_gate_decision",
        ]
    )
    incident_flag_rules: list[str] = Field(
        default_factory=lambda: [
            "budget_exceeded",
            "repeated_guardrail_failures",
            "policy_override_used",
        ]
    )
    repeated_guardrail_failure_threshold: int = Field(default=3, ge=1)

    @classmethod
    def default(cls, *, registry: SubagentRegistry | None = None) -> SubagentGovernancePolicy:
        active_registry = registry or SubagentRegistry()
        return cls(
            role_permissions={
                profile.subagent_id: list(profile.required_permissions)
                for profile in active_registry.list_profiles()
            },
            budget_limits={
                "codex_tokens": 200_000.0,
                "tool_cost_usd": 100.0,
                "compute_units": 1_000.0,
            },
        )


class SubagentGovernanceUsage(BaseModel):
    codex_tasks: int = 0
    tool_calls_by_subagent: dict[str, int] = Field(default_factory=dict)
    budget_spend: dict[str, float] = Field(default_factory=dict)
    approvals: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentGovernanceDecision(BaseModel):
    status: GovernanceStatus
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    incident_flags: list[str] = Field(default_factory=list)
    policy_overrides_used: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetentionItem(BaseModel):
    item_id: str
    item_type: RetentionItemType
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class RetentionApplication(BaseModel):
    retained_item_ids: list[str]
    expired_item_ids: list[str]
    expired_counts: dict[str, int]
    applied_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultiAgentGovernance:
    def __init__(
        self,
        *,
        policy: SubagentGovernancePolicy | None = None,
        registry: SubagentRegistry | None = None,
    ) -> None:
        self.registry = registry or SubagentRegistry()
        self.policy = policy or SubagentGovernancePolicy.default(registry=self.registry)

    def evaluate_session(
        self,
        session: MultiAgentSession,
        *,
        usage: SubagentGovernanceUsage | None = None,
        now: datetime | None = None,
    ) -> SubagentGovernanceDecision:
        active_usage = usage or SubagentGovernanceUsage()
        current_time = now or _now()
        reasons: list[str] = []
        required_approvals: list[str] = []
        incident_flags: list[str] = []
        overrides_used: list[str] = []

        if len(session.subagent_ids) > self.policy.max_concurrent_subagents:
            reasons.append("max concurrent subagents exceeded")

        if active_usage.codex_tasks > self.policy.max_codex_tasks_per_session:
            reasons.append("max Codex tasks per session exceeded")

        for subagent_id, call_count in sorted(active_usage.tool_calls_by_subagent.items()):
            if call_count > self.policy.max_tool_calls_per_subagent:
                reasons.append(f"max tool calls exceeded for {subagent_id}")

        budget_violations = _budget_violations(self.policy, active_usage)
        reasons.extend(budget_violations)
        if budget_violations:
            incident_flags.append("budget_exceeded")

        permission_violations = self._permission_violations(session)
        reasons.extend(permission_violations)

        high_risk_without_sentinel = _high_risk_without_sentinel(session)
        if high_risk_without_sentinel:
            reasons.append("high-risk scientific output requires GuardrailSentinel critique")
            required_approvals.append("guardrail_sentinel_critique")

        approval_actions = _approval_actions(session)
        missing_approvals = sorted(approval_actions - active_usage.approvals)
        override_actions = {
            override.action
            for override in self.policy.policy_overrides
            if override.active(now=current_time)
        }
        for action in missing_approvals:
            if action in override_actions:
                overrides_used.extend(
                    override.override_id
                    for override in self.policy.policy_overrides
                    if override.action == action and override.active(now=current_time)
                )
                incident_flags.append("policy_override_used")
                continue
            required_approvals.append(action)

        unresolved_disagreement = _has_unresolved_disagreement(session.consensus)
        if unresolved_disagreement:
            required_approvals.append("human_review_unresolved_disagreement")

        guardrail_failures = _guardrail_failure_count(session)
        if guardrail_failures >= self.policy.repeated_guardrail_failure_threshold:
            incident_flags.append("repeated_guardrail_failures")
            return SubagentGovernanceDecision(
                status="paused",
                allowed=False,
                reasons=[
                    *list(dict.fromkeys(reasons)),
                    "repeated guardrail failures pause session",
                ],
                required_approvals=list(dict.fromkeys(required_approvals)),
                incident_flags=list(dict.fromkeys(incident_flags)),
                policy_overrides_used=list(dict.fromkeys(overrides_used)),
                metadata={"guardrail_failure_count": guardrail_failures},
            )

        reasons = list(dict.fromkeys(reasons))
        required_approvals = list(dict.fromkeys(required_approvals))
        incident_flags = list(dict.fromkeys(incident_flags))
        if reasons:
            status: GovernanceStatus = "blocked"
        elif required_approvals or unresolved_disagreement:
            status = "requires_human_review"
        else:
            status = "allowed"
        return SubagentGovernanceDecision(
            status=status,
            allowed=status == "allowed",
            reasons=reasons,
            required_approvals=required_approvals,
            incident_flags=incident_flags,
            policy_overrides_used=list(dict.fromkeys(overrides_used)),
            metadata={"guardrail_failure_count": guardrail_failures},
        )

    def apply_session_decision(
        self,
        session: MultiAgentSession,
        decision: SubagentGovernanceDecision,
    ) -> MultiAgentSession:
        updated = session.model_copy(deep=True)
        if decision.status == "paused":
            updated.status = "paused"
        elif decision.status == "blocked":
            updated.status = "blocked"
        elif decision.status == "requires_human_review":
            updated.status = "awaiting_human_review"
        updated.metadata = {
            **updated.metadata,
            "governance": decision.model_dump(mode="json"),
        }
        return updated

    def apply_retention(
        self,
        items: list[RetentionItem],
        *,
        now: datetime | None = None,
    ) -> RetentionApplication:
        current_time = now or _now()
        expired: list[str] = []
        retained: list[str] = []
        expired_counts = {"session": 0, "transcript": 0}
        for item in items:
            retention_days = (
                self.policy.session_retention_days
                if item.item_type == "session"
                else self.policy.transcript_retention_days
            )
            if item.created_at < current_time - timedelta(days=retention_days):
                expired.append(item.item_id)
                expired_counts[item.item_type] += 1
            else:
                retained.append(item.item_id)
        return RetentionApplication(
            retained_item_ids=retained,
            expired_item_ids=expired,
            expired_counts=expired_counts,
            applied_at=current_time,
            metadata={
                "session_retention_days": self.policy.session_retention_days,
                "transcript_retention_days": self.policy.transcript_retention_days,
            },
        )

    def permissions_for_subagent(self, subagent_id: str) -> set[str]:
        permissions = self.policy.role_permissions.get(subagent_id)
        if permissions is not None:
            return set(permissions)
        return set(self.registry.require(subagent_id).required_permissions)

    def _permission_violations(self, session: MultiAgentSession) -> list[str]:
        violations: list[str] = []
        for task in session.tasks:
            required_permissions = _task_required_permissions(task)
            if not required_permissions:
                continue
            allowed_permissions = self.permissions_for_subagent(task.assigned_subagent_id)
            unauthorized = sorted(required_permissions - allowed_permissions)
            if unauthorized:
                violations.append(
                    f"{task.assigned_subagent_id} lacks permissions: "
                    + ", ".join(unauthorized)
                )
        return violations


def _task_required_permissions(task: SubagentTask) -> set[str]:
    raw = task.metadata.get("required_permissions", [])
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return set()


def _budget_violations(
    policy: SubagentGovernancePolicy,
    usage: SubagentGovernanceUsage,
) -> list[str]:
    violations: list[str] = []
    for budget_name, limit in sorted(policy.budget_limits.items()):
        spend = float(usage.budget_spend.get(budget_name, 0.0))
        if spend > limit:
            violations.append(f"budget limit exceeded: {budget_name}")
    return violations


def _high_risk_without_sentinel(session: MultiAgentSession) -> bool:
    high_risk_task_ids = {
        task.task_id for task in session.tasks if task.risk_level in {"high", "critical"}
    }
    high_risk_result_ids = {
        result.result_id
        for result in session.results
        if result.task_id in high_risk_task_ids
        or result.status in {"guardrail_failed", "validation_failed"}
        or result.metadata.get("scientific_output_created") is True
    }
    if not high_risk_task_ids and not high_risk_result_ids:
        return False
    return not any(
        critique.critic_subagent_id == "guardrail-sentinel"
        and (
            not high_risk_result_ids
            or critique.target_result_id in high_risk_result_ids
            or critique.metadata.get("required_for_high_risk") is True
        )
        for critique in session.critiques
    )


def _approval_actions(session: MultiAgentSession) -> set[str]:
    actions: set[str] = set()
    goal = session.user_goal.lower()
    if "external write" in goal:
        actions.add("external_write")
    if "generated molecule assay advancement" in goal or "generated molecule advancement" in goal:
        actions.add("generated_molecule_advancement")
    if "campaign approval" in goal or "approve campaign" in goal:
        actions.add("campaign_approval")
    if "stage gate" in goal or "stage-gate" in goal:
        actions.add("stage_gate_decision")
    for task in session.tasks:
        if task.requires_human_approval:
            actions.add("task_human_approval")
        for tool_name in task.allowed_tool_names:
            action = HIGH_RISK_TOOLS.get(tool_name)
            if action in HUMAN_APPROVAL_ACTIONS:
                actions.add(action)
        metadata_actions = task.metadata.get("approval_actions", [])
        if isinstance(metadata_actions, list):
            actions.update(str(item) for item in metadata_actions)
    return actions


def _has_unresolved_disagreement(consensus: list[SubagentConsensus]) -> bool:
    return any(
        item.consensus_status in {"disagreement", "requires_human_review"}
        or item.human_review_required
        for item in consensus
    )


def _guardrail_failure_count(session: MultiAgentSession) -> int:
    critique_failures = sum(1 for critique in session.critiques if _is_guardrail_failure(critique))
    result_failures = sum(1 for result in session.results if result.status == "guardrail_failed")
    finding_failures = sum(len(result.guardrail_findings) for result in session.results)
    return critique_failures + result_failures + finding_failures


def _is_guardrail_failure(critique: SubagentCritique) -> bool:
    return (
        not critique.passed
        and (
            critique.critic_subagent_id == "guardrail-sentinel"
            or critique.critique_type == "scientific_guardrail"
            or critique.metadata.get("non_overridable") is True
        )
    )


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "GovernanceStatus",
    "HUMAN_APPROVAL_ACTIONS",
    "HIGH_RISK_TOOLS",
    "MultiAgentGovernance",
    "RetentionApplication",
    "RetentionItem",
    "RetentionItemType",
    "SubagentGovernanceDecision",
    "SubagentGovernanceError",
    "SubagentGovernancePolicy",
    "SubagentGovernanceUsage",
    "SubagentPolicyOverride",
]
