from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

WorkflowType = Literal[
    "disease_to_ranked_candidates",
    "disease_to_generated_hypotheses",
    "disease_to_antibody_candidates",
    "disease_to_review_workspace",
    "disease_to_campaign_plan",
    "disease_to_portfolio_and_campaign",
    "full_discovery_loop",
    "full_discovery_loop_with_biologics",
    "biologics_discovery_loop",
    "integration_sync_loop",
    "prospective_evaluation_loop",
]
WorkflowMode = Literal[
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]
WorkflowStatus = Literal[
    "planned",
    "running",
    "awaiting_approval",
    "succeeded",
    "failed",
    "cancelled",
    "partially_succeeded",
]
WorkflowStepType = Literal[
    "project_setup",
    "disease_resolution",
    "target_discovery",
    "molecule_retrieval",
    "antigen_context",
    "antibody_retrieval",
    "antibody_generation",
    "antibody_sequence_validation",
    "antibody_numbering",
    "antibody_novelty",
    "antibody_developability",
    "antibody_review",
    "literature_retrieval",
    "generation",
    "developability",
    "experimental_import",
    "graph_build",
    "hypothesis_generation",
    "portfolio_optimization",
    "campaign_planning",
    "review_workspace",
    "integration_sync",
    "evaluation",
    "report_bundle",
    "codex_summary",
    "approval_gate",
]
WorkflowStepStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "awaiting_approval",
]
LineageRelationType = Literal[
    "produced",
    "consumed",
    "imported_from",
    "exported_to",
    "derived_from",
    "synced_from",
    "synced_to",
    "validated_by",
    "rejected_by",
    "mapped_to",
    "pending_mapping",
    "approved_mapping",
    "blocked_by_policy",
    "approved_for",
    "blocked_by",
    "repaired_by",
]

NOT_SCIENTIFIC_EVIDENCE_LIMITATION = (
    "End-to-end result bundles are workflow audit artifacts, not scientific evidence."
)


class EndToEndSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class EndToEndWorkflow(EndToEndSchema):
    workflow_id: str
    name: str
    workflow_type: WorkflowType
    disease_name: str | None
    project_id: str | None
    campaign_id: str | None
    mode: WorkflowMode
    requested_by: str | None
    autonomy_level: str
    status: WorkflowStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndWorkflowStep(EndToEndSchema):
    step_id: str
    workflow_id: str
    step_index: int = Field(ge=0)
    step_name: str
    step_type: WorkflowStepType
    required: bool
    tool_name: str | None
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    external_system_ids: list[str] = Field(default_factory=list)
    status: WorkflowStepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndResultBundle(EndToEndSchema):
    bundle_id: str
    workflow_id: str
    project_id: str | None
    disease_name: str | None
    result_summary: str
    key_artifact_ids: list[str] = Field(default_factory=list)
    candidate_summary: dict[str, Any] = Field(default_factory=dict)
    generated_summary: dict[str, Any] = Field(default_factory=dict)
    biologics_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    campaign_summary: dict[str, Any] = Field(default_factory=dict)
    evaluation_summary: dict[str, Any] = Field(default_factory=dict)
    integration_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_bundle_is_not_scientific_evidence(self) -> EndToEndResultBundle:
        evidence_flags = {
            "scientific_evidence",
            "is_scientific_evidence",
            "evidence_record",
            "biomedical_truth",
        }
        if any(self.metadata.get(flag) is True for flag in evidence_flags):
            raise ValueError("End-to-end result bundle is not scientific evidence itself.")
        limitation_text = " ".join(self.limitations).lower()
        if "not scientific evidence" not in limitation_text:
            self.limitations.append(NOT_SCIENTIFIC_EVIDENCE_LIMITATION)
        if self.biologics_summary.get("antibody_generation_enabled") is True:
            if self.biologics_summary.get("review_gate_required") is not True:
                raise ValueError("Antibody generation requires an explicit review gate.")
            if self.biologics_summary.get("generated_antibodies_with_direct_evidence", 0):
                exact_results = self.biologics_summary.get("exact_imported_experimental_result_ids")
                if not exact_results:
                    raise ValueError(
                        "Generated antibodies need exact imported experimental results "
                        "before direct evidence can be recorded."
                    )
        return self


class WorkflowLineageRecord(EndToEndSchema):
    lineage_id: str
    workflow_id: str
    source_object_type: str
    source_object_id: str
    target_object_type: str
    target_object_id: str
    relation_type: LineageRelationType
    artifact_ids: list[str] = Field(default_factory=list)
    external_record_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndValidationResult(EndToEndSchema):
    validation_id: str
    workflow_id: str
    passed: bool
    required_artifacts_present: bool
    artifact_contracts_valid: bool
    lineage_complete: bool
    guardrails_passed: bool
    external_sync_validated: bool
    approvals_satisfied: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "EndToEndResultBundle",
    "EndToEndValidationResult",
    "EndToEndWorkflow",
    "EndToEndWorkflowStep",
    "LineageRelationType",
    "WorkflowLineageRecord",
    "WorkflowMode",
    "WorkflowStatus",
    "WorkflowStepStatus",
    "WorkflowStepType",
    "WorkflowType",
]
