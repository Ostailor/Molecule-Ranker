from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.e2e.schemas import (
    EndToEndResultBundle,
    LineageRelationType,
    WorkflowLineageRecord,
)


class ExternalLineageTracker:
    """Tracks lineage across internal workflow artifacts and external systems."""

    def __init__(
        self,
        *,
        workflow_id: str,
        records: list[WorkflowLineageRecord] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.records = list(records or [])
        self._now = now or (lambda: datetime.now(UTC))

    def record_import(
        self,
        *,
        internal_object_type: str,
        internal_object_id: str,
        external_system_id: str,
        external_record_id: str,
        artifact_ids: list[str] | None = None,
        sync_job_id: str | None = None,
        payload_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        if internal_object_type == "assay_result" and not external_record_id.strip():
            raise ValueError("Imported assay results must link to a source external record.")
        active_artifact_ids = list(artifact_ids or [])
        if payload_artifact_id:
            active_artifact_ids.append(payload_artifact_id)
        return self.record(
            source_object_type="external_record",
            source_object_id=external_record_id,
            target_object_type=internal_object_type,
            target_object_id=internal_object_id,
            relation_type="imported_from",
            artifact_ids=active_artifact_ids,
            external_record_refs=[
                self._external_ref(
                    external_system_id=external_system_id,
                    external_record_id=external_record_id,
                    sync_job_id=sync_job_id,
                )
            ],
            metadata={
                "external_import": True,
                "sync_job_id": sync_job_id,
                "payload_artifact_id": payload_artifact_id,
                **(metadata or {}),
            },
        )

    def record_export(
        self,
        *,
        source_object_type: str,
        source_object_id: str,
        external_system_id: str,
        external_record_id: str,
        artifact_id: str,
        approval_id: str | None = None,
        sync_job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        if not approval_id:
            raise ValueError("External write artifacts require an approval ID.")
        return self.record(
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            target_object_type="external_record",
            target_object_id=external_record_id,
            relation_type="exported_to",
            artifact_ids=[artifact_id],
            external_record_refs=[
                self._external_ref(
                    external_system_id=external_system_id,
                    external_record_id=external_record_id,
                    sync_job_id=sync_job_id,
                )
            ],
            metadata={
                "external_export": True,
                "external_write_artifact": True,
                "approval_id": approval_id,
                "sync_job_id": sync_job_id,
                **(metadata or {}),
            },
        )

    def record_pending_mapping(
        self,
        *,
        internal_object_type: str,
        internal_object_id: str,
        external_system_id: str,
        external_record_id: str,
        artifact_ids: list[str] | None = None,
        codex_suggested: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        return self.record(
            source_object_type=internal_object_type,
            source_object_id=internal_object_id,
            target_object_type="external_record",
            target_object_id=external_record_id,
            relation_type="pending_mapping",
            artifact_ids=artifact_ids or [],
            external_record_refs=[
                self._external_ref(
                    external_system_id=external_system_id,
                    external_record_id=external_record_id,
                )
            ],
            metadata={
                "mapping_status": "pending_review",
                "codex_suggested": codex_suggested,
                "codex_can_approve_mapping": False,
                **(metadata or {}),
            },
        )

    def record_approved_mapping(
        self,
        *,
        internal_object_type: str,
        internal_object_id: str,
        external_system_id: str,
        external_record_id: str,
        approval_id: str,
        artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        if not approval_id.strip():
            raise ValueError("Approved mappings require an approval ID.")
        return self.record(
            source_object_type=internal_object_type,
            source_object_id=internal_object_id,
            target_object_type="external_record",
            target_object_id=external_record_id,
            relation_type="approved_mapping",
            artifact_ids=artifact_ids or [],
            external_record_refs=[
                self._external_ref(
                    external_system_id=external_system_id,
                    external_record_id=external_record_id,
                )
            ],
            metadata={
                "mapping_status": "approved",
                "approval_id": approval_id,
                **(metadata or {}),
            },
        )

    def record_validation(
        self,
        *,
        source_object_type: str,
        source_object_id: str,
        validator_object_type: str,
        validator_object_id: str,
        artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        return self.record(
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            target_object_type=validator_object_type,
            target_object_id=validator_object_id,
            relation_type="validated_by",
            artifact_ids=artifact_ids or [],
            metadata=metadata or {},
        )

    def record_policy_block(
        self,
        *,
        source_object_type: str,
        source_object_id: str,
        policy_id: str,
        artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        return self.record(
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            target_object_type="governance_policy",
            target_object_id=policy_id,
            relation_type="blocked_by_policy",
            artifact_ids=artifact_ids or [],
            metadata=metadata or {},
        )

    def record(
        self,
        *,
        source_object_type: str,
        source_object_id: str,
        target_object_type: str,
        target_object_id: str,
        relation_type: LineageRelationType,
        artifact_ids: list[str] | None = None,
        external_record_refs: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowLineageRecord:
        record = WorkflowLineageRecord(
            lineage_id=f"lineage-{uuid4().hex[:16]}",
            workflow_id=self.workflow_id,
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            target_object_type=target_object_type,
            target_object_id=target_object_id,
            relation_type=relation_type,
            artifact_ids=list(artifact_ids or []),
            external_record_refs=list(external_record_refs or []),
            created_at=self._now(),
            metadata={
                "tracked_by": "ExternalLineageTracker",
                **(metadata or {}),
            },
        )
        self.records.append(record)
        return record

    def include_in_bundle(
        self, bundle: EndToEndResultBundle
    ) -> EndToEndResultBundle:
        lineage_payload = [record.model_dump(mode="json") for record in self.records]
        return bundle.model_copy(
            update={
                "integration_summary": {
                    **bundle.integration_summary,
                    "lineage_record_count": len(self.records),
                    "lineage_included": True,
                },
                "metadata": {
                    **bundle.metadata,
                    "lineage_records": lineage_payload,
                },
            }
        )

    def records_for_artifact(self, artifact_id: str) -> list[WorkflowLineageRecord]:
        return [record for record in self.records if artifact_id in record.artifact_ids]

    def _external_ref(
        self,
        *,
        external_system_id: str,
        external_record_id: str,
        sync_job_id: str | None = None,
    ) -> dict[str, Any]:
        if not external_system_id.strip():
            raise ValueError("External lineage requires an external system ID.")
        if not external_record_id.strip():
            raise ValueError("External lineage requires an external record ID.")
        ref: dict[str, Any] = {
            "external_system_id": external_system_id,
            "external_record_id": external_record_id,
        }
        if sync_job_id:
            ref["sync_job_id"] = sync_job_id
        return ref


__all__ = [
    "ExternalLineageTracker",
    "LineageRelationType",
    "WorkflowLineageRecord",
]
