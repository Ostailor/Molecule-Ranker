from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.e2e.schemas import EndToEndValidationResult
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import (
    EndToEndWorkflowRunner,
    EndToEndWorkflowRunnerConfig,
    WorkflowRunRequest,
    WorkflowRunResult,
)


class HostedE2EWorkflowCreateRequest(BaseModel):
    workflow: str = "full_discovery_loop"
    disease_name: str | None = None
    disease: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    mode: str = "mocked"
    requested_by: str | None = None
    autonomy: str = "governed"
    enable_generation: bool = False
    enable_structure: bool = False
    enable_integrations: bool = False
    enable_codex_summary: bool = False
    dry_run: bool = False
    requested_external_write: bool = False
    approvals: list[str] = Field(default_factory=list)
    governance_permissions: list[str] = Field(default_factory=list)
    unavailable_required_data: list[str] = Field(default_factory=list)
    partial_on_live_data_unavailable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HostedE2EWorkflowRecord(BaseModel):
    request: WorkflowRunRequest
    result: WorkflowRunResult
    validation: EndToEndValidationResult
    created_at: datetime
    updated_at: datetime

    @property
    def workflow_id(self) -> str:
        return self.result.workflow.workflow_id


class HostedE2EWorkflowStore:
    """In-process hosted workflow store for V2.8 e2e operations."""

    def __init__(self) -> None:
        self._records: dict[str, HostedE2EWorkflowRecord] = {}

    def create(self, request: HostedE2EWorkflowCreateRequest) -> HostedE2EWorkflowRecord:
        workflow_request = _workflow_request(request)
        return self._run_and_store(workflow_request, created_at=datetime.now(UTC))

    def list(self) -> list[HostedE2EWorkflowRecord]:
        return sorted(
            self._records.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )

    def get(self, workflow_id: str) -> HostedE2EWorkflowRecord | None:
        return self._records.get(workflow_id)

    def resume(self, workflow_id: str) -> HostedE2EWorkflowRecord:
        record = self.require(workflow_id)
        if record.result.workflow.status == "cancelled":
            raise ValueError("cancelled workflows must be cloned or restarted")
        request = record.request.model_copy(
            update={
                "unavailable_required_data": [],
                "metadata": {
                    **record.request.metadata,
                    "workflow_id": workflow_id,
                    "resumed_from_status": record.result.workflow.status,
                },
            }
        )
        return self._run_and_store(request, created_at=record.created_at)

    def cancel(self, workflow_id: str) -> HostedE2EWorkflowRecord:
        record = self.require(workflow_id)
        workflow = record.result.workflow.model_copy(
            update={"status": "cancelled", "completed_at": datetime.now(UTC)}
        )
        result = record.result.model_copy(update={"workflow": workflow})
        updated = record.model_copy(update={"result": result, "updated_at": datetime.now(UTC)})
        self._records[workflow_id] = updated
        return updated

    def validate(self, workflow_id: str) -> HostedE2EWorkflowRecord:
        record = self.require(workflow_id)
        validation = EndToEndWorkflowValidator().validate_run_result(record.result)
        updated = record.model_copy(
            update={"validation": validation, "updated_at": datetime.now(UTC)}
        )
        self._records[workflow_id] = updated
        return updated

    def require(self, workflow_id: str) -> HostedE2EWorkflowRecord:
        record = self.get(workflow_id)
        if record is None:
            raise KeyError(workflow_id)
        return record

    def _run_and_store(
        self,
        request: WorkflowRunRequest,
        *,
        created_at: datetime,
    ) -> HostedE2EWorkflowRecord:
        result = EndToEndWorkflowRunner().run(request)
        _normalize_dry_run_integration_summary(result, request)
        validation = EndToEndWorkflowValidator().validate_run_result(result)
        record = HostedE2EWorkflowRecord(
            request=request,
            result=result,
            validation=validation,
            created_at=created_at,
            updated_at=datetime.now(UTC),
        )
        self._records[result.workflow.workflow_id] = record
        return record


def _workflow_request(request: HostedE2EWorkflowCreateRequest) -> WorkflowRunRequest:
    mode = "dry_run" if request.dry_run else request.mode
    return WorkflowRunRequest(
        workflow_type=request.workflow,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        disease_name=request.disease_name or request.disease,
        project_id=request.project_id,
        campaign_id=request.campaign_id,
        requested_by=request.requested_by,
        autonomy_level=request.autonomy,
        requested_external_write=request.requested_external_write or request.enable_integrations,
        approvals=request.approvals,
        governance_permissions=request.governance_permissions,
        unavailable_required_data=request.unavailable_required_data,
        config=EndToEndWorkflowRunnerConfig(
            partial_on_live_data_unavailable=request.partial_on_live_data_unavailable
        ),
        metadata={
            **request.metadata,
            "hosted_api": True,
            "enable_generation": request.enable_generation,
            "enable_structure": request.enable_structure,
            "enable_integrations": request.enable_integrations,
            "enable_codex_summary": request.enable_codex_summary,
            "dry_run_option": request.dry_run,
        },
    )


def _normalize_dry_run_integration_summary(
    result: WorkflowRunResult,
    request: WorkflowRunRequest,
) -> None:
    if request.mode != "dry_run":
        return
    if not (
        request.requested_external_write
        or request.metadata.get("enable_integrations") is True
    ):
        return
    result.planned_external_writes = max(result.planned_external_writes, 1)
    result.external_writes_performed = 0
    if result.bundle is not None:
        result.bundle.integration_summary["planned_external_writes"] = (
            result.planned_external_writes
        )
        result.bundle.integration_summary["external_writes_performed"] = 0
        result.bundle.integration_summary["dry_run"] = True


def hosted_e2e_record_payload(record: HostedE2EWorkflowRecord) -> dict[str, Any]:
    return {
        "workflow": record.result.workflow.model_dump(mode="json"),
        "steps": [step.model_dump(mode="json") for step in record.result.steps],
        "validation": record.validation.model_dump(mode="json"),
        "external_writes_performed": record.result.external_writes_performed,
        "planned_external_writes": record.result.planned_external_writes,
        "warnings": record.result.warnings,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


__all__ = [
    "HostedE2EWorkflowCreateRequest",
    "HostedE2EWorkflowRecord",
    "HostedE2EWorkflowStore",
    "hosted_e2e_record_payload",
]
