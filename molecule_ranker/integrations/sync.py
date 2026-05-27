from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.experimental.schemas import AssayImportResult, AssayResult
from molecule_ranker.integrations.connectors.base import ConnectorError, ExternalConnector
from molecule_ranker.integrations.mapping import map_candidate_to_registry_entry
from molecule_ranker.integrations.schemas import (
    DataContract,
    EntityMapping,
    ExternalRecordEnvelope,
    ExternalRecordRef,
    SyncDirection,
    SyncJob,
    SyncJobRecord,
    SyncMode,
    SyncRecord,
)
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.validation import validate_record_against_contract

SyncObjectType = Literal[
    "candidates",
    "generated_molecules",
    "assay_results",
    "assay_runs",
    "review_dossiers",
    "validation_handoffs",
    "active_learning_batches",
    "project_summaries",
]

IMPORT_OBJECT_TYPES: set[str] = {
    "candidates",
    "generated_molecules",
    "assay_results",
    "assay_runs",
    "review_dossiers",
    "validation_handoffs",
    "active_learning_batches",
    "project_summaries",
}


class SyncRequest(BaseModel):
    direction: SyncDirection = "import"
    object_types: list[SyncObjectType] = Field(default_factory=lambda: ["assay_results"])
    mode: SyncMode = "dry_run"
    project_id: str | None = None
    org_id: str = "default"
    requested_by_user_id: str | None = None
    data_contracts: dict[str, DataContract] = Field(default_factory=dict)
    export_records: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncEngine:
    def __init__(self, store: IntegrationStore) -> None:
        self.store = store

    def run(self, connector: ExternalConnector, request: SyncRequest) -> SyncJob:
        if request.mode == "write_enabled" and connector.config.mode != "write_enabled":
            raise ConnectorError("External writes require a write_enabled connector.")
        job = self.store.create_sync_job(
            SyncJob(
                sync_job_id=f"sync-{uuid.uuid4().hex[:16]}",
                external_system_id=connector.config.connector_id,
                project_id=request.project_id,
                direction=request.direction,
                object_types=list(request.object_types),
                mode=request.mode,
                status="queued",
                requested_by_user_id=request.requested_by_user_id,
                metadata=request.metadata,
            ),
            org_id=request.org_id,
        )
        job = self.store.update_sync_job(
            job.sync_job_id,
            status="running",
            started_at=datetime.now(UTC),
        )
        counters = _Counters()
        warnings: list[str] = []
        try:
            if request.direction in {"import", "bidirectional"}:
                self._run_import(connector, request, job, counters, warnings)
            if request.direction in {"export", "bidirectional"}:
                self._run_export(connector, request, job, counters, warnings)
        except Exception as exc:
            status = "failed" if counters.records_seen == 0 else "partial"
            return self.store.update_sync_job(
                job.sync_job_id,
                status=status,
                completed_at=datetime.now(UTC),
                records_seen=counters.records_seen,
                records_imported=counters.records_imported,
                records_exported=counters.records_exported,
                records_skipped=counters.records_skipped,
                records_failed=counters.records_failed,
                warnings=warnings,
                error_summary=str(exc),
            )

        status = "succeeded"
        if counters.records_failed:
            status = "partial"
        return self.store.update_sync_job(
            job.sync_job_id,
            status=status,
            completed_at=datetime.now(UTC),
            records_seen=counters.records_seen,
            records_imported=counters.records_imported,
            records_exported=counters.records_exported,
            records_skipped=counters.records_skipped,
            records_failed=counters.records_failed,
            warnings=warnings,
        )

    def _run_import(
        self,
        connector: ExternalConnector,
        request: SyncRequest,
        job: SyncJob,
        counters: _Counters,
        warnings: list[str],
    ) -> None:
        existing_hashes = self._existing_hashes(connector.config.connector_id)
        for object_type in request.object_types:
            if object_type not in IMPORT_OBJECT_TYPES:
                warnings.append(f"Unsupported import object type skipped: {object_type}")
                continue
            records = self._connector_import(connector, object_type)
            for item in records:
                external_ref, payload = _record_parts(
                    item,
                    connector_id=connector.config.connector_id,
                    object_type=_external_record_type(object_type),
                )
                counters.records_seen += 1
                payload_hash = _payload_hash(payload)
                validation_errors = self._validation_errors(
                    payload,
                    request.data_contracts.get(object_type),
                )
                if (external_ref.external_record_id, payload_hash) in existing_hashes:
                    self._add_record(
                        job,
                        external_ref=external_ref,
                        action="skipped",
                        status="skipped",
                        payload=payload,
                        metadata={
                            "idempotency": "duplicate_payload_hash",
                            "payload_sha256": payload_hash,
                        },
                    )
                    counters.records_skipped += 1
                    continue
                if validation_errors:
                    self._add_record(
                        job,
                        external_ref=external_ref,
                        action="failed",
                        status="failed",
                        payload=payload,
                        validation_errors=validation_errors,
                        metadata={"payload_sha256": payload_hash},
                    )
                    counters.records_failed += 1
                    continue
                mapping = self._maybe_map_record(object_type, payload, external_ref)
                self._add_record(
                    job,
                    external_ref=external_ref,
                    action="imported" if mapping is None else "mapped",
                    status="succeeded"
                    if mapping is None or mapping.status == "active"
                    else "pending_review",
                    payload=payload,
                    internal_entity_type=mapping.internal_entity_type
                    if mapping
                    else _internal_type(object_type),
                    internal_entity_id=mapping.internal_entity_id
                    if mapping
                    else _internal_id(payload, object_type),
                    metadata={
                        "payload_sha256": payload_hash,
                        "mapping_id": mapping.mapping_id if mapping else None,
                        "mapping_status": mapping.status if mapping else None,
                    },
                )
                existing_hashes.add((external_ref.external_record_id, payload_hash))
                counters.records_imported += 1

    def _run_export(
        self,
        connector: ExternalConnector,
        request: SyncRequest,
        job: SyncJob,
        counters: _Counters,
        warnings: list[str],
    ) -> None:
        for object_type in request.object_types:
            records = request.export_records.get(object_type, [])
            for index, payload in enumerate(records, start=1):
                counters.records_seen += 1
                external_ref = ExternalRecordRef(
                    external_system_id=connector.config.connector_id,
                    external_record_type=f"{_external_record_type(object_type)}_export",
                    external_record_id=str(
                        payload.get("source_record_id")
                        or payload.get("id")
                        or f"dry-run-export-{object_type}-{index}"
                    ),
                    retrieved_at=datetime.now(UTC),
                    metadata={"dry_run": request.mode != "write_enabled"},
                )
                if request.mode != "write_enabled":
                    self._add_record(
                        job,
                        external_ref=external_ref,
                        action="exported",
                        status="skipped",
                        payload=payload,
                        warnings=["dry-run export artifact only; no external write performed"],
                        metadata={"payload_sha256": _payload_hash(payload), "dry_run": True},
                    )
                    counters.records_skipped += 1
                    continue
                result = self._connector_export(connector, object_type, payload)
                result_ref, result_payload = _record_parts(
                    result,
                    connector_id=connector.config.connector_id,
                    object_type=external_ref.external_record_type,
                )
                self._add_record(
                    job,
                    external_ref=result_ref,
                    action="exported",
                    status="succeeded",
                    payload=result_payload,
                    metadata={"payload_sha256": _payload_hash(result_payload)},
                )
                counters.records_exported += 1

    def _connector_import(self, connector: ExternalConnector, object_type: str) -> list[Any]:
        if object_type == "assay_results":
            imported = _call_if_available(connector, "import_assay_results")
            if imported is not None:
                return _as_records(imported)
            assay_results = _call_if_available(connector, "get_assay_results")
            if assay_results is not None:
                return _as_records(assay_results)
        if object_type == "assay_runs":
            assay_runs = _call_if_available(connector, "list_assay_runs")
            if assay_runs is not None:
                return _as_records(assay_runs)
        if object_type == "review_dossiers":
            entries = _call_if_available(connector, "list_entries")
            if entries is not None:
                return _as_records(entries)
        records = _call_if_available(connector, "list_records", object_type=object_type)
        if records is not None:
            return _as_records(records)
        if object_type == "candidates":
            molecules = _call_if_available(connector, "search_molecule", "")
            if molecules is not None:
                return _as_records(molecules)
        raise ConnectorError(f"Connector does not support import for {object_type}.")

    def _connector_export(
        self,
        connector: ExternalConnector,
        object_type: str,
        payload: dict[str, Any],
    ) -> Any:
        if object_type in {"candidates", "generated_molecules"} and hasattr(
            connector,
            "export_candidate",
        ):
            exported = _call_if_available(connector, "export_candidate", payload)
            if exported is not None:
                return exported
        if object_type == "assay_results":
            exported_assays = _call_if_available(connector, "export_assay_results", [payload])
            if exported_assays is not None:
                return exported_assays
        if object_type == "review_dossiers":
            entry = _call_if_available(connector, "create_notebook_entry", payload)
            if entry is not None:
                return entry
        exported_records = _call_if_available(connector, "export_records", [payload])
        if exported_records is not None:
            return exported_records
        raise ConnectorError(f"Connector does not support export for {object_type}.")

    def _validation_errors(
        self,
        payload: dict[str, Any],
        contract: DataContract | None,
    ) -> list[str]:
        if contract is None:
            return []
        return validate_record_against_contract(payload, contract)

    def _maybe_map_record(
        self,
        object_type: str,
        payload: dict[str, Any],
        external_ref: ExternalRecordRef,
    ) -> EntityMapping | None:
        if object_type != "candidates":
            return None
        if not _internal_id(payload, object_type):
            return None
        return map_candidate_to_registry_entry(
            payload,
            [{**payload, "external_ref": external_ref}],
            project_id=self.store.project_id,
        )

    def _add_record(
        self,
        job: SyncJob,
        *,
        external_ref: ExternalRecordRef,
        action: str,
        status: str,
        payload: dict[str, Any],
        internal_entity_type: str | None = None,
        internal_entity_id: str | None = None,
        validation_errors: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SyncRecord:
        clean_metadata = {
            key: value for key, value in (metadata or {}).items() if value is not None
        }
        return self.store.add_sync_record(
            SyncRecord(
                sync_record_id=f"sync-record-{uuid.uuid4().hex[:16]}",
                sync_job_id=job.sync_job_id,
                external_ref=external_ref,
                internal_entity_type=internal_entity_type,
                internal_entity_id=internal_entity_id,
                action=action,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                validation_errors=validation_errors or [],
                warnings=warnings or [],
                raw_payload_artifact_id=None,
                created_at=datetime.now(UTC),
                metadata=clean_metadata,
            ),
            raw_payload=payload,
        )

    def _existing_hashes(self, external_system_id: str) -> set[tuple[str, str]]:
        hashes: set[tuple[str, str]] = set()
        for record in self.store.list_sync_records(
            external_system_id=external_system_id,
            limit=100_000,
        ):
            payload_hash = record.metadata.get("payload_sha256")
            if payload_hash:
                hashes.add((record.external_ref.external_record_id, str(payload_hash)))
        return hashes


@dataclass
class _Counters:
    records_seen: int = 0
    records_imported: int = 0
    records_exported: int = 0
    records_skipped: int = 0
    records_failed: int = 0


def run_sync(
    *,
    store: IntegrationStore,
    connector: ExternalConnector,
    request: SyncRequest,
) -> SyncJob:
    return SyncEngine(store).run(connector, request)


def _as_records(value: Any) -> list[Any]:
    if isinstance(value, AssayImportResult):
        return list(value.results)
    if isinstance(value, list):
        return value
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | dict):
        return list(value)
    return [value]


def _call_if_available(
    connector: ExternalConnector,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    method = getattr(connector, method_name, None)
    if not callable(method):
        return None
    return method(*args, **kwargs)


def _record_parts(
    value: Any,
    *,
    connector_id: str,
    object_type: str,
) -> tuple[ExternalRecordRef, dict[str, Any]]:
    if isinstance(value, SyncRecord):
        return value.external_ref, dict(value.metadata)
    if isinstance(value, ExternalRecordEnvelope):
        return value.external_ref, dict(value.payload)
    if isinstance(value, AssayResult):
        payload = value.model_dump(mode="json")
        return _assay_ref(value, connector_id), payload
    if isinstance(value, dict):
        raw_ref = value.get("external_ref")
        payload = dict(value.get("payload") or value.get("raw") or value)
        payload.pop("external_ref", None)
        if isinstance(raw_ref, ExternalRecordRef):
            ref = raw_ref
        elif isinstance(raw_ref, dict):
            ref = ExternalRecordRef.model_validate(raw_ref)
        else:
            ref = ExternalRecordRef(
                external_system_id=str(value.get("external_system_id") or connector_id),
                external_record_type=str(value.get("external_record_type") or object_type),
                external_record_id=str(
                    value.get("external_record_id")
                    or value.get("source_record_id")
                    or value.get("registry_id")
                    or value.get("result_id")
                    or value.get("id")
                    or _payload_hash(payload)[:16]
                ),
                retrieved_at=datetime.now(UTC),
                metadata={},
            )
        payload.setdefault("source_record_id", ref.external_record_id)
        return ref, payload
    payload = {"value": str(value)}
    return (
        ExternalRecordRef(
            external_system_id=connector_id,
            external_record_type=object_type,
            external_record_id=_payload_hash(payload)[:16],
            retrieved_at=datetime.now(UTC),
            metadata={},
        ),
        payload,
    )


def _assay_ref(result: AssayResult, connector_id: str) -> ExternalRecordRef:
    provenance = result.provenance or {}
    source_id = (
        provenance.get("source_record_id")
        or provenance.get("external_record_id")
        or result.result_id
    )
    return ExternalRecordRef(
        external_system_id=str(provenance.get("source_system") or connector_id),
        external_record_type="assay_result",
        external_record_id=str(source_id),
        retrieved_at=result.imported_at,
        metadata={"result_id": result.result_id},
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _external_record_type(object_type: str) -> str:
    return {
        "candidates": "registry_entry",
        "generated_molecules": "proposed_compound",
        "assay_results": "assay_result",
        "assay_runs": "assay_run",
        "review_dossiers": "notebook_entry",
        "validation_handoffs": "validation_handoff",
        "active_learning_batches": "active_learning_batch",
        "project_summaries": "project_summary",
    }.get(object_type, "record")


def _internal_type(object_type: str) -> str | None:
    return {
        "candidates": "candidate",
        "generated_molecules": "generated_molecule",
        "assay_results": "assay_result",
        "review_dossiers": "review_item",
    }.get(object_type)


def _internal_id(payload: dict[str, Any], object_type: str) -> str | None:
    for key in [
        "candidate_id",
        "generated_id",
        "generated_molecule_id",
        "result_id",
        "review_item_id",
        "internal_entity_id",
    ]:
        value = payload.get(key)
        if value:
            return str(value)
    return None


__all__ = [
    "SyncEngine",
    "SyncJob",
    "SyncJobRecord",
    "SyncRecord",
    "SyncRequest",
    "run_sync",
]
