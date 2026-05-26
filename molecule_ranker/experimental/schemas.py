from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator, model_validator

AssayOutcome = Literal["positive", "negative", "inconclusive", "failed"]
AssayValidationStatus = Literal["valid", "incomplete", "invalid"]
AssayResultSourceType = Literal["user_imported_file", "explicit_user_input", "connected_system"]

EXPERIMENTAL_LIMITATIONS = [
    (
        "Experimental results must come from user-imported files, explicit user input, "
        "or connected result systems."
    ),
    "Assay results do not establish clinical efficacy, disease treatment, or cure.",
    "In-vitro or model-system results do not prove patient benefit.",
    "Generated molecules remain unvalidated unless directly linked imported results support them.",
    (
        "No lab protocol, synthesis route, dosing instruction, patient instruction, "
        "or wet-lab execution workflow is provided."
    ),
    "Expert review decisions are tracked separately from experimental evidence.",
]


class TimezoneAwareExperimentalModel(BaseModel):
    @field_validator("imported_at", "generated_at", check_fields=False)
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class AssayResult(TimezoneAwareExperimentalModel):
    """User-supplied experimental assay result with provenance and conservative status."""

    result_id: str = ""
    experiment_id: str | None = None
    assay_name: str | None = None
    assay_type: str | None = None
    molecule_name: str | None = None
    candidate_id: str | None = None
    generated_molecule_name: str | None = None
    target_symbol: str | None = None
    disease_name: str | None = None
    review_item_id: str | None = None
    validation_handoff_id: str | None = None
    outcome: AssayOutcome | None = None
    value: float | None = None
    unit: str | None = None
    qualifier: str | None = None
    direction: Literal["higher_is_better", "lower_is_better", "neutral", "unknown"] = "unknown"
    result_date: str | None = None
    source_path: str | None = None
    source_row: int | None = None
    evidence_channel: Literal["experimental"] = "experimental"
    validation_status: AssayValidationStatus = "valid"
    validation_issues: list[str] = Field(default_factory=list)
    linked_candidate_name: str | None = None
    linked_candidate_id: str | None = None
    linked_generated_molecule_name: str | None = None
    linked_review_item_id: str | None = None
    linked_validation_handoff_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def fill_id_and_validation_status(self) -> Self:
        issues = list(dict.fromkeys(self.validation_issues + self._required_field_issues()))
        status = self.validation_status
        if status != "invalid":
            status = "incomplete" if issues else "valid"
        self.validation_issues = issues
        self.validation_status = status
        if not self.result_id:
            self.result_id = _hashed_id(
                "assay-result",
                self.experiment_id,
                self.assay_name,
                self.candidate_id,
                self.molecule_name,
                self.generated_molecule_name,
                self.target_symbol,
                self.outcome,
                self.source_path,
                self.source_row,
                json.dumps(self.metadata.get("raw", {}), sort_keys=True, default=str),
            )
        return self

    def _required_field_issues(self) -> list[str]:
        issues: list[str] = []
        if not _has_text(self.experiment_id):
            issues.append("experiment_id is required")
        if not _has_text(self.assay_name):
            issues.append("assay_name is required")
        if not any(
            _has_text(value)
            for value in [self.molecule_name, self.candidate_id, self.generated_molecule_name]
        ):
            issues.append("molecule_name, candidate_id, or generated_molecule_name is required")
        if self.outcome is None:
            issues.append("outcome is required")
        return issues


class AssayResultValidationReport(TimezoneAwareExperimentalModel):
    total_count: int = 0
    valid_count: int = 0
    incomplete_count: int = 0
    invalid_count: int = 0
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    row_issues: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(EXPERIMENTAL_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssayImportResult(TimezoneAwareExperimentalModel):
    source_path: str
    source_format: Literal["csv", "json", "inline"]
    results: list[AssayResult] = Field(default_factory=list)
    validation_report: AssayResultValidationReport
    limitations: list[str] = Field(default_factory=lambda: list(EXPERIMENTAL_LIMITATIONS))
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentSummaryReport(TimezoneAwareExperimentalModel):
    result_count: int = 0
    valid_count: int = 0
    incomplete_count: int = 0
    invalid_count: int = 0
    experiment_count: int = 0
    assay_count: int = 0
    linked_candidate_count: int = 0
    generated_link_count: int = 0
    review_link_count: int = 0
    validation_handoff_link_count: int = 0
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    experiments: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(EXPERIMENTAL_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CandidateRecalibration(BaseModel):
    candidate_id: str
    candidate_name: str
    original_score: float | None = Field(default=None, ge=0.0, le=1.0)
    recalibrated_score: float | None = Field(default=None, ge=0.0, le=1.0)
    experimental_score_delta: float = Field(ge=-1.0, le=1.0)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    evidence_result_ids: list[str] = Field(default_factory=list)
    explanation: str


class CandidateRecalibrationReport(TimezoneAwareExperimentalModel):
    recalibrations: list[CandidateRecalibration] = Field(default_factory=list)
    excluded_result_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(EXPERIMENTAL_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActiveLearningRecommendation(BaseModel):
    candidate_id: str
    candidate_name: str
    priority_score: float = Field(ge=0.0, le=1.0)
    expected_information_gain: float = Field(ge=0.0, le=1.0)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    rationale: str
    evidence_gap: str


class ActiveLearningReport(TimezoneAwareExperimentalModel):
    recommendations: list[ActiveLearningRecommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(EXPERIMENTAL_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _hashed_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}-{uuid5(NAMESPACE_URL, raw).hex[:16]}"


def _has_text(value: object) -> bool:
    return bool(str(value or "").strip())
