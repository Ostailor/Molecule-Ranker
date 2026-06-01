from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

CampaignWorkType = Literal[
    "expert_review",
    "assay_slot_allocation",
    "computation_slot_allocation",
    "integration_review",
    "decision_gate_review",
]
CampaignWorkStatus = Literal[
    "planned",
    "ready_for_review",
    "blocked",
    "deferred",
    "completed",
]
CampaignEventType = Literal[
    "assay_result_imported",
    "review_decision_recorded",
    "model_prediction_updated",
    "graph_contradiction_detected",
    "integration_data_updated",
]
CampaignReplanTriggerType = Literal[
    "new_assay_result",
    "review_decision",
    "model_prediction_change",
    "graph_contradiction",
    "integration_update",
]

CAMPAIGN_BOUNDARIES = [
    "Campaign plans are research-management artifacts, not lab protocols.",
    "No synthesis routes, reagents, concentrations, incubation times, temperatures, dosing, "
    "or patient treatment guidance are provided.",
    "Campaign priorities, budget fit, dependencies, and replan triggers are deterministic.",
    "Codex may draft only from deterministic campaign artifacts.",
    "Generated molecules remain computational hypotheses unless exact imported experimental "
    "evidence exists.",
]

_PROTOCOL_DETAIL_PATTERN = re.compile(
    r"\b("
    r"\d+(?:\.\d+)?\s*(?:nm|um|mm|mg|ml|ul|hours?|hrs?|minutes?|mins?|seconds?|secs?|c)"
    r"|concentration|temperature|reagent|reagents|incubate|incubation|pipette|wash|"
    r"centrifuge|protocol step|step-by-step|synthetic route|synthesis route|animal dosing|"
    r"human dosing|patient treatment"
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


class CampaignBudget(BaseModel):
    budget_id: str = "default-campaign-budget"
    max_work_packages: int | None = Field(default=8, ge=0)
    max_assay_slots: int | None = Field(default=None, ge=0)
    max_review_slots: int | None = Field(default=None, ge=0)
    max_computation_slots: int | None = Field(default=None, ge=0)
    max_total_cost: float | None = Field(default=None, ge=0.0)
    cost_units: str | None = None
    require_human_approval: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignResourceEstimate(BaseModel):
    assay_slots: int = Field(default=0, ge=0)
    review_slots: int = Field(default=0, ge=0)
    computation_slots: int = Field(default=0, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    cost_units: str | None = None
    cost_provenance_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_cost_provenance(self) -> Self:
        if self.estimated_cost is not None and not self.cost_provenance_ids:
            raise ValueError("estimated campaign costs require cost provenance")
        return self


class ReviewGate(BaseModel):
    gate_id: str = Field(default_factory=lambda: f"campaign-gate-{uuid4().hex[:12]}")
    required: bool = True
    gate_type: str = "human_review"
    required_approvals: list[str] = Field(default_factory=list)
    decision: str | None = None
    rationale: str = "Human review is required before configured campaign advancement."
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignWorkPackage(TimezoneAwareCampaignModel):
    work_package_id: str
    work_type: CampaignWorkType
    title: str
    hypothesis_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    high_level_followup_categories: list[str] = Field(default_factory=list)
    priority_score: float = Field(ge=0.0, le=1.0)
    expected_learning_value: float = Field(ge=0.0, le=1.0)
    opportunity_cost_score: float = Field(default=0.0, ge=0.0, le=1.0)
    resource_estimate: CampaignResourceEstimate = Field(default_factory=CampaignResourceEstimate)
    dependencies: list[str] = Field(default_factory=list)
    review_gate: ReviewGate = Field(default_factory=ReviewGate)
    status: CampaignWorkStatus = "planned"
    not_lab_protocol: bool = True
    provenance_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_protocol_details(self) -> Self:
        text = " ".join(
            [
                self.title,
                *self.high_level_followup_categories,
                *self.dependencies,
                self.review_gate.rationale,
                *self.warnings,
                *(str(value) for value in self.metadata.values()),
            ]
        )
        if _PROTOCOL_DETAIL_PATTERN.search(text):
            self.not_lab_protocol = False
            raise ValueError("CampaignWorkPackage must not include protocol-level details")
        if not self.provenance_ids:
            raise ValueError("CampaignWorkPackage requires provenance IDs")
        self.not_lab_protocol = True
        return self


class CampaignSlotAllocation(BaseModel):
    allocation_id: str
    work_package_id: str
    assay_slot_ids: list[str] = Field(default_factory=list)
    review_slot_ids: list[str] = Field(default_factory=list)
    computation_slot_ids: list[str] = Field(default_factory=list)
    allocated_cost: float | None = None
    cost_provenance_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_cost_provenance(self) -> Self:
        if self.allocated_cost is not None and not self.cost_provenance_ids:
            raise ValueError("allocated campaign costs require cost provenance")
        return self


class CampaignBudgetFit(BaseModel):
    budget_id: str
    within_budget: bool
    work_packages_selected: int = Field(ge=0)
    work_packages_deferred: int = Field(ge=0)
    assay_slots_used: int = Field(ge=0)
    review_slots_used: int = Field(ge=0)
    computation_slots_used: int = Field(ge=0)
    total_cost: float | None = Field(default=None, ge=0.0)
    unknown_cost_work_package_ids: list[str] = Field(default_factory=list)
    limiting_factors: list[str] = Field(default_factory=list)


class CampaignEvent(TimezoneAwareCampaignModel):
    event_id: str
    event_type: CampaignEventType
    source_artifact_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    linked_candidate_ids: list[str] = Field(default_factory=list)
    summary: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_source_artifacts(self) -> Self:
        if not self.source_artifact_ids:
            raise ValueError("Campaign events require source artifact IDs")
        if _PROTOCOL_DETAIL_PATTERN.search(self.summary):
            raise ValueError("Campaign events must not include protocol-level details")
        return self


class CampaignReplanTrigger(BaseModel):
    trigger_id: str
    trigger_type: CampaignReplanTriggerType
    source_event_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    linked_candidate_ids: list[str] = Field(default_factory=list)
    rationale: str
    requires_human_review: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignAuditEvent(TimezoneAwareCampaignModel):
    event_id: str
    event_type: str
    summary: str
    actor: str = "campaign-planner"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeferredCampaignWorkPackage(BaseModel):
    work_package_id: str
    priority_score: float = Field(ge=0.0, le=1.0)
    expected_learning_value: float = Field(ge=0.0, le=1.0)
    opportunity_cost_score: float = Field(ge=0.0, le=1.0)
    defer_reason: str
    candidate_ids: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignPlan(TimezoneAwareCampaignModel):
    campaign_id: str
    schema_version: str = "1.7"
    title: str
    hypothesis_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    work_packages: list[CampaignWorkPackage] = Field(default_factory=list)
    deferred_work_packages: list[DeferredCampaignWorkPackage] = Field(default_factory=list)
    allocations: list[CampaignSlotAllocation] = Field(default_factory=list)
    budget: CampaignBudget = Field(default_factory=CampaignBudget)
    budget_fit: CampaignBudgetFit
    replan_triggers: list[CampaignReplanTrigger] = Field(default_factory=list)
    audit_trail: list[CampaignAuditEvent] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(CAMPAIGN_BOUNDARIES))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


def reject_campaign_protocol_details(text: str) -> None:
    if _PROTOCOL_DETAIL_PATTERN.search(text):
        raise ValueError("campaign artifact contains protocol-level details")


def campaign_protocol_violations(text: str) -> list[str]:
    return ["protocol-level details"] if _PROTOCOL_DETAIL_PATTERN.search(text) else []
