from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.agent_repair.schemas import RepairAction, RepairPlan

RepairPolicyDecisionStatus = Literal["allowed", "approval_required", "blocked"]

SAFE_AUTONOMY_LEVELS = {
    "execute_safe_repairs",
    "execute_safe_tools",
    "execute_with_approval",
    "supervised_auto",
    "full_auto_restricted",
}
APPROVAL_AUTONOMY_LEVELS = {"execute_with_approval", "supervised_auto"}
SAFE_AUTOMATIC_ACTIONS = {
    "retry_external_read",
    "revalidate_artifact",
    "regenerate_artifact",
    "rebuild_index",
    "retry_codex_with_schema",
    "run_regression_check",
    "mark_skipped",
}
APPROVAL_REQUIRED_ACTIONS = {
    "rollback_artifact",
    "rollback_job",
    "quarantine_artifact",
}
EXPENSIVE_JOB_TERMS = {
    "generation",
    "large_generation",
    "expensive_generation",
    "docking",
    "external_sync",
    "structure_docking",
}
APPROVAL_ARTIFACT_TYPES = {
    "source_artifact",
    "review_packet",
    "support_bundle",
    "portfolio_selection",
    "campaign_plan",
}
BLOCKED_TERMS = {
    "approve_own_external_write",
    "bypass_approval",
    "invent_missing_evidence",
    "invent_evidence",
    "generated_molecule_evidence",
    "edit_assay_results",
    "edit_source_records",
    "edit_source_artifact",
    "fake_citation",
    "fabricate_citation",
    "forbidden_tool",
    "hide_failed_qc",
    "bypass_rbac",
    "approve_stage_gate",
    "remove_guardrail_failure",
    "direct_score_edit",
    "change_score_directly",
    "change_autonomy_level",
    "alter_benchmark_outcome",
}
BLOCKED_TOOL_NAMES = {"import_assay_results", "link_assay_results"}
RISKY_SIDE_EFFECTS = {"external_write", "destructive"}
WRITE_SIDE_EFFECTS = {"artifact_write", "db_write", "external_write", "destructive"}
REVIEW_ROLES = {"admin", "repair_approver", "tool_admin", "project_owner"}


class RepairPolicyContext(BaseModel):
    job_type: str | None = None
    failure_category: str | None = None
    autonomy_level: str = "suggest_only"
    user_role: str | None = None
    user_roles: set[str] = Field(default_factory=set)
    project_policy: dict[str, Any] = Field(default_factory=dict)
    org_policy: dict[str, Any] = Field(default_factory=dict)
    external_system_mode: str | None = None
    scientific_risk_level: str = "low"
    artifact_type: str | None = None
    policy_context: dict[str, Any] = Field(default_factory=dict)
    guardrail_category: str | None = None


class RepairPolicyDecision(BaseModel):
    status: RepairPolicyDecisionStatus
    reason: str
    required_approvals: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"

    @property
    def approval_required(self) -> bool:
        return self.status == "approval_required"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


class RepairPolicyEngine:
    """Deterministic repair policy evaluator."""

    def evaluate_action(
        self,
        action: RepairAction | Mapping[str, Any],
        *,
        context: RepairPolicyContext | Mapping[str, Any] | None = None,
    ) -> RepairPolicyDecision:
        parsed_action = _parse_action(action)
        parsed_context = _parse_context(context)
        blocked = _blocked_reasons(parsed_action, parsed_context)
        if blocked:
            return RepairPolicyDecision(
                status="blocked",
                reason="Repair action is blocked by repair policy.",
                blocked_reasons=blocked,
                metadata=_decision_metadata(parsed_action, parsed_context),
            )

        approvals = _approval_requirements(parsed_action, parsed_context)
        if approvals:
            return RepairPolicyDecision(
                status="approval_required",
                reason="Repair action requires human approval.",
                required_approvals=approvals,
                metadata=_decision_metadata(parsed_action, parsed_context),
            )

        safe = _is_default_safe_action(parsed_action, parsed_context)
        if safe:
            return RepairPolicyDecision(
                status="allowed",
                reason="Repair action is allowed as a safe automatic repair.",
                metadata=_decision_metadata(parsed_action, parsed_context),
            )

        return RepairPolicyDecision(
            status="approval_required",
            reason="Repair action is not on the automatic safe list.",
            required_approvals=["repair_action_review"],
            metadata=_decision_metadata(parsed_action, parsed_context),
        )

    def evaluate_plan(
        self,
        plan: RepairPlan | Mapping[str, Any],
        *,
        context: RepairPolicyContext | Mapping[str, Any] | None = None,
    ) -> RepairPolicyDecision:
        parsed_plan = _parse_plan(plan)
        decisions = [
            self.evaluate_action(action, context=context) for action in parsed_plan.actions
        ]
        blocked = [
            reason for decision in decisions for reason in decision.blocked_reasons
        ]
        approvals = [
            approval
            for decision in decisions
            for approval in decision.required_approvals
        ]
        if blocked:
            return RepairPolicyDecision(
                status="blocked",
                reason="Repair plan is blocked by repair policy.",
                required_approvals=sorted(set(approvals)),
                blocked_reasons=sorted(set(blocked)),
                metadata={"repair_plan_id": parsed_plan.repair_plan_id},
            )
        if approvals:
            return RepairPolicyDecision(
                status="approval_required",
                reason="Repair plan requires human approval.",
                required_approvals=sorted(set(approvals)),
                metadata={"repair_plan_id": parsed_plan.repair_plan_id},
            )
        return RepairPolicyDecision(
            status="allowed",
            reason="Repair plan is allowed by repair policy.",
            metadata={"repair_plan_id": parsed_plan.repair_plan_id},
        )

    def requires_approval(
        self,
        action: RepairAction | Mapping[str, Any],
        context: RepairPolicyContext | Mapping[str, Any] | None = None,
    ) -> bool:
        return self.evaluate_action(action, context=context).approval_required

    def is_allowed(
        self,
        action: RepairAction | Mapping[str, Any],
        context: RepairPolicyContext | Mapping[str, Any] | None = None,
    ) -> bool:
        return self.evaluate_action(action, context=context).allowed

    def is_blocked(
        self,
        action: RepairAction | Mapping[str, Any],
        context: RepairPolicyContext | Mapping[str, Any] | None = None,
    ) -> bool:
        return self.evaluate_action(action, context=context).blocked


def evaluate_repair_action(
    action: RepairAction | Mapping[str, Any],
    *,
    context: RepairPolicyContext | Mapping[str, Any] | None = None,
) -> RepairPolicyDecision:
    return RepairPolicyEngine().evaluate_action(action, context=context)


def evaluate_repair_plan(
    plan: RepairPlan | Mapping[str, Any],
    *,
    context: RepairPolicyContext | Mapping[str, Any] | None = None,
) -> RepairPolicyDecision:
    return RepairPolicyEngine().evaluate_plan(plan, context=context)


def _blocked_reasons(
    action: RepairAction,
    context: RepairPolicyContext,
) -> list[str]:
    reasons: list[str] = []
    payload = _action_payload_text(action, context)
    if action.tool_name in BLOCKED_TOOL_NAMES:
        reasons.append("Repair cannot edit assay results or source records.")
    if action.side_effect_level == "external_write" and not _has_review_role(context):
        reasons.append("External write without approval is blocked.")
    if action.side_effect_level == "destructive":
        reasons.append("Destructive filesystem action is blocked by default.")
    if context.project_policy.get("rbac_bypass") is True or "bypass_rbac" in payload:
        reasons.append("Repair cannot bypass RBAC.")
    for term in BLOCKED_TERMS:
        if term in payload:
            reasons.append(_blocked_term_reason(term))
    if _contains_key(
        action.tool_args,
        {
            "assay_result",
            "raw_assay_data",
            "scientific_score",
            "score",
            "source_artifact",
            "source_record",
        },
    ):
        reasons.append("Repair cannot edit assay results or source records.")
    if _contains_key(action.tool_args, {"citation", "citations"}) and "fake" in payload:
        reasons.append("Repair cannot create fake citations.")
    return sorted(set(reasons))


def _approval_requirements(
    action: RepairAction,
    context: RepairPolicyContext,
) -> list[str]:
    approvals: list[str] = []
    payload = _action_payload_text(action, context)
    if (
        context.autonomy_level in {"observe_only", "suggest_only"}
        and action.side_effect_level in WRITE_SIDE_EFFECTS
    ):
        approvals.append("autonomy_upgrade")
    if (
        context.autonomy_level not in SAFE_AUTONOMY_LEVELS
        and action.action_type in SAFE_AUTOMATIC_ACTIONS
    ):
        approvals.append("autonomy_upgrade")
    if action.requires_approval:
        approvals.append("repair_action_approval")
    if action.side_effect_level in RISKY_SIDE_EFFECTS:
        approvals.append(f"{action.side_effect_level}_approval")
    if (
        action.risk_level in {"high", "critical"}
        or context.scientific_risk_level in {"high", "critical"}
    ):
        approvals.append("high_risk_repair_approval")
    if _normalized(context.job_type) in EXPENSIVE_JOB_TERMS or any(
        term in payload for term in EXPENSIVE_JOB_TERMS
    ):
        approvals.append("expensive_or_sensitive_job_approval")
    if _normalized(context.artifact_type) in APPROVAL_ARTIFACT_TYPES:
        approvals.append("artifact_review_approval")
    if context.external_system_mode in {"write_enabled", "sync_write", "external_write"}:
        approvals.append("external_system_approval")
    if context.project_policy.get("require_repair_approval") is True:
        approvals.append("project_repair_approval")
    if context.org_policy.get("require_repair_approval") is True:
        approvals.append("org_repair_approval")
    if "support_bundle" in payload and ("logs" in payload or "transcript" in payload):
        approvals.append("support_bundle_log_approval")
    if "review_packet" in payload and action.action_type == "retry_codex_with_schema":
        approvals.append("review_packet_codex_summary_approval")
    if "campaign_plan" in payload or "portfolio_selection" in payload:
        approvals.append("portfolio_or_campaign_change_approval")
    if action.action_type in APPROVAL_REQUIRED_ACTIONS:
        approvals.append("repair_action_approval")
    return sorted(set(approvals))


def _is_default_safe_action(action: RepairAction, context: RepairPolicyContext) -> bool:
    if context.autonomy_level not in SAFE_AUTONOMY_LEVELS:
        return False
    if action.side_effect_level not in {"none", "artifact_write", "external_read"}:
        return False
    if action.risk_level not in {"low", "medium"}:
        return False
    if action.action_type in {
        "retry_external_read",
        "revalidate_artifact",
        "rebuild_index",
        "run_regression_check",
    }:
        return True
    if action.action_type == "regenerate_artifact":
        return context.artifact_type in {None, "derived_report", "derived_artifact", "report"}
    if action.action_type == "retry_codex_with_schema":
        return context.failure_category in {"parse_error", "invalid_schema", "validation_failed"}
    if action.action_type == "mark_skipped":
        return context.artifact_type in {None, "optional_summary", "non_scientific_summary"}
    return False


def _parse_action(action: RepairAction | Mapping[str, Any]) -> RepairAction:
    if isinstance(action, RepairAction):
        return action
    return RepairAction.model_validate(action)


def _parse_plan(plan: RepairPlan | Mapping[str, Any]) -> RepairPlan:
    if isinstance(plan, RepairPlan):
        return plan
    return RepairPlan.model_validate(plan)


def _parse_context(
    context: RepairPolicyContext | Mapping[str, Any] | None,
) -> RepairPolicyContext:
    if isinstance(context, RepairPolicyContext):
        return context
    return RepairPolicyContext.model_validate(context or {})


def _decision_metadata(
    action: RepairAction,
    context: RepairPolicyContext,
) -> dict[str, Any]:
    return {
        "repair_action_id": action.repair_action_id,
        "action_type": action.action_type,
        "side_effect_level": action.side_effect_level,
        "failure_category": context.failure_category,
        "autonomy_level": context.autonomy_level,
        "artifact_type": context.artifact_type,
        "scientific_risk_level": context.scientific_risk_level,
    }


def _action_payload_text(action: RepairAction, context: RepairPolicyContext) -> str:
    values = [
        action.action_type,
        action.target_object_type,
        action.target_object_id,
        action.tool_name or "",
        action.expected_effect,
        repr(action.tool_args),
        repr(action.metadata),
        context.job_type or "",
        context.failure_category or "",
        context.artifact_type or "",
        context.guardrail_category or "",
        repr(context.policy_context),
    ]
    return _normalized(" ".join(values))


def _contains_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            _normalized_key(key) in keys or _contains_key(item, keys)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(item, keys) for item in value)
    return False


def _blocked_term_reason(term: str) -> str:
    return {
        "approve_own_external_write": "Codex cannot approve its own external writes.",
        "bypass_approval": "Repair cannot bypass approval gates.",
        "invent_missing_evidence": "Repair cannot invent missing evidence.",
        "invent_evidence": "Repair cannot invent missing evidence.",
        "generated_molecule_evidence": "Repair cannot add generated molecules as evidence.",
        "edit_assay_results": "Repair cannot edit assay results.",
        "edit_source_records": "Repair cannot edit source records.",
        "edit_source_artifact": "Repair cannot edit source artifacts.",
        "fake_citation": "Repair cannot create fake citations.",
        "fabricate_citation": "Repair cannot create fake citations.",
        "forbidden_tool": "Repair cannot use forbidden tools.",
        "hide_failed_qc": "Repair cannot hide failed QC.",
        "bypass_rbac": "Repair cannot bypass RBAC.",
        "approve_stage_gate": "Codex cannot approve stage gates.",
        "remove_guardrail_failure": "Repair cannot remove or hide guardrail failures.",
        "direct_score_edit": "Repair cannot edit scientific scores directly.",
        "change_score_directly": "Repair cannot edit scientific scores directly.",
        "change_autonomy_level": "Repair cannot bypass approval by changing autonomy level.",
        "alter_benchmark_outcome": "Repair cannot alter benchmark outcomes.",
    }[term]


def _has_review_role(context: RepairPolicyContext) -> bool:
    roles = set(context.user_roles)
    if context.user_role:
        roles.add(context.user_role)
    return bool(roles.intersection(REVIEW_ROLES))


def _normalized(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


__all__ = [
    "RepairAction",
    "RepairPlan",
    "RepairPolicyContext",
    "RepairPolicyDecision",
    "RepairPolicyDecisionStatus",
    "RepairPolicyEngine",
    "evaluate_repair_action",
    "evaluate_repair_plan",
]
