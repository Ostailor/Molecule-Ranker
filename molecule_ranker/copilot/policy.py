from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

from molecule_ranker.copilot.schemas import (
    ActionStatus,
    ActionType,
    CoPilotAction,
    CoPilotTrigger,
    RiskLevel,
    SideEffectLevel,
)

PolicyStatus = Literal["allowed", "approval_required", "blocked"]

HUMAN_APPROVAL_REQUIRED_FOR = [
    "campaign_advancement",
    "stage_gates",
    "external_writes",
    "generated_molecule_assay_advancement",
    "destructive_actions",
    "high_cost_jobs",
    "policy_overrides",
]

_ALLOWED_AUTOMATIC_ACTIONS = {
    "summarize_status",
    "create_replan_draft",
    "run_graph_refresh",
    "run_contradiction_scan",
    "create_review_request",
    "notify_user",
}

_BLOCKED_ACTION_REASONS = {
    "approve_campaign_advancement": "Co-pilot cannot approve campaign advancement.",
    "approve_stage_gate": "Co-pilot cannot approve stage gate decisions.",
    "approve_own_action": "Co-pilot cannot approve own actions.",
    "fabricate_result": "Co-pilot cannot fabricate result/evidence.",
    "fabricate_evidence": "Co-pilot cannot fabricate result/evidence.",
    "change_score_directly": "Co-pilot cannot change candidate scores directly.",
    "edit_candidate_score": "Co-pilot cannot change candidate scores directly.",
    "edit_assay_result": "Co-pilot cannot edit assay result records.",
    "edit_source_artifact": "Co-pilot cannot edit source artifact records.",
    "bypass_guardrail": "Co-pilot cannot bypass guardrail controls.",
}

_SIDE_EFFECT_BY_ACTION_TYPE: dict[ActionType, SideEffectLevel] = {
    "summarize_status": "none",
    "create_replan_draft": "artifact_write",
    "run_campaign_replan": "db_write",
    "update_campaign_status": "db_write",
    "create_review_request": "db_write",
    "create_followup_request": "db_write",
    "run_graph_refresh": "db_write",
    "run_contradiction_scan": "artifact_write",
    "run_portfolio_reoptimization": "artifact_write",
    "run_active_learning_update": "artifact_write",
    "run_evaluation_update": "artifact_write",
    "run_repair_workflow": "artifact_write",
    "request_approval": "db_write",
    "notify_user": "none",
    "pause_campaign": "db_write",
    "create_support_bundle": "artifact_write",
}

_RISK_BY_ACTION_TYPE: dict[ActionType, RiskLevel] = {
    "summarize_status": "low",
    "create_replan_draft": "medium",
    "run_campaign_replan": "high",
    "update_campaign_status": "medium",
    "create_review_request": "medium",
    "create_followup_request": "medium",
    "run_graph_refresh": "medium",
    "run_contradiction_scan": "medium",
    "run_portfolio_reoptimization": "high",
    "run_active_learning_update": "high",
    "run_evaluation_update": "medium",
    "run_repair_workflow": "medium",
    "request_approval": "medium",
    "notify_user": "low",
    "pause_campaign": "high",
    "create_support_bundle": "low",
}


@dataclass(frozen=True)
class PolicyDecision:
    status: PolicyStatus
    reason: str
    requires_approval: bool
    blocked: bool
    side_effect_level: SideEffectLevel
    risk_level: RiskLevel
    warnings: list[str] = field(default_factory=list)


class CoPilotPolicyEngine:
    def evaluate_action(
        self,
        *,
        action_type: str,
        autonomy_level: str,
        metadata: dict[str, object] | None = None,
        user_policy: dict[str, object] | None = None,
        project_policy: dict[str, object] | None = None,
        org_policy: dict[str, object] | None = None,
        campaign_policy: dict[str, object] | None = None,
    ) -> PolicyDecision:
        metadata = metadata or {}
        side_effect_level = self._side_effect_level(action_type, metadata)
        risk_level = self._risk_level(action_type)

        blocked_reason = self._blocked_reason(
            action_type=action_type,
            side_effect_level=side_effect_level,
            metadata=metadata,
            user_policy=user_policy or {},
            project_policy=project_policy or {},
            org_policy=org_policy or {},
            campaign_policy=campaign_policy or {},
        )
        if blocked_reason is not None:
            return self._decision(
                "blocked",
                blocked_reason,
                side_effect_level=side_effect_level,
                risk_level=risk_level,
            )

        approval_reason = self._approval_reason(
            action_type=action_type,
            side_effect_level=side_effect_level,
            risk_level=risk_level,
            metadata=metadata,
            autonomy_level=autonomy_level,
        )
        if approval_reason is not None:
            return self._decision(
                "approval_required",
                approval_reason,
                side_effect_level=side_effect_level,
                risk_level=risk_level,
            )

        if autonomy_level == "observe_only":
            return self._decision(
                "blocked",
                "observe_only autonomy blocks action execution.",
                side_effect_level=side_effect_level,
                risk_level=risk_level,
            )
        if autonomy_level == "suggest_only":
            return self._decision(
                "approval_required",
                "suggest_only autonomy requires approval before execution.",
                side_effect_level=side_effect_level,
                risk_level=risk_level,
            )
        if autonomy_level == "execute_safe_actions" and not self._is_default_safe(
            action_type, metadata
        ):
            return self._decision(
                "approval_required",
                "execute_safe_actions autonomy only permits default safe actions.",
                side_effect_level=side_effect_level,
                risk_level=risk_level,
            )
        return self._decision(
            "allowed",
            "Action is allowed by co-pilot policy.",
            side_effect_level=side_effect_level,
            risk_level=risk_level,
        )

    def propose_actions(
        self,
        trigger: CoPilotTrigger,
        *,
        autonomy_level: str,
    ) -> list[CoPilotAction]:
        return [
            self._build_action(trigger, action_type, autonomy_level=autonomy_level)
            for action_type in trigger.recommended_action_types
            if self._is_schema_action(action_type)
        ]

    def requires_approval(
        self,
        *,
        side_effect_level: str,
        risk_level: str,
        trigger: CoPilotTrigger,
        autonomy_level: str,
    ) -> bool:
        return (
            autonomy_level in {"observe_only", "suggest_only"}
            or trigger.requires_human_attention
            or side_effect_level in {"external_write", "destructive"}
            or risk_level in {"high", "critical"}
        )

    def _build_action(
        self,
        trigger: CoPilotTrigger,
        action_type: str,
        *,
        autonomy_level: str,
    ) -> CoPilotAction:
        typed_action_type = cast(ActionType, action_type)
        decision = self.evaluate_action(
            action_type=action_type,
            autonomy_level=autonomy_level,
            metadata={
                "trigger_id": trigger.trigger_id,
                "trigger_type": trigger.trigger_type,
                "requires_human_attention": trigger.requires_human_attention,
            },
        )
        status: ActionStatus = (
            "queued"
            if decision.status == "allowed" and autonomy_level != "suggest_only"
            else "proposed"
        )
        if decision.status == "blocked":
            status = "skipped"
        return CoPilotAction(
            copilot_action_id=f"action-{trigger.trigger_id}-{action_type}",
            campaign_id=trigger.campaign_id,
            trigger_id=trigger.trigger_id,
            action_type=typed_action_type,
            tool_name=None,
            tool_args={"trigger_id": trigger.trigger_id},
            side_effect_level=decision.side_effect_level,
            risk_level=decision.risk_level,
            requires_approval=decision.requires_approval,
            approval_reason=decision.reason if decision.requires_approval else None,
            status=status,
            created_at=datetime.now(UTC),
            completed_at=None,
            metadata={"policy_status": decision.status, "policy_reason": decision.reason},
        )

    def _blocked_reason(
        self,
        *,
        action_type: str,
        side_effect_level: SideEffectLevel,
        metadata: dict[str, object],
        user_policy: dict[str, object],
        project_policy: dict[str, object],
        org_policy: dict[str, object],
        campaign_policy: dict[str, object],
    ) -> str | None:
        if action_type in _BLOCKED_ACTION_REASONS:
            return _BLOCKED_ACTION_REASONS[action_type]
        actor_id = metadata.get("actor_id")
        approver_id = metadata.get("approver_id")
        if actor_id is not None and actor_id == approver_id:
            return "Co-pilot cannot approve own actions."
        if metadata.get("guardrail_failure") or action_type == "bypass_guardrail":
            return "Co-pilot cannot bypass guardrail controls."
        if metadata.get("budget_approval_bypass"):
            return "Co-pilot cannot bypass budget approval."
        if side_effect_level == "external_write" and metadata.get("execution_requested"):
            return "Co-pilot blocks external write without approval."
        if self._policy_blocks_action(action_type, user_policy, project_policy, campaign_policy):
            return "Action blocked by user/project/campaign policy."
        if self._policy_blocks_side_effect(side_effect_level, org_policy):
            return "Action blocked by org policy."
        return None

    def _approval_reason(
        self,
        *,
        action_type: str,
        side_effect_level: SideEffectLevel,
        risk_level: RiskLevel,
        metadata: dict[str, object],
        autonomy_level: str,
    ) -> str | None:
        if autonomy_level == "observe_only":
            return None
        if autonomy_level == "suggest_only":
            return "suggest_only autonomy requires approval before execution."
        if action_type == "run_campaign_replan" and metadata.get("changes_active_plan"):
            return "Human approval required to change active campaign plan."
        if action_type == "run_portfolio_reoptimization" and metadata.get(
            "changes_selected_candidates"
        ):
            return "Human approval required to change selected candidates."
        if action_type == "update_campaign_status" and metadata.get("new_status") in {
            "active",
            "completed",
            "cancelled",
        }:
            return "Human approval required for campaign status changes."
        if side_effect_level == "external_write":
            return "Human approval required for external write actions."
        if metadata.get("decision_type") == "stage_gate":
            return "Human approval required for stage gate decisions."
        if metadata.get("generated_molecule_assay_advancement"):
            return "Human approval required for generated molecule assay advancement."
        if metadata.get("high_cost_job"):
            return "Human approval required for high-cost jobs."
        if action_type == "create_support_bundle" and (
            metadata.get("include_logs") or metadata.get("include_transcripts")
        ):
            return "Human approval required for support bundle including logs/transcripts."
        if metadata.get("destructive") or side_effect_level == "destructive":
            return "Human approval required for destructive or rollback actions."
        if self._int_metadata(metadata, "recent_failure_count") >= 3:
            return "Human approval required after repeated failure."
        if action_type == "run_repair_workflow" and not metadata.get("safe_repair"):
            return "Human approval required for non-safe repair workflows."
        if risk_level in {"high", "critical"} and not self._is_default_safe(action_type, metadata):
            return "Human approval required for high-risk actions."
        return None

    def _side_effect_level(
        self,
        action_type: str,
        metadata: dict[str, object],
    ) -> SideEffectLevel:
        explicit = metadata.get("side_effect_level")
        if explicit in {
            "none",
            "artifact_write",
            "db_write",
            "external_read",
            "external_write",
            "destructive",
        }:
            return cast(SideEffectLevel, explicit)
        if action_type in _SIDE_EFFECT_BY_ACTION_TYPE:
            return _SIDE_EFFECT_BY_ACTION_TYPE[cast(ActionType, action_type)]
        return "none"

    def _risk_level(self, action_type: str) -> RiskLevel:
        if action_type in _RISK_BY_ACTION_TYPE:
            return _RISK_BY_ACTION_TYPE[cast(ActionType, action_type)]
        return "critical"

    def _is_default_safe(self, action_type: str, metadata: dict[str, object]) -> bool:
        if action_type == "run_repair_workflow":
            return bool(metadata.get("safe_repair"))
        if action_type == "create_support_bundle":
            return not metadata.get("include_logs") and not metadata.get(
                "include_transcripts"
            )
        return action_type in _ALLOWED_AUTOMATIC_ACTIONS

    def _is_schema_action(self, action_type: str) -> bool:
        return action_type in _SIDE_EFFECT_BY_ACTION_TYPE

    def _policy_blocks_action(
        self,
        action_type: str,
        user_policy: dict[str, object],
        project_policy: dict[str, object],
        campaign_policy: dict[str, object],
    ) -> bool:
        if action_type in self._string_set(user_policy.get("blocked_action_types")):
            return True
        if action_type in self._string_set(campaign_policy.get("blocked_action_types")):
            return True
        allowed = project_policy.get("allowed_action_types")
        return isinstance(allowed, list) and action_type not in set(allowed)

    def _policy_blocks_side_effect(
        self,
        side_effect_level: SideEffectLevel,
        org_policy: dict[str, object],
    ) -> bool:
        return side_effect_level in self._string_set(
            org_policy.get("blocked_side_effect_levels")
        )

    def _string_set(self, value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item) for item in value}

    def _int_metadata(self, metadata: dict[str, object], key: str) -> int:
        value = metadata.get(key, 0)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0

    def _decision(
        self,
        status: PolicyStatus,
        reason: str,
        *,
        side_effect_level: SideEffectLevel,
        risk_level: RiskLevel,
    ) -> PolicyDecision:
        return PolicyDecision(
            status=status,
            reason=reason,
            requires_approval=status == "approval_required",
            blocked=status == "blocked",
            side_effect_level=side_effect_level,
            risk_level=risk_level,
        )
