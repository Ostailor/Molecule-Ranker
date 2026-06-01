from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

CampaignStatus = Literal[
    "draft",
    "under_review",
    "approved",
    "active",
    "paused",
    "completed",
    "cancelled",
    "replanning_required",
]
CampaignObjectiveType = Literal[
    "validate_hypothesis",
    "resolve_contradiction",
    "close_evidence_gap",
    "compare_candidates",
    "expand_series",
    "reduce_risk",
    "improve_developability",
    "learn_from_uncertainty",
    "portfolio_decision",
]
CampaignWorkPackageType = Literal[
    "expert_review",
    "computational_rerun",
    "literature_update",
    "developability_review",
    "structure_review",
    "assay_triage_request",
    "external_sync",
    "active_learning_batch",
    "portfolio_reoptimization",
    "hypothesis_review",
]
CampaignWorkPackageStatus = Literal[
    "proposed",
    "approved",
    "blocked",
    "ready",
    "in_progress",
    "completed",
    "cancelled",
    "failed",
]
CampaignExecutionEventType = Literal[
    "created",
    "approved",
    "started",
    "completed",
    "failed",
    "blocked",
    "paused",
    "resumed",
    "cancelled",
    "replanning_triggered",
    "result_imported",
    "review_decision_added",
    "budget_changed",
    "stage_gate_decided",
]
ReplanTriggerType = Literal[
    "new_positive_result",
    "new_negative_result",
    "safety_concern",
    "failed_qc",
    "contradiction_detected",
    "hypothesis_retired",
    "budget_exceeded",
    "work_package_failed",
    "model_retrained",
    "external_sync_update",
    "review_rejection",
    "portfolio_changed",
]
ReplanSeverity = Literal["low", "medium", "high", "critical"]
ReplanRecommendedAction = Literal["continue", "pause", "replan", "stop", "expert_review"]

_PROCEDURAL_LAB_DETAIL_RE = re.compile(
    r"\b("
    r"\d+(?:\.\d+)?\s*(?:nm|um|µm|mm|mg|g|ml|ul|µl|hours?|hrs?|minutes?|mins?|"
    r"seconds?|secs?|c|°c)"
    r"|concentration|temperature|reagent|reagents|incubate|incubation|pipette|wash|"
    r"centrifuge|step-by-step|protocol|synthetic route|synthesis route|synthesis "
    r"instructions|animal dosing|human dosing|patient treatment"
    r")\b",
    re.IGNORECASE,
)


class TimezoneAwareCampaignModel(BaseModel):
    @field_validator("created_at", "updated_at", "timestamp", check_fields=False)
    @classmethod
    def require_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class Campaign(TimezoneAwareCampaignModel):
    campaign_id: str
    project_id: str | None = None
    program_id: str | None = None
    name: str
    description: str | None = None
    disease_focus: list[str] = Field(default_factory=list)
    target_focus: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    portfolio_selection_ids: list[str] = Field(default_factory=list)
    status: CampaignStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignObjective(BaseModel):
    objective_id: str
    campaign_id: str
    name: str
    objective_type: CampaignObjectiveType
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    linked_candidate_ids: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    stop_criteria: list[str] = Field(default_factory=list)
    priority_weight: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignWorkPackage(BaseModel):
    work_package_id: str
    campaign_id: str
    objective_ids: list[str] = Field(default_factory=list)
    package_type: CampaignWorkPackageType
    title: str
    description: str
    linked_candidate_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    high_level_activity_category: str
    dependencies: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    cost_units: str | None = None
    estimated_review_hours: float | None = Field(default=None, ge=0.0)
    estimated_compute_units: float | None = Field(default=None, ge=0.0)
    estimated_assay_slots: int | None = Field(default=None, ge=0)
    status: CampaignWorkPackageStatus
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_procedural_lab_details(self) -> Self:
        text = " ".join(
            [
                self.title,
                self.description,
                self.high_level_activity_category,
                *self.dependencies,
                *self.required_approvals,
                *self.blocking_reasons,
                *self.warnings,
                *(str(value) for value in self.metadata.values()),
            ]
        )
        if contains_procedural_lab_detail(text):
            raise ValueError("CampaignWorkPackage must not include procedural lab details")
        return self


class CampaignBudget(BaseModel):
    budget_id: str
    campaign_id: str
    max_total_cost: float | None = Field(default=None, ge=0.0)
    cost_units: str | None = None
    max_assay_slots: int | None = Field(default=None, ge=0)
    max_review_hours: float | None = Field(default=None, ge=0.0)
    max_compute_units: float | None = Field(default=None, ge=0.0)
    max_codex_tasks: int | None = Field(default=None, ge=0)
    max_external_sync_jobs: int | None = Field(default=None, ge=0)
    reserved_budget: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignPlan(TimezoneAwareCampaignModel):
    campaign_plan_id: str
    campaign_id: str
    objectives: list[CampaignObjective] = Field(default_factory=list)
    work_packages: list[CampaignWorkPackage] = Field(default_factory=list)
    budget: CampaignBudget
    stage_gates: list[dict[str, Any]] = Field(default_factory=list)
    dependency_graph: dict[str, Any] = Field(default_factory=dict)
    expected_learning_value: float = Field(ge=0.0, le=1.0)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    uncertainty_summary: dict[str, Any] = Field(default_factory=dict)
    budget_summary: dict[str, Any] = Field(default_factory=dict)
    recommended_sequence: list[str] = Field(default_factory=list)
    replan_triggers: list[str] = Field(default_factory=list)
    human_approval_required: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignExecutionEvent(TimezoneAwareCampaignModel):
    event_id: str
    campaign_id: str
    work_package_id: str | None = None
    event_type: CampaignExecutionEventType
    actor: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanTrigger(BaseModel):
    trigger_id: str
    campaign_id: str
    trigger_type: ReplanTriggerType
    severity: ReplanSeverity
    description: str
    linked_entity_ids: list[str] = Field(default_factory=list)
    recommended_action: ReplanRecommendedAction
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignMemo(TimezoneAwareCampaignModel):
    memo_id: str
    campaign_id: str
    title: str
    executive_summary: str
    objectives_summary: str
    selected_work_packages: list[str] = Field(default_factory=list)
    budget_summary: str
    key_tradeoffs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    replan_triggers: list[str] = Field(default_factory=list)
    approvals_required: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


def contains_procedural_lab_detail(text: str) -> bool:
    return bool(_PROCEDURAL_LAB_DETAIL_RE.search(text))
