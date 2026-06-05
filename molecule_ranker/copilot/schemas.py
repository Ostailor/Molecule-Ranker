from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SessionStatus = Literal["active", "paused", "stopped", "failed", "awaiting_approval"]
AutonomyLevel = Literal[
    "observe_only",
    "suggest_only",
    "execute_safe_actions",
    "execute_with_approval",
    "supervised_auto",
]
CampaignEventType = Literal[
    "assay_result_imported",
    "review_decision_added",
    "hypothesis_status_changed",
    "graph_contradiction_detected",
    "stale_decision_detected",
    "model_retrained",
    "portfolio_updated",
    "structure_assessment_updated",
    "developability_risk_changed",
    "integration_sync_completed",
    "external_status_update",
    "job_failed",
    "job_repaired",
    "budget_changed",
    "stage_gate_decision",
    "evaluation_report_created",
    "guardrail_failure",
    "user_comment_added",
    "scheduled_check",
]
Severity = Literal["info", "low", "medium", "high", "critical"]
TriggerType = Literal[
    "replan_needed",
    "approval_needed",
    "blocker_detected",
    "result_followup_needed",
    "contradiction_resolution_needed",
    "safety_review_needed",
    "budget_review_needed",
    "campaign_update_needed",
    "evaluation_needed",
    "repair_needed",
    "no_action",
]
Priority = Literal["low", "medium", "high", "critical"]
ActionType = Literal[
    "summarize_status",
    "create_replan_draft",
    "run_campaign_replan",
    "update_campaign_status",
    "create_review_request",
    "create_followup_request",
    "run_graph_refresh",
    "run_contradiction_scan",
    "run_portfolio_reoptimization",
    "run_active_learning_update",
    "run_evaluation_update",
    "run_repair_workflow",
    "request_approval",
    "notify_user",
    "pause_campaign",
    "create_support_bundle",
]
SideEffectLevel = Literal[
    "none",
    "artifact_write",
    "db_write",
    "external_read",
    "external_write",
    "destructive",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
ActionStatus = Literal[
    "proposed",
    "approved",
    "rejected",
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
]
ActionResultStatus = Literal[
    "succeeded",
    "failed",
    "partially_succeeded",
    "skipped",
    "blocked_by_policy",
    "approval_required",
    "guardrail_failed",
]
EscalationType = Literal[
    "human_approval_required",
    "safety_review_required",
    "scientific_disagreement",
    "policy_block",
    "repeated_failure",
    "budget_exceeded",
    "external_system_issue",
    "guardrail_failure",
    "campaign_blocked",
    "missing_input",
]
EscalationStatus = Literal["open", "acknowledged", "resolved", "cancelled"]

_FORBIDDEN_ACTION_TERMS = (
    "protocol",
    "synthesis",
    "synthesize",
    "dosing",
    "dose",
    "patient guidance",
    "patient-specific",
    "wet-lab",
    "wet lab",
    "procedural wet-lab",
)


class _CoPilotBaseModel(BaseModel):
    @field_validator("*", mode="after")
    @classmethod
    def _datetimes_must_be_timezone_aware(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.tzinfo.utcoffset(value) is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class CampaignCoPilotSession(_CoPilotBaseModel):
    copilot_session_id: str
    campaign_id: str
    project_id: str | None
    program_id: str | None
    status: SessionStatus
    autonomy_level: AutonomyLevel
    started_at: datetime
    stopped_at: datetime | None
    last_check_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignEvent(_CoPilotBaseModel):
    event_id: str
    campaign_id: str
    event_type: CampaignEventType
    source_object_type: str
    source_object_id: str
    severity: Severity
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    detected_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoPilotTrigger(_CoPilotBaseModel):
    trigger_id: str
    campaign_id: str
    event_ids: list[str] = Field(default_factory=list)
    trigger_signature: str | None = None
    trigger_type: TriggerType
    priority: Priority
    rationale: str
    recommended_action_types: list[str] = Field(default_factory=list)
    requires_human_attention: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoPilotAction(_CoPilotBaseModel):
    copilot_action_id: str
    campaign_id: str
    trigger_id: str
    action_type: ActionType
    tool_name: str | None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    side_effect_level: SideEffectLevel
    risk_level: RiskLevel
    requires_approval: bool
    approval_reason: str | None
    status: ActionStatus
    created_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _must_not_contain_protocol_or_clinical_details(self) -> CoPilotAction:
        payload = json.dumps(
            {
                "tool_name": self.tool_name,
                "tool_args": self.tool_args,
                "approval_reason": self.approval_reason,
                "metadata": self.metadata,
            },
            default=str,
            sort_keys=True,
        ).lower()
        if any(term in payload for term in _FORBIDDEN_ACTION_TERMS):
            raise ValueError(
                "research planning actions must not contain protocol, synthesis, dosing, "
                "patient guidance, or procedural wet-lab details"
            )
        return self


class CoPilotActionResult(_CoPilotBaseModel):
    result_id: str
    copilot_action_id: str
    status: ActionResultStatus
    artifact_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoPilotEscalation(_CoPilotBaseModel):
    escalation_id: str
    campaign_id: str
    trigger_id: str | None
    action_id: str | None
    escalation_type: EscalationType
    priority: Priority
    assigned_role: str | None
    message: str
    artifact_ids: list[str] = Field(default_factory=list)
    status: EscalationStatus
    created_at: datetime
    resolved_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoPilotStatusUpdate(_CoPilotBaseModel):
    status_update_id: str
    campaign_id: str
    period_start: datetime
    period_end: datetime
    executive_summary: str
    key_events: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    approvals_needed: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_recommended_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoPilotMemoryRecord(_CoPilotBaseModel):
    memory_id: str
    campaign_id: str | None
    trigger_signature: str
    recommended_action_type: str
    success_rate: float = Field(ge=0.0, le=1.0)
    occurrence_count: int
    last_seen_at: datetime
    notes: str
    metadata: dict[str, Any] = Field(default_factory=dict)
