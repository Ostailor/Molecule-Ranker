from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.integrations.connectors.base import (
    ConnectorCallRecorder,
    ConnectorError,
    ExternalConnector,
)
from molecule_ranker.integrations.schemas import ConnectorConfig, DataContract, ExternalRecordRef
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.sync import SyncEngine, SyncRequest
from molecule_ranker.platform.db import PlatformDatabase


def test_import_sync(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connector = FakeSyncConnector(_config())

    job = SyncEngine(store).run(
        connector,
        SyncRequest(direction="import", object_types=["assay_results"], mode="dry_run"),
    )
    records = store.list_sync_records(sync_job_id=job.sync_job_id)

    assert job.status == "succeeded"
    assert job.records_seen == 1
    assert job.records_imported == 1
    assert records[0].external_ref.external_record_id == "result-1"
    assert records[0].raw_payload_artifact_id is not None


def test_export_dry_run_writes_artifact_without_external_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connector = FakeSyncConnector(_config())

    job = SyncEngine(store).run(
        connector,
        SyncRequest(
            direction="export",
            object_types=["review_dossiers"],
            mode="dry_run",
            export_records={"review_dossiers": [{"id": "review-1", "summary": "ok"}]},
        ),
    )
    records = store.list_sync_records(sync_job_id=job.sync_job_id)

    assert connector.export_calls == 0
    assert job.status == "succeeded"
    assert job.records_skipped == 1
    assert records[0].status == "skipped"
    assert records[0].metadata["dry_run"] is True


def test_write_blocked_without_write_enabled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connector = FakeSyncConnector(_config(mode="read_only"))

    with pytest.raises(ConnectorError, match="write_enabled"):
        SyncEngine(store).run(
            connector,
            SyncRequest(
                direction="export",
                object_types=["review_dossiers"],
                mode="write_enabled",
                export_records={"review_dossiers": [{"id": "review-1"}]},
            ),
        )


def test_partial_failure_records_failed_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connector = FakeSyncConnector(_config(), invalid=True)
    contract = DataContract(
        contract_id="assay-result-v1",
        name="Assay result",
        object_type="assay_result",
        version="1",
        required_fields=["candidate_id", "outcome_label", "value", "unit"],
        field_types={"candidate_id": "string", "outcome_label": "string", "value": "number"},
        controlled_vocabularies={"outcome_label": ["positive", "negative"]},
        identifier_fields=["candidate_id"],
    )

    job = SyncEngine(store).run(
        connector,
        SyncRequest(
            direction="import",
            object_types=["assay_results"],
            mode="dry_run",
            data_contracts={"assay_results": contract},
        ),
    )
    records = store.list_sync_records(sync_job_id=job.sync_job_id, status="failed")

    assert job.status == "partial"
    assert job.records_failed == 1
    assert records[0].validation_errors


def test_idempotent_duplicate_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    connector = FakeSyncConnector(_config())
    request = SyncRequest(direction="import", object_types=["assay_results"], mode="dry_run")

    first = SyncEngine(store).run(connector, request)
    second = SyncEngine(store).run(connector, request)

    assert first.records_imported == 1
    assert second.records_skipped == 1
    duplicate_records = store.list_sync_records(sync_job_id=second.sync_job_id)
    assert duplicate_records[0].metadata["idempotency"] == "duplicate_payload_hash"


class FakeSyncConnector(ExternalConnector):
    connector_name = "fake-sync"

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        invalid: bool = False,
    ) -> None:
        super().__init__(config, recorder=ConnectorCallRecorder())
        self.invalid = invalid
        self.export_calls = 0

    def import_assay_results(self) -> list[dict[str, Any]]:
        outcome = "invalid" if self.invalid else "positive"
        return [
            {
                "external_ref": _ref("result-1"),
                "payload": {
                    "source_record_id": "result-1",
                    "candidate_id": "cand-1",
                    "outcome_label": outcome,
                    "value": 1.0,
                    "unit": "nM",
                },
            }
        ]

    def create_notebook_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.export_calls += 1
        return {"external_ref": _ref("entry-1"), "payload": payload}


def _store(tmp_path: Path) -> IntegrationStore:
    return IntegrationStore(
        PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite"),
        org_id="default",
    )


def _config(*, mode: str = "dry_run") -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="sync-test",
        name="Sync test",
        provider="generic_rest",
        kind="generic_rest",
        mode=mode,  # type: ignore[arg-type]
        allow_writes=mode == "write_enabled",
        explicit_write_permission=mode == "write_enabled",
    )


def _ref(record_id: str) -> ExternalRecordRef:
    return ExternalRecordRef(
        external_system_id="sync-test",
        external_record_type="assay_result",
        external_record_id=record_id,
        metadata={},
    )
