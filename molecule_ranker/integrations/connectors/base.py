from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.experimental.importers import validate_assay_results
from molecule_ranker.experimental.schemas import AssayImportResult, AssayResult
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    DataContract,
    DataContractValidationReport,
    ExternalRecordEnvelope,
    ExternalRecordRef,
    IntegrationAuditEvent,
    IntegrationHealthStatus,
    SyncRecord,
    SyncRecordAction,
)
from molecule_ranker.integrations.validation import validate_data_contract

WRITE_METHODS = {
    "attach_report",
    "create_notebook_entry",
    "export_assay_results",
    "export_candidate",
    "export_table",
}
RANKING_SCORE_KEYS = {"ranking_score", "final_score", "score_breakdown", "recalibrated_score"}


class ConnectorError(RuntimeError):
    """Raised when a connector cannot safely perform the requested operation."""


class ConnectorCallRecorder:
    """Small in-memory recorder used by connector tests and non-platform callers."""

    def __init__(self) -> None:
        self.audit_events: list[IntegrationAuditEvent] = []
        self.sync_records: list[SyncRecord] = []

    def write_audit(self, event: IntegrationAuditEvent) -> None:
        self.audit_events.append(event)

    def write_sync_record(self, record: SyncRecord) -> None:
        self.sync_records.append(record)


class ExternalConnector:
    connector_name: str = "external"
    system_type: str = "generic_rest"
    provider: str = "generic"
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "Read-only or dry-run by default.",
        "No lab instruments or devices are controlled.",
        "No lab protocols, synthesis instructions, dosing, or treatment guidance are provided.",
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
    ) -> None:
        self.config = config
        self.recorder = recorder or ConnectorCallRecorder()

    def health_check(self) -> IntegrationHealthStatus:
        def call() -> IntegrationHealthStatus:
            if self.config.credential_ref is None and self.config.provider not in {
                "generic_csv_sftp",
                "sila_metadata",
            }:
                return IntegrationHealthStatus(
                    connector_id=self.config.connector_id,
                    provider=self.config.provider,
                    status="unconfigured",
                    message="Credential reference is not configured.",
                    capabilities=list(self.capabilities),
                    limitations=list(self.limitations),
                )
            return IntegrationHealthStatus(
                connector_id=self.config.connector_id,
                provider=self.config.provider,
                status="ok" if self.config.mode != "write_enabled" else "degraded",
                message=(
                    "Connector configuration is valid; external network calls are not made here."
                ),
                capabilities=list(self.capabilities),
                limitations=list(self.limitations),
            )

        return self._call("health_check", call)

    def preview_import(
        self,
        *,
        rows: list[ExternalRecordEnvelope],
        contract: DataContract,
    ) -> DataContractValidationReport:
        return self._call(
            "preview_import",
            lambda: validate_data_contract(rows, contract),
            result_refs=[record.external_ref for record in rows],
        )

    def export_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call(
            "export_records",
            lambda: {
                "status": "dry_run" if self.config.mode != "write_enabled" else "planned",
                "record_count": len(records),
                "connector_id": self.config.connector_id,
                "provider": self.config.provider,
            },
            write=True,
            payload=records,
        )

    def _call(
        self,
        method_name: str,
        callback: Callable[[], Any],
        *,
        write: bool = False,
        payload: Any = None,
        result_refs: list[ExternalRecordRef] | None = None,
    ) -> Any:
        try:
            if write or method_name in WRITE_METHODS:
                self._require_explicit_write_permission()
            self._reject_ranking_score_mutation(payload)
            result = callback()
        except Exception as exc:
            self._record_audit(method_name, "failed", str(exc))
            self._record_sync(method_name, "failed", refs=result_refs or [])
            raise
        self._reject_ranking_score_mutation(result)
        refs = result_refs or _extract_external_refs(result)
        self._require_source_record_ids(method_name, refs)
        self._record_audit(method_name, "succeeded", f"Connector call {method_name} succeeded.")
        self._record_sync(method_name, "succeeded", refs=refs)
        return result

    def _require_explicit_write_permission(self) -> None:
        if (
            self.config.mode != "write_enabled"
            or not self.config.allow_writes
            or not self.config.explicit_write_permission
        ):
            raise ConnectorError("External writes/exports are blocked by default.")

    def _require_source_record_ids(self, method_name: str, refs: list[ExternalRecordRef]) -> None:
        if method_name in {"health_check", "connect", "validate_connection", "verify_signature"}:
            return
        if refs and all(ref.external_record_id.strip() for ref in refs):
            return
        raise ConnectorError(f"{method_name} did not preserve source record IDs.")

    def _record_audit(self, method_name: str, status: str, summary: str) -> None:
        self.recorder.write_audit(
            IntegrationAuditEvent(
                event_id=f"int-audit-{uuid4().hex[:16]}",
                external_system_id=self.config.connector_id,
                sync_job_id=None,
                actor_user_id=None,
                event_type=f"connector.{method_name}.{status}",
                timestamp=datetime.now(UTC),
                object_type="connector_call",
                object_id=method_name,
                summary=summary,
                metadata={
                    "connector_name": self.connector_name,
                    "system_type": self.system_type,
                    "mode": self.config.mode,
                },
            )
        )

    def _record_sync(
        self,
        method_name: str,
        status: str,
        *,
        refs: list[ExternalRecordRef],
    ) -> None:
        sync_status = "succeeded" if status == "succeeded" else "failed"
        if not refs:
            refs = [
                ExternalRecordRef(
                    external_system_id=self.config.connector_id,
                    external_record_type="connector_call",
                    external_record_id=method_name,
                    retrieved_at=datetime.now(UTC),
                    metadata={"connector_name": self.connector_name},
                )
            ]
        for ref in refs:
            self.recorder.write_sync_record(
                SyncRecord(
                    sync_record_id=f"sync-record-{uuid4().hex[:16]}",
                    sync_job_id=f"connector-call-{self.config.connector_id}",
                    external_ref=ref,
                    internal_entity_type=None,
                    internal_entity_id=None,
                    action=_sync_action_for_method(method_name, sync_status),
                    status=sync_status,  # type: ignore[arg-type]
                    validation_errors=[],
                    warnings=[],
                    raw_payload_artifact_id=None,
                    created_at=datetime.now(UTC),
                    metadata={"method": method_name},
                )
            )

    def _reject_ranking_score_mutation(self, payload: Any) -> None:
        if _contains_ranking_score_keys(payload):
            raise ConnectorError("Connector payloads must not directly alter ranking scores.")


class RegistryConnector(ExternalConnector):
    system_type = "compound_registry"

    def search_molecule(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("search_molecule", lambda: self._search_molecule(*args, **kwargs))

    def get_molecule(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_molecule", lambda: self._get_molecule(*args, **kwargs))

    def map_candidate(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("map_candidate", lambda: self._map_candidate(*args, **kwargs))

    def export_candidate(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(
            "export_candidate",
            lambda: self._export_candidate(*args, **kwargs),
            write=True,
            payload={"args": args, "kwargs": kwargs},
        )

    def _search_molecule(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _get_molecule(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _map_candidate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _export_candidate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class AssayConnector(ExternalConnector):
    system_type = "assay_provider"

    def list_assay_runs(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("list_assay_runs", lambda: self._list_assay_runs(*args, **kwargs))

    def get_assay_results(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_assay_results", lambda: self._get_assay_results(*args, **kwargs))

    def import_assay_results(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> list[AssayResult] | AssayImportResult:
        def call() -> list[AssayResult] | AssayImportResult:
            imported = self._import_assay_results(*args, **kwargs)
            _validate_imported_assay_results(imported)
            return imported

        return self._call("import_assay_results", call)

    def export_assay_results(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(
            "export_assay_results",
            lambda: self._export_assay_results(*args, **kwargs),
            write=True,
            payload={"args": args, "kwargs": kwargs},
        )

    def _list_assay_runs(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _get_assay_results(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _import_assay_results(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> list[AssayResult] | AssayImportResult:
        raise NotImplementedError

    def _export_assay_results(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class ELNConnector(ExternalConnector):
    system_type = "eln"

    def create_notebook_entry(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(
            "create_notebook_entry",
            lambda: self._create_notebook_entry(*args, **kwargs),
            write=True,
            payload={"args": args, "kwargs": kwargs},
        )

    def attach_report(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(
            "attach_report",
            lambda: self._attach_report(*args, **kwargs),
            write=True,
            payload={"args": args, "kwargs": kwargs},
        )

    def list_entries(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("list_entries", lambda: self._list_entries(*args, **kwargs))

    def get_entry(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_entry", lambda: self._get_entry(*args, **kwargs))

    def _create_notebook_entry(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _attach_report(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _list_entries(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _get_entry(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class WarehouseConnector(ExternalConnector):
    system_type = "data_warehouse"

    def connect(self) -> Any:
        return self._call("connect", self._connect)

    def validate_connection(self) -> Any:
        return self._call("validate_connection", self._validate_connection)

    def export_table(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(
            "export_table",
            lambda: self._export_table(*args, **kwargs),
            write=True,
            payload={"args": args, "kwargs": kwargs},
        )

    def import_query(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("import_query", lambda: self._import_query(*args, **kwargs))

    def run_query_readonly(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("run_query_readonly", lambda: self._run_query_readonly(*args, **kwargs))

    def _connect(self) -> Any:
        raise NotImplementedError

    def _validate_connection(self) -> Any:
        raise NotImplementedError

    def _export_table(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _import_query(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _run_query_readonly(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class WebhookConnector(ExternalConnector):
    system_type = "generic_rest"

    def verify_signature(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("verify_signature", lambda: self._verify_signature(*args, **kwargs))

    def parse_event(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("parse_event", lambda: self._parse_event(*args, **kwargs))

    def enqueue_sync_job(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("enqueue_sync_job", lambda: self._enqueue_sync_job(*args, **kwargs))

    def _verify_signature(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _parse_event(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _enqueue_sync_job(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


BaseConnector = ExternalConnector


def _validate_imported_assay_results(imported: list[AssayResult] | AssayImportResult) -> None:
    if isinstance(imported, AssayImportResult):
        report = imported.validation_report
    else:
        report = validate_assay_results(imported)
    if report.invalid_count or report.incomplete_count:
        raise ConnectorError("Imported assay results failed V0.6 validation.")


def _extract_external_refs(value: Any) -> list[ExternalRecordRef]:
    refs: list[ExternalRecordRef] = []
    _collect_external_refs(value, refs)
    return refs


def _collect_external_refs(value: Any, refs: list[ExternalRecordRef]) -> None:
    if isinstance(value, ExternalRecordRef):
        refs.append(value)
        return
    if isinstance(value, ExternalRecordEnvelope):
        refs.append(value.external_ref)
        return
    if isinstance(value, SyncRecord):
        refs.append(value.external_ref)
        return
    if isinstance(value, AssayImportResult):
        for result in value.results:
            _collect_external_refs(result, refs)
        return
    if isinstance(value, AssayResult):
        ref = _assay_result_external_ref(value)
        if ref is not None:
            refs.append(ref)
        return
    if isinstance(value, dict):
        ref = _dict_external_ref(value)
        if ref is not None:
            refs.append(ref)
            return
        for raw in value.values():
            _collect_external_refs(raw, refs)
        return
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        for item in value:
            _collect_external_refs(item, refs)


def _dict_external_ref(value: dict[str, Any]) -> ExternalRecordRef | None:
    raw_ref = value.get("external_ref")
    if isinstance(raw_ref, ExternalRecordRef):
        return raw_ref
    if isinstance(raw_ref, dict):
        return ExternalRecordRef.model_validate(raw_ref)
    source_system = value.get("external_system_id") or value.get("source_system")
    source_id = (
        value.get("external_record_id")
        or value.get("source_record_id")
        or value.get("registry_id")
        or value.get("assay_result_id")
        or value.get("id")
    )
    record_type = value.get("external_record_type") or value.get("record_type") or "record"
    if source_system and source_id:
        return ExternalRecordRef(
            external_system_id=str(source_system),
            external_record_type=str(record_type),
            external_record_id=str(source_id),
            retrieved_at=datetime.now(UTC),
            metadata={},
        )
    return None


def _assay_result_external_ref(result: AssayResult) -> ExternalRecordRef | None:
    provenance = result.provenance or {}
    source_system = provenance.get("source_system") or provenance.get("source_type")
    source_id = (
        provenance.get("source_record_id")
        or provenance.get("external_record_id")
        or result.result_id
    )
    if not source_system or not source_id:
        return None
    return ExternalRecordRef(
        external_system_id=str(source_system),
        external_record_type="assay_result",
        external_record_id=str(source_id),
        retrieved_at=result.imported_at,
        metadata={"result_id": result.result_id},
    )


def _contains_ranking_score_keys(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in RANKING_SCORE_KEYS for key in value):
            return True
        return any(_contains_ranking_score_keys(raw) for raw in value.values())
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        return any(_contains_ranking_score_keys(item) for item in value)
    return False


def _sync_action_for_method(method_name: str, status: str) -> SyncRecordAction:
    if status == "failed":
        return "failed"
    if method_name.startswith("export") or method_name in {
        "create_notebook_entry",
        "attach_report",
    }:
        return "exported"
    if method_name.startswith("map"):
        return "mapped"
    if method_name == "enqueue_sync_job":
        return "updated"
    return "imported"
