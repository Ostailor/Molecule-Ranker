from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EndpointCategory = Literal[
    "potency",
    "target_engagement",
    "phenotypic",
    "safety",
    "developability",
    "selectivity",
    "other",
]
LabelType = Literal["binary", "regression", "multiclass", "ordinal"]
Directionality = Literal[
    "lower_is_better",
    "higher_is_better",
    "binary",
    "categorical",
    "neutral",
]
Normalization = Literal["none", "standard", "robust", "minmax"]
CandidateOrigin = Literal["existing", "generated", "unknown"]
ApplicabilityDomain = Literal["in_domain", "near_domain", "out_of_domain", "unknown"]
CalibrationStatus = Literal[
    "calibrated",
    "uncalibrated",
    "insufficient_data",
    "not_applicable",
]
TrainingRunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped_insufficient_data",
]


class ModelSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ModelEndpoint(ModelSchema):
    endpoint_id: str
    endpoint_name: str
    endpoint_category: EndpointCategory
    target_symbol: str | None = None
    disease_name: str | None = None
    assay_type: str | None = None
    unit: str | None = None
    label_type: LabelType
    positive_label: str | None = None
    directionality: Directionality
    thresholds: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelFeatureSpec(ModelSchema):
    feature_spec_id: str
    feature_families: list[str] = Field(default_factory=list)
    fingerprint_radius: int | None = None
    fingerprint_bits: int | None = None
    descriptor_names: list[str] = Field(default_factory=list)
    normalization: Normalization = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelTrainingDataset(ModelSchema):
    dataset_id: str
    endpoint: ModelEndpoint
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_result_ids: list[str] = Field(default_factory=list)
    included_candidate_ids: list[str] = Field(default_factory=list)
    excluded_result_ids: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    feature_spec: ModelFeatureSpec
    feature_matrix_uri: str | None = None
    labels_uri: str | None = None
    row_count: int = Field(ge=0)
    positive_count: int | None = Field(default=None, ge=0)
    negative_count: int | None = Field(default=None, ge=0)
    train_count: int | None = Field(default=None, ge=0)
    validation_count: int | None = Field(default=None, ge=0)
    test_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCard(ModelSchema):
    model_id: str
    model_name: str
    model_version: str
    plugin_name: str
    endpoint: ModelEndpoint
    feature_spec: ModelFeatureSpec
    training_dataset_id: str
    training_data_summary: dict[str, Any] = Field(default_factory=dict)
    model_type: str
    intended_use: str
    limitations: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    calibration_metrics: dict[str, Any] = Field(default_factory=dict)
    applicability_domain_method: str
    license: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelPrediction(ModelSchema):
    prediction_id: str
    model_id: str
    model_version: str
    endpoint_id: str
    candidate_id: str | None = None
    candidate_name: str
    candidate_origin: CandidateOrigin
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    predicted_value: float | str | bool | None = None
    predicted_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    prediction_label: str | None = None
    uncertainty: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    applicability_domain: ApplicabilityDomain
    calibration_status: CalibrationStatus
    explanation: str
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelTrainingRun(ModelSchema):
    training_run_id: str
    model_id: str
    dataset_id: str
    status: TrainingRunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    calibration_metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelEvaluationReport(ModelSchema):
    evaluation_id: str
    model_id: str
    dataset_id: str
    split_strategy: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    calibration_metrics: dict[str, Any] = Field(default_factory=dict)
    leakage_checks: dict[str, Any] = Field(default_factory=dict)
    applicability_domain_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ApplicabilityDomain",
    "CalibrationStatus",
    "CandidateOrigin",
    "Directionality",
    "EndpointCategory",
    "LabelType",
    "ModelCard",
    "ModelEndpoint",
    "ModelEvaluationReport",
    "ModelFeatureSpec",
    "ModelPrediction",
    "ModelTrainingDataset",
    "ModelTrainingRun",
    "Normalization",
    "TrainingRunStatus",
]
