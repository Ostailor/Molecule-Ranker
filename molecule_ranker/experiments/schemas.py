from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

EndpointCategory = Literal[
    "potency",
    "target_engagement",
    "phenotypic",
    "safety",
    "developability",
    "selectivity",
    "quality_control",
    "other",
]
EndpointDirectionality = Literal[
    "lower_is_better",
    "higher_is_better",
    "binary",
    "categorical",
    "neutral",
]
AssayType = Literal[
    "biochemical",
    "cellular",
    "phenotypic",
    "safety",
    "developability",
    "computational_validation",
    "other",
]
CandidateOrigin = Literal["existing", "generated", "unknown"]
OutcomeLabel = Literal[
    "positive",
    "negative",
    "inconclusive",
    "failed_qc",
    "not_tested",
    "invalid",
]
ActivityDirection = Literal[
    "active",
    "inactive",
    "toxic",
    "non_toxic",
    "improved",
    "worsened",
    "no_effect",
    "ambiguous",
    "not_applicable",
]
QcStatus = Literal["passed", "failed", "partial", "unknown"]


class TimezoneAwareExperimentModel(BaseModel):
    @field_validator("created_at", "imported_at", "timestamp", check_fields=False)
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class AssayEndpoint(BaseModel):
    endpoint_id: str
    name: str
    endpoint_category: EndpointCategory
    unit: str | None = None
    directionality: EndpointDirectionality
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssayContext(BaseModel):
    assay_context_id: str
    assay_name: str
    assay_type: AssayType
    target_symbol: str | None = None
    target_identifiers: dict[str, str] = Field(default_factory=dict)
    disease_name: str | None = None
    model_system: str | None = None
    species: str | None = None
    endpoint: AssayEndpoint
    protocol_reference: str | None = None
    protocol_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_operational_protocol_details(self) -> Self:
        _reject_protocol_content([self.protocol_reference, self.protocol_summary])
        return self


class AssayResult(TimezoneAwareExperimentModel):
    result_id: str
    run_id: str | None = None
    workspace_id: str | None = None
    review_item_id: str | None = None
    candidate_id: str | None = None
    candidate_name: str
    candidate_origin: CandidateOrigin
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    disease_name: str | None = None
    target_symbol: str | None = None
    assay_context: AssayContext
    measured_value: float | str | bool | None = None
    measured_value_numeric: float | None = None
    unit: str | None = None
    relation: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    outcome_label: OutcomeLabel
    activity_direction: ActivityDirection
    replicate_count: int | None = Field(default=None, ge=0)
    replicate_values: list[float] = Field(default_factory=list)
    uncertainty: float | None = Field(default=None, ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    qc_status: QcStatus
    result_date: date | None = None
    source: str
    source_record_id: str | None = None
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    imported_by: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentalEvidenceSummary(BaseModel):
    candidate_id: str | None = None
    candidate_name: str
    candidate_origin: CandidateOrigin
    result_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)
    failed_qc_count: int = Field(ge=0)
    endpoint_summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    best_supporting_results: list[str] = Field(default_factory=list)
    key_negative_results: list[str] = Field(default_factory=list)
    safety_concerns: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    interpretation: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentalLearningDataset(TimezoneAwareExperimentModel):
    dataset_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disease_name: str | None = None
    target_symbol: str | None = None
    endpoint_name: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    feature_schema: dict[str, Any] = Field(default_factory=dict)
    label_schema: dict[str, Any] = Field(default_factory=dict)
    included_result_ids: list[str] = Field(default_factory=list)
    excluded_result_ids: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveLearningSuggestion(BaseModel):
    suggestion_id: str
    candidate_id: str | None = None
    candidate_name: str
    candidate_origin: CandidateOrigin
    target_symbol: str | None = None
    canonical_smiles: str | None = None
    acquisition_score: float = Field(ge=0.0, le=1.0)
    acquisition_strategy: str
    rationale: str
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    diversity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_value_score: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_penalty: float | None = Field(default=None, ge=0.0, le=1.0)
    constraints_satisfied: bool
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveLearningBatch(TimezoneAwareExperimentModel):
    batch_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disease_name: str | None = None
    target_symbol: str | None = None
    endpoint_name: str
    strategy: str
    suggestions: list[ActiveLearningSuggestion] = Field(default_factory=list)
    excluded_candidates: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentAuditEvent(TimezoneAwareExperimentModel):
    event_id: str
    event_type: str
    actor: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    object_type: str
    object_id: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _reject_protocol_content(values: list[str | None]) -> None:
    text = "\n".join(value for value in values if value).lower()
    if not text:
        return
    forbidden_patterns = [
        r"\bstep\s*\d+\b",
        r"\bstep-by-step\b",
        r"\breagent\b",
        r"\breagents\b",
        r"\brecipe\b",
        r"\breaction condition",
        r"\bsynthesis\b",
        r"\bsynthesis route\b",
        r"\bdose\b",
        r"\bdosing\b",
        r"\bmg/kg\b",
        r"\badminister\b",
        r"\bincubat",
        r"\btemperature\b",
        r"\b\d+\s*(?:°c|c\b|celsius)\b",
        r"\b\d+\s*(?:min|mins|minute|minutes|h|hr|hour|hours)\b",
    ]
    if any(re.search(pattern, text) for pattern in forbidden_patterns):
        raise ValueError(
            "AssayContext protocol fields must not include step-by-step protocols, "
            "reagent recipes, incubation times, temperatures, synthesis instructions, "
            "or dosing instructions."
        )
