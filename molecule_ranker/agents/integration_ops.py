from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.e2e.schemas import WorkflowLineageRecord
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.exporters import (
    ExportPackageResult,
    create_export_package,
)
from molecule_ranker.integrations.mapping import (
    codex_suggest_mapping,
    map_assay_result_to_external,
    map_candidate_to_registry_entry,
    map_review_item_to_external,
)
from molecule_ranker.integrations.schemas import (
    ConnectorHealth,
    DataContract,
    DataContractValidationReport,
    EntityMapping,
    ExternalRecordRef,
    IntegrationAuditEvent,
    SyncJobRecord,
)
from molecule_ranker.integrations.validation import (
    normalize_record,
    validate_data_contract,
)

IntegrationOpsMode = Literal["dry_run", "read_only", "write_enabled"]
IntegrationOpsStatus = Literal[
    "planned",
    "succeeded",
    "failed",
    "approval_required",
    "pending_review",
    "validation_failed",
    "repair_queued",
]

SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


class IntegrationOpsModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class IntegrationOpsRequest(IntegrationOpsModel):
    project_id: str | None = None
    external_system_id: str
    object_types: list[str] = Field(default_factory=lambda: ["assay_result"])
    mode: IntegrationOpsMode = "dry_run"
    requested_by: str | None = None
    requested_external_write: bool = False
    write_approval_id: str | None = None
    governance_permissions: list[str] = Field(default_factory=list)
    assay_results: list[dict[str, Any]] = Field(default_factory=list)
    external_records: list[dict[str, Any]] = Field(default_factory=list)
    data_contract: DataContract | None = None
    webhook_events: list[dict[str, Any]] = Field(default_factory=list)
    export_payload: dict[str, Any] = Field(default_factory=dict)
    output_dir: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationOpsResult(IntegrationOpsModel):
    operation_id: str = Field(default_factory=lambda: f"integration-ops-{uuid4().hex[:16]}")
    status: IntegrationOpsStatus
    mode: IntegrationOpsMode
    sync_job: SyncJobRecord
    health: ConnectorHealth | None = None
    validation_reports: list[DataContractValidationReport] = Field(default_factory=list)
    mapping: EntityMapping | None = None
    mapping_review_queue: list[EntityMapping] = Field(default_factory=list)
    export_package: ExportPackageResult | None = None
    records_seen: int = 0
    records_valid: int = 0
    records_imported: int = 0
    records_failed: int = 0
    records_skipped: int = 0
    external_write_performed: bool = False
    lineage_records: list[WorkflowLineageRecord] = Field(default_factory=list)
    audit_events: list[IntegrationAuditEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationOpsAgent:
    """Deterministic integration operations agent with approval-gated writes."""

    name = "IntegrationOpsAgent"
    _WRITE_PERMISSION = "integration:write"

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def inspect_integration_health(
        self, request: IntegrationOpsRequest
    ) -> IntegrationOpsResult:
        health = ConnectorHealth(
            connector_id=request.external_system_id,
            provider=str(request.metadata.get("provider") or "deterministic"),
            status="ok",
            checked_at=self._now(),
            message="Integration configuration is inspectable through deterministic tools.",
            capabilities=["health", "dry_run_sync", "read_only_sync"],
            limitations=["No secrets loaded into agent context.", "No live writes by default."],
        )
        sync_job = self._sync_job(request, status="planned", mode=self._safe_mode(request.mode))
        return self._result(
            request=request,
            status="succeeded",
            mode=self._safe_mode(request.mode),
            sync_job=sync_job,
            health=health,
            metadata={"health_status": health.status},
            completed_at=self._now(),
        )

    def plan_dry_run_sync(self, request: IntegrationOpsRequest) -> IntegrationOpsResult:
        sync_job = self._sync_job(request, status="dry_run", mode="dry_run")
        return self._result(
            request=request,
            status="planned",
            mode="dry_run",
            sync_job=sync_job,
            warnings=[
                "Dry-run sync planned only; no external writes will be performed.",
                "External data must pass deterministic validation before scoring.",
            ],
            metadata={
                **self._redact_json(request.metadata),
                "planned_actions": [
                    "inspect_integration_health",
                    "validate_data_contracts",
                    "detect_mapping_conflicts",
                    "queue_mapping_review_if_needed",
                ],
                "write_enabled": False,
            },
        )

    def run_read_only_sync(self, request: IntegrationOpsRequest) -> IntegrationOpsResult:
        if request.requested_external_write:
            return self._approval_required_result(
                request,
                "Read-only sync cannot perform external writes.",
            )
        rows = request.assay_results or request.external_records
        sync_job = self._sync_job(
            request,
            status="succeeded",
            mode="read_only",
            rows_seen=len(rows),
        )
        return self._result(
            request=request,
            status="succeeded",
            mode="read_only",
            sync_job=sync_job,
            records_seen=len(rows),
            records_skipped=len(rows),
            warnings=["Read-only sync inspected source records without importing them."],
            completed_at=self._now(),
        )

    def run_write_sync(self, request: IntegrationOpsRequest) -> IntegrationOpsResult:
        if not self._write_authorized(request):
            return self._approval_required_result(
                request,
                "Write-enabled sync requires explicit approval and governance capability.",
            )
        sync_job = self._sync_job(
            request,
            status="succeeded",
            mode="write_enabled",
            rows_seen=len(request.assay_results or request.external_records),
        )
        return self._result(
            request=request,
            status="succeeded",
            mode="write_enabled",
            sync_job=sync_job,
            external_write_performed=True,
            warnings=["External write was approved by governance; Codex did not approve it."],
            completed_at=self._now(),
            metadata={
                "write_approval_id": request.write_approval_id,
                "codex_approved_external_write": False,
            },
        )

    def validate_data_contracts(
        self,
        records: list[dict[str, Any]],
        contract: DataContract,
        request: IntegrationOpsRequest,
    ) -> IntegrationOpsResult:
        report = validate_data_contract(records, contract)
        status: IntegrationOpsStatus = "succeeded" if report.valid else "validation_failed"
        sync_job = self._sync_job(
            request,
            status="succeeded" if report.valid else "failed",
            mode=self._safe_mode(request.mode),
            rows_seen=report.row_count,
            rows_valid=report.row_count - report.issue_count if report.valid else 0,
            rows_rejected=report.issue_count,
        )
        return self._result(
            request=request,
            status=status,
            mode=self._safe_mode(request.mode),
            sync_job=sync_job,
            validation_reports=[report],
            records_seen=report.row_count,
            records_valid=report.row_count if report.valid else 0,
            records_failed=0 if report.valid else report.issue_count,
            completed_at=self._now(),
            metadata={"validation_versions": ["V0.6", contract.version]},
        )

    def detect_mapping_conflicts(
        self,
        *,
        internal_entity: dict[str, Any],
        external_records: list[dict[str, Any]],
        internal_entity_type: str,
        project_id: str | None = None,
        codex_suggested: bool = False,
    ) -> IntegrationOpsResult:
        sanitized_internal = self._redact_json(internal_entity)
        sanitized_records = [self._redact_json(record) for record in external_records]
        external_ref = self._external_ref_from_records(sanitized_records)
        if codex_suggested:
            internal_id = self._internal_entity_id(sanitized_internal, internal_entity_type)
            mapping = codex_suggest_mapping(
                internal_entity_type=internal_entity_type,
                internal_entity_id=internal_id,
                external_ref=external_ref,
                project_id=project_id,
                confidence=0.5,
                metadata={
                    "conflict_type": self._mapping_conflict_type(sanitized_records),
                    "codex_cannot_approve_external_mapping": True,
                },
                created_by=self.name,
            )
        elif internal_entity_type == "assay_result":
            mapping = map_assay_result_to_external(
                sanitized_internal,
                sanitized_records,
                project_id=project_id,
                created_by=self.name,
            )
        elif internal_entity_type == "review_item":
            mapping = map_review_item_to_external(
                sanitized_internal,
                sanitized_records,
                project_id=project_id,
                created_by=self.name,
            )
        else:
            mapping = map_candidate_to_registry_entry(
                sanitized_internal,
                sanitized_records,
                project_id=project_id,
                created_by=self.name,
            )
        status: IntegrationOpsStatus = (
            "pending_review" if mapping.status == "pending_review" else "succeeded"
        )
        request = IntegrationOpsRequest(
            project_id=project_id,
            external_system_id=external_ref.external_system_id,
            object_types=[internal_entity_type],
            metadata={"mapping_status": mapping.status},
        )
        return self._result(
            request=request,
            status=status,
            mode="dry_run",
            sync_job=self._sync_job(request, status="planned", mode="dry_run"),
            mapping=mapping,
            mapping_review_queue=[mapping] if mapping.status == "pending_review" else [],
            warnings=["Mapping conflict queued for review."]
            if mapping.status == "pending_review"
            else [],
            metadata={"mapping_conflict_type": mapping.metadata.get("conflict_type")},
        )

    def queue_mapping_review(
        self,
        mapping: EntityMapping,
        request: IntegrationOpsRequest,
    ) -> IntegrationOpsResult:
        queued = mapping.model_copy(
            update={
                "status": "pending_review",
                "metadata": {
                    **self._redact_json(mapping.metadata),
                    "queued_by": self.name,
                    "codex_suggested_mappings_stay_pending_review": True,
                },
            }
        )
        return self._result(
            request=request,
            status="pending_review",
            mode="dry_run",
            sync_job=self._sync_job(request, status="planned", mode="dry_run"),
            mapping=queued,
            mapping_review_queue=[queued],
            warnings=["Mapping queued for human review."],
        )

    def import_validated_assay_results(
        self, request: IntegrationOpsRequest
    ) -> IntegrationOpsResult:
        contract = request.data_contract
        if contract is None:
            raise ValueError("data_contract is required to import assay results")
        report = validate_data_contract(request.assay_results, contract)
        if not report.valid:
            sync_job = self._sync_job(
                request,
                status="failed",
                mode="read_only",
                rows_seen=report.row_count,
                rows_rejected=report.issue_count,
            )
            return self._result(
                request=request,
                status="validation_failed",
                mode="read_only",
                sync_job=sync_job,
                validation_reports=[report],
                records_seen=report.row_count,
                records_failed=report.issue_count,
                warnings=["Assay import blocked by deterministic validation failure."],
                completed_at=self._now(),
                metadata={"validation_versions": ["V0.6", contract.version]},
            )

        normalized = [normalize_record(record, contract) for record in request.assay_results]
        lineage_records = [
            self._lineage_for_assay_result(request, record)
            for record in normalized
        ]
        sync_job = self._sync_job(
            request,
            status="succeeded",
            mode="read_only",
            rows_seen=len(normalized),
            rows_valid=len(normalized),
        )
        return self._result(
            request=request,
            status="succeeded",
            mode="read_only",
            sync_job=sync_job,
            validation_reports=[report],
            records_seen=len(normalized),
            records_valid=len(normalized),
            records_imported=len(normalized),
            lineage_records=lineage_records,
            completed_at=self._now(),
            metadata={
                "validation_versions": ["V0.6", contract.version],
                "scoring_gate": "validated_assay_results_only",
                "normalized_records": normalized,
            },
        )

    def export_approved_packages(
        self,
        request: IntegrationOpsRequest,
        *,
        package_type: str = "validation_handoff_package",
    ) -> IntegrationOpsResult:
        if request.requested_external_write and not self._write_authorized(request):
            return self._approval_required_result(
                request,
                "Package export requires explicit approval and governance capability.",
            )
        output_dir = Path(request.output_dir or ".molecule-ranker/integration-export")
        package = create_export_package(
            package_type=package_type,  # type: ignore[arg-type]
            payload=self._redact_json(request.export_payload),
            output_dir=output_dir,
            external_write=request.requested_external_write,
            explicit_permission=self._write_authorized(request),
        )
        sync_job = self._sync_job(
            request,
            status="succeeded",
            mode="write_enabled" if package.external_write_ready else "dry_run",
        )
        return self._result(
            request=request,
            status="succeeded",
            mode="write_enabled" if package.external_write_ready else "dry_run",
            sync_job=sync_job,
            export_package=package,
            external_write_performed=package.external_write_ready,
            completed_at=self._now(),
        )

    def monitor_webhook_events(
        self, request: IntegrationOpsRequest
    ) -> IntegrationOpsResult:
        sync_job = self._sync_job(
            request,
            status="succeeded",
            mode="read_only",
            rows_seen=len(request.webhook_events),
        )
        return self._result(
            request=request,
            status="succeeded",
            mode="read_only",
            sync_job=sync_job,
            records_seen=len(request.webhook_events),
            records_skipped=len(request.webhook_events),
            warnings=["Webhook events observed; processing remains read-only/dry-run."],
            completed_at=self._now(),
            metadata={
                "webhook_events": [
                    self._redact_json(event) for event in request.webhook_events
                ]
            },
        )

    def diagnose_sync_failures(
        self, result: IntegrationOpsResult
    ) -> IntegrationOpsResult:
        request = IntegrationOpsRequest(
            project_id=result.sync_job.project_id,
            external_system_id=result.sync_job.connector_id,
            object_types=result.sync_job.metadata.get("object_types", ["assay_result"]),
        )
        causes = []
        if result.records_failed:
            causes.append("deterministic_validation_failed")
        if result.status == "approval_required":
            causes.append("missing_external_write_approval")
        if result.mapping_review_queue:
            causes.append("mapping_review_required")
        return self._result(
            request=request,
            status="succeeded",
            mode="dry_run",
            sync_job=self._sync_job(request, status="planned", mode="dry_run"),
            warnings=result.warnings,
            metadata={"diagnosed_causes": causes or ["no_failure_detected"]},
            completed_at=self._now(),
        )

    def trigger_integration_repairs(
        self, result: IntegrationOpsResult
    ) -> IntegrationOpsResult:
        request = IntegrationOpsRequest(
            project_id=result.sync_job.project_id,
            external_system_id=result.sync_job.connector_id,
            object_types=result.sync_job.metadata.get("object_types", ["assay_result"]),
        )
        return self._result(
            request=request,
            status="repair_queued",
            mode="dry_run",
            sync_job=self._sync_job(request, status="planned", mode="dry_run"),
            warnings=[
                "Repair queued for integration validation or mapping only.",
                "Repair must not fabricate assay results, citations, molecules, or graph facts.",
            ],
            metadata={"source_operation_id": result.operation_id},
        )

    def preserve_lineage(
        self,
        request: IntegrationOpsRequest,
        *,
        source_object_id: str,
        target_object_id: str,
        relation_type: str = "synced_from",
    ) -> WorkflowLineageRecord:
        return WorkflowLineageRecord(
            lineage_id=f"lineage-{uuid4().hex[:16]}",
            workflow_id=f"integration-ops-{request.project_id or 'unscoped'}",
            source_object_type="external_record",
            source_object_id=source_object_id,
            target_object_type="internal_artifact",
            target_object_id=target_object_id,
            relation_type=relation_type,  # type: ignore[arg-type]
            artifact_ids=[target_object_id],
            external_record_refs=[
                {
                    "external_system_id": request.external_system_id,
                    "external_record_id": source_object_id,
                }
            ],
            created_at=self._now(),
            metadata={
                "deterministic_validation_required": True,
                "preserved_by": self.name,
            },
        )

    def _approval_required_result(
        self, request: IntegrationOpsRequest, warning: str
    ) -> IntegrationOpsResult:
        return self._result(
            request=request,
            status="approval_required",
            mode=(
                "write_enabled"
                if request.requested_external_write
                else self._safe_mode(request.mode)
            ),
            sync_job=self._sync_job(request, status="blocked", mode="dry_run"),
            warnings=[warning, "Codex cannot approve external writes."],
            external_write_performed=False,
            metadata={
                "requires_approval": True,
                "requires_governance_permission": self._WRITE_PERMISSION,
                "codex_approved_external_write": False,
            },
        )

    def _sync_job(
        self,
        request: IntegrationOpsRequest,
        *,
        status: Literal["planned", "running", "succeeded", "failed", "dry_run", "blocked"],
        mode: Literal["read_only", "dry_run", "sandbox", "write_enabled"],
        rows_seen: int = 0,
        rows_valid: int = 0,
        rows_rejected: int = 0,
    ) -> SyncJobRecord:
        return SyncJobRecord(
            connector_id=request.external_system_id,
            project_id=request.project_id,
            direction="import",
            mode=mode,
            status=status,
            started_at=self._now() if status in {"running", "succeeded", "failed"} else None,
            completed_at=self._now() if status in {"succeeded", "failed"} else None,
            rows_seen=rows_seen,
            rows_valid=rows_valid,
            rows_rejected=rows_rejected,
            metadata={
                "object_types": list(request.object_types),
                "requested_by": request.requested_by or self.name,
                **self._redact_json(request.metadata),
            },
        )

    def _result(
        self,
        *,
        request: IntegrationOpsRequest,
        status: IntegrationOpsStatus,
        mode: IntegrationOpsMode,
        sync_job: SyncJobRecord,
        health: ConnectorHealth | None = None,
        validation_reports: list[DataContractValidationReport] | None = None,
        mapping: EntityMapping | None = None,
        mapping_review_queue: list[EntityMapping] | None = None,
        export_package: ExportPackageResult | None = None,
        records_seen: int = 0,
        records_valid: int = 0,
        records_imported: int = 0,
        records_failed: int = 0,
        records_skipped: int = 0,
        external_write_performed: bool = False,
        lineage_records: list[WorkflowLineageRecord] | None = None,
        warnings: list[str] | None = None,
        completed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IntegrationOpsResult:
        sanitized_metadata = self._redact_json(metadata or request.metadata)
        sanitized_warnings = [
            redact_secret_values(warning) for warning in (warnings or [])
        ]
        return IntegrationOpsResult(
            status=status,
            mode=mode,
            sync_job=sync_job.model_copy(
                update={"metadata": self._redact_json(sync_job.metadata)}
            ),
            health=health,
            validation_reports=validation_reports or [],
            mapping=mapping,
            mapping_review_queue=mapping_review_queue or [],
            export_package=export_package,
            records_seen=records_seen,
            records_valid=records_valid,
            records_imported=records_imported,
            records_failed=records_failed,
            records_skipped=records_skipped,
            external_write_performed=external_write_performed,
            lineage_records=lineage_records or [],
            audit_events=[
                self._audit_event(
                    request=request,
                    sync_job_id=sync_job.sync_job_id,
                    event_type=f"integration_ops_{status}",
                    summary=f"{self.name} produced {status} integration operation result.",
                    metadata=sanitized_metadata,
                )
            ],
            warnings=sanitized_warnings,
            completed_at=completed_at,
            metadata=sanitized_metadata,
        )

    def _audit_event(
        self,
        *,
        request: IntegrationOpsRequest,
        sync_job_id: str,
        event_type: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> IntegrationAuditEvent:
        return IntegrationAuditEvent(
            event_id=f"integrations-audit-{uuid4().hex[:16]}",
            external_system_id=request.external_system_id,
            sync_job_id=sync_job_id,
            actor_user_id=request.requested_by,
            event_type=event_type,
            timestamp=self._now(),
            object_type="integration_operation",
            object_id=sync_job_id,
            summary=redact_secret_values(summary),
            metadata={
                "requested_by": request.requested_by or self.name,
                **self._redact_json(metadata),
            },
        )

    def _lineage_for_assay_result(
        self,
        request: IntegrationOpsRequest,
        record: dict[str, Any],
    ) -> WorkflowLineageRecord:
        source_record_id = str(record.get("source_record_id") or record.get("result_id"))
        target_artifact_id = f"validated-assay-{source_record_id}"
        return self.preserve_lineage(
            request,
            source_object_id=source_record_id,
            target_object_id=target_artifact_id,
            relation_type="synced_from",
        )

    def _write_authorized(self, request: IntegrationOpsRequest) -> bool:
        return bool(
            request.write_approval_id
            and self._WRITE_PERMISSION in request.governance_permissions
        )

    def _safe_mode(self, mode: IntegrationOpsMode) -> Literal["dry_run", "read_only"]:
        return "read_only" if mode == "read_only" else "dry_run"

    def _external_ref_from_records(
        self,
        external_records: list[dict[str, Any]],
    ) -> ExternalRecordRef:
        first = external_records[0] if external_records else {}
        return ExternalRecordRef(
            external_system_id=str(
                first.get("external_system_id")
                or first.get("source_system")
                or "external-system"
            ),
            external_record_type=str(first.get("record_type") or "external_record"),
            external_record_id=str(
                first.get("external_record_id")
                or first.get("source_record_id")
                or first.get("external_id")
                or first.get("id")
                or "pending-review"
            ),
            retrieved_at=self._now(),
            metadata={"record_count": len(external_records)},
        )

    def _mapping_conflict_type(self, external_records: list[dict[str, Any]]) -> str:
        external_ids = {
            str(
                record.get("external_record_id")
                or record.get("source_record_id")
                or record.get("external_id")
                or record.get("id")
            )
            for record in external_records
        }
        return (
            "one_internal_maps_to_multiple_external"
            if len(external_ids) > 1
            else "codex_suggested_mapping"
        )

    def _internal_entity_id(
        self,
        internal_entity: dict[str, Any],
        internal_entity_type: str,
    ) -> str:
        keys = {
            "assay_result": ["result_id", "assay_result_id", "source_record_id"],
            "review_item": ["review_item_id"],
            "candidate": ["candidate_id", "internal_id"],
            "generated_molecule": ["generated_id", "candidate_id"],
        }.get(internal_entity_type, ["id", "internal_id"])
        for key in keys:
            value = internal_entity.get(key)
            if value:
                return str(value)
        return f"pending-{uuid4().hex[:8]}"

    def _redact_json(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, raw in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in SECRET_KEYS or any(marker in normalized for marker in SECRET_KEYS):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_json(raw)
            return redacted
        if isinstance(value, list):
            return [self._redact_json(item) for item in value]
        if isinstance(value, str):
            return redact_secret_values(value)
        return value

    def _redacted_json_string(self, value: Any) -> str:
        return redact_secret_values(json.dumps(self._redact_json(value), sort_keys=True))


__all__ = [
    "IntegrationOpsAgent",
    "IntegrationOpsMode",
    "IntegrationOpsRequest",
    "IntegrationOpsResult",
    "IntegrationOpsStatus",
]
