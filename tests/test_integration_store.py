from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from molecule_ranker.integrations.schemas import (
    DataContract,
    EntityMapping,
    ExternalRecordRef,
    ExternalSystem,
    IntegrationCredential,
    SyncJob,
    SyncRecord,
)
from molecule_ranker.integrations.store import (
    IntegrationStore,
    integration_audit_events,
    integration_credentials,
    sync_records,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.schemas import UserAccount


def test_external_system_crud_writes_audit_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    system = ExternalSystem(
        external_system_id="ext-benchling",
        name="Benchling",
        system_type="eln",
        vendor="benchling",
        base_url="https://benchling.example",
        enabled=True,
        default_mode="dry_run",
    )

    created = store.create_external_system(system, org_id="org-a", project_id="proj-a")
    updated = store.update_external_system("ext-benchling", enabled=False, metadata={"tier": "dev"})

    assert created.external_system_id == "ext-benchling"
    assert store.get_external_system("ext-benchling") == updated
    assert store.list_external_systems(org_id="org-a", project_id="proj-a", enabled=False) == [
        updated
    ]
    assert updated.enabled is False
    assert _audit_types(store.database)[:2] == [
        "external_system_created",
        "external_system_updated",
    ]


def test_credential_reference_persists_without_plaintext_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHLING_TOKEN", "benchling-secret-value")
    store = _store(tmp_path)
    credential = IntegrationCredential(
        credential_id="cred-benchling",
        external_system_id="ext-benchling",
        credential_type="api_key",
        secret_ref="env:BENCHLING_TOKEN",
        metadata={"rotation": {"owner": "platform"}},
    )

    store.create_credential_reference(credential)

    with store.database.engine.connect() as connection:
        row = connection.execute(
            select(integration_credentials).where(
                integration_credentials.c.credential_id == "cred-benchling"
            )
        ).mappings().one()
    payload = json.dumps(dict(row), default=str, sort_keys=True)
    assert "benchling-secret-value" not in payload
    assert row["secret_hash"] is None
    assert row["secret_salt"] is None
    assert row["backend"] == "env"
    assert row["key_ref"] == "BENCHLING_TOKEN"
    assert "integration_credential_reference_created" in _audit_types(store.database)


def test_mapping_lookup_supports_internal_external_and_project_scopes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mapping = EntityMapping(
        mapping_id="map-1",
        project_id="proj-a",
        internal_entity_type="generated_molecule",
        internal_entity_id="mol-123",
        external_ref=ExternalRecordRef(
            external_system_id="ext-registry",
            external_record_type="registry_entry",
            external_record_id="REG-123",
        ),
        mapping_method="registry_id",
        mapping_confidence=0.99,
        status="active",
        created_by="user-a",
    )

    store.create_mapping(mapping, org_id="org-a")

    assert store.get_mapping("map-1") == mapping
    assert store.find_mappings(
        org_id="org-a",
        project_id="proj-a",
        internal_entity_id="mol-123",
    ) == [mapping]
    assert store.find_mappings(org_id="org-a", external_record_id="REG-123") == [mapping]
    assert store.find_mappings(org_id="org-a", project_id="other") == []


def test_sync_job_state_transition_and_raw_payload_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = SyncJob(
        sync_job_id="sync-1",
        external_system_id="ext-assay",
        project_id="proj-a",
        direction="import",
        object_types=["assay_result"],
        mode="dry_run",
        status="queued",
    )

    store.create_sync_job(job, org_id="org-a")
    running = store.update_sync_job("sync-1", status="running", started_at=datetime.now(UTC))
    completed = store.update_sync_job(
        "sync-1",
        status="succeeded",
        completed_at=datetime.now(UTC),
        records_seen=1,
        records_imported=1,
    )
    record = store.add_sync_record(
        SyncRecord(
            sync_record_id="srec-1",
            sync_job_id="sync-1",
            external_ref=ExternalRecordRef(
                external_system_id="ext-assay",
                external_record_type="assay_result",
                external_record_id="AR-1",
            ),
            internal_entity_type="assay_result",
            internal_entity_id="result-1",
            action="imported",
            status="succeeded",
        ),
        raw_payload={"blob": "x" * 5000},
    )

    assert running.status == "running"
    assert completed.status == "succeeded"
    assert completed.records_imported == 1
    assert store.list_sync_jobs(org_id="org-a", project_id="proj-a", status="succeeded") == [
        completed
    ]
    assert store.list_sync_records(sync_job_id="sync-1") == [record]
    assert record.raw_payload_artifact_id is not None
    artifact_path = Path(record.metadata["raw_payload_artifact"]["path"])
    assert artifact_path.exists()
    with store.database.engine.connect() as connection:
        row = connection.execute(
            select(sync_records).where(sync_records.c.sync_record_id == "srec-1")
        ).mappings().one()
    assert "x" * 5000 not in json.dumps(dict(row), default=str, sort_keys=True)
    assert "sync_record_added" in _audit_types(store.database)


def test_data_contract_validation_is_persisted_and_audited(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract = DataContract(
        contract_id="contract-assay",
        name="Assay result",
        object_type="assay_result",
        version="1.0",
        required_fields=["result_id", "value"],
        field_types={"result_id": "string", "value": "number"},
        controlled_vocabularies={"unit": ["nM"]},
    )

    store.create_data_contract(contract)
    valid = store.validate_against_contract(
        "contract-assay",
        [
            {
                "candidate_id": "cand-1",
                "result_id": "r1",
                "source_record_id": "src-1",
                "value": 1.2,
                "unit": "nM",
            }
        ],
    )
    invalid = store.validate_against_contract(
        "contract-assay",
        [
            {
                "candidate_id": "cand-2",
                "result_id": "r2",
                "source_record_id": "src-2",
                "value": "bad",
                "unit": "bogus",
            }
        ],
    )

    assert valid.valid is True
    assert invalid.valid is False
    assert invalid.issue_count == 3
    assert _audit_types(store.database).count("data_contract_validated") == 2


def test_service_layer_rbac_blocks_unprivileged_user(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = UserAccount(user_id="user-viewer", email="viewer@example.test", is_admin=False)
    store = IntegrationStore(database, user=user, org_id="org-a", project_id="proj-a")

    with pytest.raises(PermissionError):
        store.create_external_system(
            ExternalSystem(
                external_system_id="ext-rest",
                name="REST",
                system_type="generic_rest",
                vendor="generic",
            )
        )


def test_admin_user_can_manage_integrations_without_project_role(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    admin = UserAccount(user_id="admin", email="admin@example.test", is_admin=True)
    store = IntegrationStore(database, user=admin)

    created = store.create_external_system(
        ExternalSystem(
            external_system_id="ext-admin",
            name="Admin system",
            system_type="generic_file",
            vendor="generic",
        )
    )

    assert created.external_system_id == "ext-admin"


def _store(tmp_path: Path) -> IntegrationStore:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    return IntegrationStore(database, org_id="default")


def _audit_types(database: PlatformDatabase) -> list[str]:
    with database.engine.connect() as connection:
        rows = connection.execute(
            select(integration_audit_events.c.event_type).order_by(
                integration_audit_events.c.timestamp.asc()
            )
        ).fetchall()
    return [str(row.event_type) for row in rows]
