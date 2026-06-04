from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EvaluatedObjectType = Literal[
    "runtime_plan",
    "action_step",
    "tool_result",
    "subagent_result",
    "artifact",
    "report",
    "workflow",
    "job",
    "codex_output",
]
EvaluationType = Literal[
    "pre_execution",
    "post_execution",
    "guardrail",
    "scientific_integrity",
    "schema_validity",
    "artifact_completeness",
    "permission_policy",
    "reproducibility",
    "performance",
    "operational",
]
FailureObjectType = Literal[
    "tool_call",
    "job",
    "artifact",
    "validation",
    "guardrail",
    "codex_output",
    "subagent_task",
    "workflow",
    "integration_sync",
    "model_training",
    "structure_workflow",
    "campaign",
]
FailureCategory = Literal[
    "missing_input",
    "invalid_schema",
    "missing_artifact",
    "external_unavailable",
    "permission_denied",
    "policy_blocked",
    "guardrail_failed",
    "timeout",
    "resource_exhausted",
    "tool_error",
    "validation_failed",
    "parse_error",
    "unsafe_output",
    "inconsistent_artifacts",
    "reproducibility_failure",
    "unknown",
]
Repairability = Literal[
    "automatic_safe",
    "automatic_with_limits",
    "approval_required",
    "human_input_required",
    "not_repairable",
]
RepairActionType = Literal[
    "rerun_tool",
    "rerun_job",
    "regenerate_artifact",
    "revalidate_artifact",
    "adjust_safe_config",
    "request_missing_input",
    "request_human_approval",
    "mark_skipped",
    "quarantine_artifact",
    "rollback_artifact",
    "rollback_job",
    "rebuild_index",
    "clear_derived_cache",
    "retry_external_read",
    "retry_codex_with_schema",
    "run_regression_check",
    "create_issue_report",
]
RepairSideEffectLevel = Literal[
    "none",
    "artifact_write",
    "db_write",
    "external_read",
    "external_write",
    "destructive",
]
RepairRiskLevel = Literal["low", "medium", "high", "critical"]
RepairPlanCreator = Literal["deterministic", "codex", "subagent", "human"]
RepairExecutionStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "partially_succeeded",
    "cancelled",
    "approval_required",
    "guardrail_blocked",
]
RegressionCheckType = Literal[
    "schema_contract",
    "guardrail",
    "artifact_completeness",
    "scientific_integrity",
    "permissions",
    "reproducibility",
    "performance_smoke",
    "workflow_smoke",
    "targeted_unit_subset",
    "targeted_integration_subset",
    "unit_subset",
    "integration_subset",
]

SCIENTIFIC_CONTENT_KEYS = {
    "assay_result",
    "assay_results",
    "assayresult",
    "benchmark_metric",
    "benchmark_metrics",
    "citation",
    "citations",
    "docking_score",
    "docking_scores",
    "evidence_item",
    "evidence_items",
    "evidenceitem",
    "graph_edge",
    "graph_edges",
    "graph_fact",
    "graph_facts",
    "molecule",
    "molecules",
    "score",
    "scores",
    "scientific_score",
    "scientific_scores",
}
SCIENTIFIC_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I),
    re.compile(r"\bPMID:?\s*\d{4,9}\b", re.I),
    re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I),
    re.compile(r"\b(?:SMILES|InChI)\s*[:=]\s*[A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]+", re.I),
    re.compile(
        r"\b(?:is|are|was|were|proved|proven|confirmed)\b.{0,50}"
        r"\b(?:safe|active|effective|binding|binds|synthesizable)\b",
        re.I,
    ),
)


class AgentRepairSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class AgentSelfEvaluation(AgentRepairSchema):
    evaluation_id: str
    session_id: str | None
    subagent_id: str | None
    task_id: str | None
    evaluated_object_type: EvaluatedObjectType
    evaluated_object_id: str
    evaluation_type: EvaluationType
    passed: bool
    score: float = Field(ge=0, le=1)
    findings: list[str] = Field(default_factory=list)
    required_repairs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureDiagnosis(AgentRepairSchema):
    diagnosis_id: str
    failure_object_type: FailureObjectType
    failure_object_id: str
    failure_category: FailureCategory
    root_cause_summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recoverable: bool
    repairability: Repairability
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairAction(AgentRepairSchema):
    repair_action_id: str
    action_type: RepairActionType
    target_object_type: str
    target_object_id: str
    tool_name: str | None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str
    side_effect_level: RepairSideEffectLevel
    requires_approval: bool
    approval_reason: str | None
    risk_level: RepairRiskLevel
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPlan(AgentRepairSchema):
    repair_plan_id: str
    diagnosis_id: str
    session_id: str | None
    plan_summary: str
    actions: list[RepairAction] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    rollback_plan: list[RepairAction] = Field(default_factory=list)
    requires_human_approval: bool
    scientific_guardrails: list[str] = Field(default_factory=list)
    validated: bool
    validation_errors: list[str] = Field(default_factory=list)
    created_by: RepairPlanCreator
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def block_fabricated_scientific_content(self) -> RepairPlan:
        inspected_payload = {
            "plan_summary": self.plan_summary,
            "actions": [action.model_dump(mode="python") for action in self.actions],
            "expected_artifacts": self.expected_artifacts,
            "rollback_plan": [action.model_dump(mode="python") for action in self.rollback_plan],
            "metadata": self.metadata,
        }
        if _contains_scientific_content(inspected_payload):
            raise ValueError("repair plans must not include fabricated scientific content")
        return self


class RepairExecution(AgentRepairSchema):
    repair_execution_id: str
    repair_plan_id: str
    status: RepairExecutionStatus
    executed_actions: list[dict[str, Any]] = Field(default_factory=list)
    artifacts_created: list[str] = Field(default_factory=list)
    artifacts_modified: list[str] = Field(default_factory=list)
    jobs_created: list[str] = Field(default_factory=list)
    approvals_requested: list[str] = Field(default_factory=list)
    regression_check_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegressionCheck(AgentRepairSchema):
    regression_check_id: str
    repair_execution_id: str | None
    check_type: RegressionCheckType
    passed: bool
    findings: list[str] = Field(default_factory=list)
    artifacts_checked: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairMemoryRecord(AgentRepairSchema):
    memory_id: str
    failure_signature: str
    failure_category: FailureCategory
    successful_repair_plan_id: str | None
    repair_success_rate: float = Field(ge=0, le=1)
    last_seen_at: datetime
    occurrence_count: int = Field(ge=0)
    recommended_repair_strategy: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _contains_scientific_content(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized_key in SCIENTIFIC_CONTENT_KEYS:
                return True
            if _contains_scientific_content(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_scientific_content(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SCIENTIFIC_CONTENT_PATTERNS)
    return False


__all__ = [
    "AgentRepairSchema",
    "AgentSelfEvaluation",
    "EvaluatedObjectType",
    "EvaluationType",
    "FailureCategory",
    "FailureDiagnosis",
    "FailureObjectType",
    "RegressionCheck",
    "RegressionCheckType",
    "RepairAction",
    "RepairActionType",
    "RepairExecution",
    "RepairExecutionStatus",
    "RepairMemoryRecord",
    "RepairPlan",
    "RepairPlanCreator",
    "RepairRiskLevel",
    "RepairSideEffectLevel",
    "Repairability",
]
