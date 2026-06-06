from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SubagentRole = Literal[
    "program_manager",
    "evidence_reviewer",
    "molecule_designer",
    "biologics_engineer",
    "developability_safety",
    "experiment_analyst",
    "predictive_modeler",
    "structure_reviewer",
    "graph_reasoner",
    "hypothesis_planner",
    "portfolio_strategist",
    "campaign_planner",
    "integration_operator",
    "evaluation_validator",
    "guardrail_sentinel",
    "platform_operator",
]
TaskRiskLevel = Literal["low", "medium", "high", "critical"]
SubagentTaskStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "awaiting_approval",
]
SubagentResultStatus = Literal[
    "succeeded",
    "failed",
    "partial",
    "guardrail_failed",
    "validation_failed",
]
SubagentMessageType = Literal[
    "task_request",
    "task_result",
    "critique",
    "clarification",
    "escalation",
    "approval_request",
    "status_update",
]
SubagentCritiqueType = Literal[
    "evidence_grounding",
    "scientific_guardrail",
    "tool_policy",
    "policy_permission",
    "uncertainty",
    "contradiction",
    "contradiction_staleness",
    "safety",
    "safety_developability",
    "operational",
    "operational_reliability",
    "output_schema_validity",
    "artifact_provenance",
]
SubagentConsensusStatus = Literal[
    "agreed",
    "disagreement",
    "requires_human_review",
    "inconclusive",
]


class SubagentSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class SubagentProfile(SubagentSchema):
    subagent_id: str
    name: str
    role: SubagentRole
    description: str
    allowed_tool_categories: list[str]
    denied_tool_categories: list[str]
    required_permissions: list[str]
    default_autonomy_level: str
    max_context_bytes: int = Field(gt=0)
    can_delegate: bool
    can_request_approval: bool
    can_execute_tools: bool
    can_write_artifacts: bool
    guardrail_profile: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "allowed_tool_categories",
        "denied_tool_categories",
        "required_permissions",
    )
    @classmethod
    def reject_blank_list_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("lists must not contain blank values")
        return value

    @model_validator(mode="after")
    def reject_overlapping_tool_categories(self) -> SubagentProfile:
        overlap = set(self.allowed_tool_categories).intersection(self.denied_tool_categories)
        if overlap:
            raise ValueError(
                "allowed and denied tool categories overlap: " + ", ".join(sorted(overlap))
            )
        return self


class SubagentTask(SubagentSchema):
    task_id: str
    parent_session_id: str
    assigned_subagent_id: str
    task_type: str
    objective: str
    input_artifact_ids: list[str]
    allowed_tool_names: list[str]
    forbidden_tool_names: list[str]
    expected_output_schema: dict[str, Any]
    required_outputs: list[str]
    risk_level: TaskRiskLevel
    requires_human_approval: bool
    status: SubagentTaskStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "input_artifact_ids",
        "allowed_tool_names",
        "forbidden_tool_names",
        "required_outputs",
    )
    @classmethod
    def reject_blank_task_list_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("lists must not contain blank values")
        return value

    @model_validator(mode="after")
    def require_scoped_tools_artifacts_and_schema(self) -> SubagentTask:
        if not self.input_artifact_ids:
            raise ValueError("subagent tasks must include scoped input artifacts")
        if not self.allowed_tool_names:
            raise ValueError("subagent tasks must include scoped allowed tools")
        overlap = set(self.allowed_tool_names).intersection(self.forbidden_tool_names)
        if overlap:
            raise ValueError(
                "allowed and forbidden tool names overlap: " + ", ".join(sorted(overlap))
            )
        if self.expected_output_schema.get("type") != "object":
            raise ValueError("expected_output_schema must be a JSON object schema")
        return self


class SubagentResult(SubagentSchema):
    result_id: str
    task_id: str
    subagent_id: str
    status: SubagentResultStatus
    output_json: dict[str, Any] | None
    output_text: str
    artifact_ids: list[str]
    tool_usage_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str]
    guardrail_findings: list[dict[str, Any]]
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentMessage(SubagentSchema):
    message_id: str
    parent_session_id: str
    from_subagent_id: str
    to_subagent_id: str | None
    message_type: SubagentMessageType
    content: str
    referenced_artifact_ids: list[str]
    referenced_entity_ids: list[str]
    referenced_tool_names: list[str]
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentCritique(SubagentSchema):
    critique_id: str
    critic_subagent_id: str
    target_result_id: str
    critique_type: SubagentCritiqueType
    passed: bool
    findings: list[str]
    required_fixes: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentConsensus(SubagentSchema):
    consensus_id: str
    parent_session_id: str
    task_ids: list[str]
    participating_subagent_ids: list[str]
    consensus_status: SubagentConsensusStatus
    summary: str
    agreements: list[str]
    disagreements: list[str]
    recommended_next_actions: list[str]
    human_review_required: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultiAgentSession(SubagentSchema):
    multi_agent_session_id: str
    runtime_session_id: str | None
    user_goal: str
    supervisor_subagent_id: str
    subagent_ids: list[str]
    tasks: list[SubagentTask]
    messages: list[SubagentMessage]
    results: list[SubagentResult]
    critiques: list[SubagentCritique]
    consensus: list[SubagentConsensus]
    status: str
    started_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "MultiAgentSession",
    "SubagentConsensus",
    "SubagentConsensusStatus",
    "SubagentCritique",
    "SubagentCritiqueType",
    "SubagentMessage",
    "SubagentMessageType",
    "SubagentProfile",
    "SubagentResult",
    "SubagentResultStatus",
    "SubagentRole",
    "SubagentSchema",
    "SubagentTask",
    "SubagentTaskStatus",
    "TaskRiskLevel",
]
