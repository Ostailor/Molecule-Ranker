from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

BenchmarkTaskType = Literal[
    "candidate_ranking",
    "molecule_generation",
    "developability_triage",
    "surrogate_prediction",
    "structure_prioritization",
    "portfolio_selection",
    "hypothesis_prioritization",
    "campaign_planning",
    "codex_guardrail",
    "integration_data_quality",
]

BenchmarkDatasetType = Literal[
    "imported_assay_results",
    "synthetic_validation",
    "frozen_project_artifacts",
    "external_benchmark",
    "integration_fixture",
]

BenchmarkSplitType = Literal[
    "random",
    "scaffold",
    "time_based",
    "project_based",
    "prospective",
    "external_holdout",
]

EvaluationMetricType = Literal[
    "ranking",
    "classification",
    "regression",
    "calibration",
    "generation",
    "diversity",
    "guardrail",
    "decision_quality",
    "reproducibility",
    "cost_efficiency",
]

ProspectiveValidationStatus = Literal[
    "frozen",
    "awaiting_outcomes",
    "outcomes_imported",
    "evaluated",
    "invalid",
]


class EvaluationSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class BenchmarkSuite(EvaluationSchema):
    suite_id: str
    name: str
    version: str
    description: str
    tasks: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkTask(EvaluationSchema):
    task_id: str
    suite_id: str
    name: str
    task_type: BenchmarkTaskType
    endpoint_name: str | None = None
    disease_name: str | None = None
    target_symbol: str | None = None
    objective: str
    input_artifact_ids: list[str] = Field(default_factory=list)
    label_artifact_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkDataset(EvaluationSchema):
    dataset_id: str
    name: str
    dataset_type: BenchmarkDatasetType
    source_artifact_ids: list[str] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    candidate_count: int | None = Field(default=None, ge=0)
    label_count: int | None = Field(default=None, ge=0)
    created_at: datetime
    data_contract_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkSplit(EvaluationSchema):
    split_id: str
    dataset_id: str
    split_type: BenchmarkSplitType
    train_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)
    frozen_at: datetime
    leakage_checks: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FrozenPredictionSet(EvaluationSchema):
    prediction_set_id: str
    task_id: str
    model_or_pipeline_version: str
    frozen_at: datetime
    prediction_artifact_id: str
    input_candidate_ids: list[str] = Field(default_factory=list)
    prediction_count: int = Field(ge=0)
    outcome_labels_available: bool
    outcome_artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationMetric(EvaluationSchema):
    metric_id: str
    name: str
    metric_type: EvaluationMetricType
    value: float | str | bool | None = None
    confidence_interval: dict[str, float] | None = None
    higher_is_better: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(EvaluationSchema):
    evaluation_id: str
    suite_id: str | None = None
    task_id: str
    dataset_id: str
    split_id: str | None = None
    prediction_set_id: str | None = None
    metrics: list[EvaluationMetric] = Field(default_factory=list)
    baseline_metrics: list[EvaluationMetric] = Field(default_factory=list)
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProspectiveValidationRun(EvaluationSchema):
    prospective_run_id: str
    project_id: str | None = None
    campaign_id: str | None = None
    task_id: str
    frozen_prediction_set_id: str
    frozen_before_outcomes: bool
    outcome_imported_at: datetime | None = None
    evaluation_report_id: str | None = None
    status: ProspectiveValidationStatus
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionQualityReport(EvaluationSchema):
    report_id: str
    project_id: str | None = None
    campaign_id: str | None = None
    decision_artifact_ids: list[str] = Field(default_factory=list)
    outcome_artifact_ids: list[str] = Field(default_factory=list)
    metrics: list[EvaluationMetric] = Field(default_factory=list)
    decision_summary: dict[str, Any] = Field(default_factory=dict)
    lessons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReproducibilityManifest(EvaluationSchema):
    manifest_id: str
    run_id: str | None = None
    suite_id: str | None = None
    code_version: str
    artifact_contract_version: str
    config_hash: str
    input_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    output_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    random_seeds: dict[str, Any] = Field(default_factory=dict)
    dependency_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "BenchmarkDataset",
    "BenchmarkDatasetType",
    "BenchmarkSplit",
    "BenchmarkSplitType",
    "BenchmarkSuite",
    "BenchmarkTask",
    "BenchmarkTaskType",
    "DecisionQualityReport",
    "EvaluationMetric",
    "EvaluationMetricType",
    "EvaluationReport",
    "FrozenPredictionSet",
    "ProspectiveValidationRun",
    "ProspectiveValidationStatus",
    "ReproducibilityManifest",
]
