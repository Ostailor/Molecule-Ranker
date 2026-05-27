from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.experimental.schemas import AssayResult
from molecule_ranker.integrations.connectors.base import (
    AssayConnector,
    ConnectorCallRecorder,
    ConnectorError,
    ELNConnector,
    ExternalConnector,
    RegistryConnector,
    WarehouseConnector,
    WebhookConnector,
)
from molecule_ranker.integrations.schemas import ConnectorConfig, ExternalRecordRef


def test_external_connector_health_records_audit_and_sync() -> None:
    recorder = ConnectorCallRecorder()
    connector = ExternalConnector(_config(), recorder=recorder)

    health = connector.health_check()

    assert health.status == "unconfigured"
    assert recorder.audit_events[-1].event_type == "connector.health_check.succeeded"
    assert recorder.sync_records[-1].external_ref.external_record_id == "health_check"


def test_registry_read_preserves_source_ids_and_records_call() -> None:
    recorder = ConnectorCallRecorder()
    connector = FakeRegistryConnector(_config(), recorder=recorder)

    results = connector.search_molecule("rasagiline")

    assert results[0]["external_ref"].external_record_id == "mol-1"
    assert recorder.audit_events[-1].event_type == "connector.search_molecule.succeeded"
    assert recorder.sync_records[-1].external_ref.external_record_id == "mol-1"


def test_write_methods_require_explicit_write_enabled_mode_and_are_audited() -> None:
    recorder = ConnectorCallRecorder()
    connector = FakeRegistryConnector(_config(), recorder=recorder)

    with pytest.raises(ConnectorError, match="blocked by default"):
        connector.export_candidate({"candidate_id": "candidate-1"})

    assert recorder.audit_events[-1].event_type == "connector.export_candidate.failed"
    assert recorder.sync_records[-1].status == "failed"

    writable = FakeRegistryConnector(
        _config(mode="write_enabled", allow_writes=True, explicit_write_permission=True),
        recorder=ConnectorCallRecorder(),
    )
    exported = writable.export_candidate({"candidate_id": "candidate-1"})

    assert exported["external_ref"].external_record_id == "export-1"


def test_connector_blocks_direct_ranking_score_mutation() -> None:
    connector = FakeRegistryConnector(
        _config(mode="write_enabled", allow_writes=True, explicit_write_permission=True)
    )

    with pytest.raises(ConnectorError, match="ranking scores"):
        connector.export_candidate({"candidate_id": "candidate-1", "final_score": 0.99})


def test_missing_source_record_ids_fail_even_when_call_returns_data() -> None:
    connector = FakeRegistryWithoutSourceId(_config())

    with pytest.raises(ConnectorError, match="source record IDs"):
        connector.search_molecule("rasagiline")


def test_assay_import_requires_v06_validation_before_evidence() -> None:
    connector = FakeAssayConnector(_config(provider="generic_rest", kind="assay_result_provider"))

    imported = connector.import_assay_results(valid=True)

    assert isinstance(imported, list)
    assert imported[0].validation_status == "valid"

    with pytest.raises(ConnectorError, match="V0.6 validation"):
        connector.import_assay_results(valid=False)


def test_eln_warehouse_and_webhook_interfaces_record_calls() -> None:
    recorder = ConnectorCallRecorder()
    eln = FakeELNConnector(_config(provider="benchling", kind="eln_lims"), recorder=recorder)
    warehouse = FakeWarehouseConnector(
        _config(provider="postgresql", kind="data_warehouse"),
        recorder=recorder,
    )
    webhook = FakeWebhookConnector(
        _config(provider="generic_rest", kind="webhook"),
        recorder=recorder,
    )

    assert eln.list_entries()[0]["external_ref"].external_record_id == "entry-1"
    assert warehouse.run_query_readonly("select 1")[0]["external_ref"].external_record_id == "row-1"
    assert webhook.verify_signature(payload=b"{}", signature="sig") is True
    assert webhook.parse_event({"id": "event-1"})["external_ref"].external_record_id == "event-1"
    assert len(recorder.audit_events) == 4
    assert len(recorder.sync_records) == 4


class FakeRegistryConnector(RegistryConnector):
    connector_name = "fake-registry"

    def _search_molecule(self, _query: str) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("molecule", "mol-1"), "name": "Rasagiline"}]

    def _get_molecule(self, molecule_id: str) -> dict[str, Any]:
        return {"external_ref": _ref("molecule", molecule_id), "name": "Rasagiline"}

    def _map_candidate(self, candidate_id: str) -> dict[str, Any]:
        return {
            "external_ref": _ref("molecule", "mol-1"),
            "candidate_id": candidate_id,
        }

    def _export_candidate(self, _candidate: dict[str, Any]) -> dict[str, Any]:
        return {"external_ref": _ref("registry_entry", "export-1"), "status": "planned"}


class FakeRegistryWithoutSourceId(FakeRegistryConnector):
    def _search_molecule(self, _query: str) -> list[dict[str, Any]]:
        return [{"name": "Rasagiline"}]


class FakeAssayConnector(AssayConnector):
    connector_name = "fake-assay"

    def _list_assay_runs(self) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("assay_run", "run-1")}]

    def _get_assay_results(self) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("assay_result", "result-1")}]

    def _import_assay_results(self, *, valid: bool) -> list[AssayResult]:
        return [
            AssayResult(
                experiment_id="exp-1" if valid else None,
                assay_name="Binding",
                molecule_name="Rasagiline",
                outcome="positive" if valid else None,
                provenance={
                    "source_type": "connected_system",
                    "source_system": "fake-assay",
                    "source_record_id": "result-1",
                },
                imported_at=datetime.now(UTC),
            )
        ]

    def _export_assay_results(self, _results: list[AssayResult]) -> dict[str, Any]:
        return {"external_ref": _ref("assay_result", "result-export-1")}


class FakeELNConnector(ELNConnector):
    connector_name = "fake-eln"

    def _create_notebook_entry(self) -> dict[str, Any]:
        return {"external_ref": _ref("notebook_entry", "entry-new")}

    def _attach_report(self) -> dict[str, Any]:
        return {"external_ref": _ref("notebook_entry", "entry-1")}

    def _list_entries(self) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("notebook_entry", "entry-1")}]

    def _get_entry(self, entry_id: str) -> dict[str, Any]:
        return {"external_ref": _ref("notebook_entry", entry_id)}


class FakeWarehouseConnector(WarehouseConnector):
    connector_name = "fake-warehouse"

    def _connect(self) -> object:
        return object()

    def _validate_connection(self) -> bool:
        return True

    def _export_table(self) -> dict[str, Any]:
        return {"external_ref": _ref("warehouse_row", "row-export-1")}

    def _import_query(self, _query: str) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("warehouse_row", "row-1")}]

    def _run_query_readonly(self, _query: str) -> list[dict[str, Any]]:
        return [{"external_ref": _ref("warehouse_row", "row-1")}]


class FakeWebhookConnector(WebhookConnector):
    connector_name = "fake-webhook"

    def _verify_signature(self, *, payload: bytes, signature: str) -> bool:
        return bool(payload and signature)

    def _parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"external_ref": _ref("webhook_event", str(payload["id"])), "payload": payload}

    def _enqueue_sync_job(self, event: dict[str, Any]) -> dict[str, Any]:
        return {"external_ref": _ref("webhook_event", str(event["id"])), "queued": True}


def _config(
    *,
    provider: str = "generic_rest",
    kind: str = "generic_rest",
    mode: str = "dry_run",
    allow_writes: bool = False,
    explicit_write_permission: bool = False,
) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="ext-fake",
        name="Fake connector",
        provider=provider,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        allow_writes=allow_writes,
        explicit_write_permission=explicit_write_permission,
    )


def _ref(record_type: str, record_id: str) -> ExternalRecordRef:
    return ExternalRecordRef(
        external_system_id="ext-fake",
        external_record_type=record_type,
        external_record_id=record_id,
        retrieved_at=datetime.now(UTC),
        metadata={},
    )
