from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AutonomyLevel = Literal[
    "observe_only",
    "suggest_only",
    "execute_safe_tools",
    "execute_with_approval",
    "full_auto_restricted",
]
RuntimeSessionStatus = Literal[
    "created",
    "planning",
    "awaiting_approval",
    "executing",
    "succeeded",
    "failed",
    "cancelled",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
PlanCreator = Literal["codex", "deterministic_template", "user"]
RuntimeStepStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "running",
    "succeeded",
    "failed",
    "skipped",
]
ToolSideEffectLevel = Literal[
    "none",
    "artifact_write",
    "db_write",
    "external_read",
    "external_write",
    "codex_subprocess",
]
ToolResultStatus = Literal[
    "succeeded",
    "failed",
    "skipped",
    "policy_blocked",
    "approval_required",
    "validation_failed",
]
ApprovalType = Literal[
    "execute_plan",
    "external_write",
    "generated_molecule_export",
    "campaign_advance",
    "stage_gate",
    "codex_full_auto",
    "high_cost_job",
    "integration_sync",
    "destructive_action",
    "broad_codex_access",
    "support_bundle_logs",
    "policy_override",
]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class RuntimeAgentSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class RuntimeAgentSession(RuntimeAgentSchema):
    session_id: str
    project_id: str | None
    org_id: str | None
    user_id: str | None
    user_goal: str
    autonomy_level: AutonomyLevel
    status: RuntimeSessionStatus
    started_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeActionStep(RuntimeAgentSchema):
    step_id: str
    plan_id: str
    step_index: int = Field(ge=0)
    action_type: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool
    approval_reason: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)
    status: RuntimeStepStatus
    result_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeActionPlan(RuntimeAgentSchema):
    plan_id: str
    session_id: str
    user_goal: str
    plan_summary: str
    steps: list[RuntimeActionStep] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    risk_level: RiskLevel
    guardrail_warnings: list[str] = Field(default_factory=list)
    created_by: PlanCreator
    validated: bool
    validation_errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_tool_specs_when_validated(self) -> RuntimeActionPlan:
        if not self.validated:
            return self
        tool_specs = self.metadata.get("tool_specs")
        if not isinstance(tool_specs, dict) or not tool_specs:
            raise ValueError("validated runtime action plans require tool specs")
        missing_specs = [
            step.tool_name
            for step in self.steps
            if step.tool_name not in tool_specs or not isinstance(tool_specs[step.tool_name], dict)
        ]
        if missing_specs:
            raise ValueError(
                "validated runtime action plans require tool specs for: "
                + ", ".join(sorted(set(missing_specs)))
            )
        invalid_specs = [
            name
            for name, spec in tool_specs.items()
            if isinstance(spec, dict)
            and (
                not spec.get("required_permissions")
                or not isinstance(spec.get("required_permissions"), list)
                or not spec.get("side_effect_level")
            )
        ]
        if invalid_specs:
            raise ValueError(
                "validated runtime action plans require tool specs with permissions "
                "and side-effect levels for: "
                + ", ".join(sorted(invalid_specs))
            )
        return self


class RuntimeToolSpec(RuntimeAgentSchema):
    tool_name: str
    category: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_permissions: list[str]
    policy_tags: list[str] = Field(default_factory=list)
    side_effect_level: ToolSideEffectLevel
    requires_approval_by_default: bool
    idempotent: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_permissions")
    @classmethod
    def require_permissions(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("required_permissions must not be empty")
        if any(not permission.strip() for permission in value):
            raise ValueError("required_permissions must not contain empty values")
        return value


class RuntimeToolResult(RuntimeAgentSchema):
    result_id: str
    step_id: str
    tool_name: str
    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeApprovalRequest(RuntimeAgentSchema):
    approval_id: str
    session_id: str
    plan_id: str
    step_id: str | None
    requested_by: str
    approval_type: ApprovalType
    reason: str
    risk_summary: str
    requested_at: datetime
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeAgentAuditEvent(RuntimeAgentSchema):
    event_id: str
    session_id: str
    event_type: str
    actor: str | None = None
    timestamp: datetime
    summary: str
    object_type: str | None = None
    object_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
