from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.integrations.schemas import (
    DataContract,
    EntityMapping,
    ExternalRecordRef,
    ExternalSystem,
    IntegrationAuditEvent,
    IntegrationCredential,
    SyncJob,
    SyncRecord,
)


def test_external_system_validates_mode_type_and_timezone() -> None:
    system = ExternalSystem(
        external_system_id="ext-benchling",
        name="Benchling",
        system_type="eln",
        vendor="benchling",
        base_url="https://example.benchling.com",
        enabled=True,
        default_mode="dry_run",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"tenant": "research"},
    )

    assert system.default_mode == "dry_run"

    with pytest.raises(ValidationError):
        ExternalSystem.model_validate(
            {
                "external_system_id": "ext-bad",
                "name": "Bad",
                "system_type": "device_controller",
                "enabled": True,
                "default_mode": "dry_run",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError):
        ExternalSystem(
            external_system_id="ext-naive",
            name="Naive",
            system_type="eln",
            enabled=True,
            default_mode="dry_run",
            created_at=datetime(2026, 1, 1),
            updated_at=datetime.now(UTC),
            metadata={},
        )


def test_integration_credential_uses_secret_reference_not_secret_value() -> None:
    credential = IntegrationCredential(
        credential_id="cred-1",
        external_system_id="ext-benchling",
        credential_type="api_key",
        secret_ref="env:BENCHLING_API_KEY",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        expires_at=None,
        last_used_at=None,
        metadata={},
    )

    assert credential.secret_ref == "env:BENCHLING_API_KEY"

    with pytest.raises(ValidationError):
        IntegrationCredential(
            credential_id="cred-2",
            external_system_id="ext-benchling",
            credential_type="api_key",
            secret_ref="sk-secretsecretsecretsecret",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
            metadata={},
        )


def test_entity_mapping_confidence_and_codex_pending_guardrail() -> None:
    external_ref = ExternalRecordRef(
        external_system_id="ext-benchling",
        external_record_type="molecule",
        external_record_id="mol-1",
        retrieved_at=datetime.now(UTC),
        metadata={},
    )
    mapping = EntityMapping(
        mapping_id="map-1",
        project_id="project-1",
        internal_entity_type="candidate",
        internal_entity_id="candidate-1",
        external_ref=external_ref,
        mapping_method="exact_id",
        mapping_confidence=1.0,
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by="user-1",
        metadata={},
    )

    assert mapping.mapping_confidence == 1.0

    with pytest.raises(ValidationError):
        EntityMapping(
            mapping_id="map-bad-confidence",
            project_id=None,
            internal_entity_type="candidate",
            internal_entity_id="candidate-1",
            external_ref=external_ref,
            mapping_method="exact_id",
            mapping_confidence=1.1,
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=None,
            metadata={},
        )

    with pytest.raises(ValidationError):
        EntityMapping(
            mapping_id="map-codex-active",
            project_id=None,
            internal_entity_type="candidate",
            internal_entity_id="candidate-1",
            external_ref=external_ref,
            mapping_method="codex_suggested_pending_validation",
            mapping_confidence=0.6,
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=None,
            metadata={},
        )


def test_sync_job_record_and_audit_schemas_validate_literals() -> None:
    external_ref = ExternalRecordRef(
        external_system_id="ext-warehouse",
        external_record_type="warehouse_row",
        external_record_id="row-1",
        metadata={},
    )
    job = SyncJob(
        sync_job_id="sync-1",
        external_system_id="ext-warehouse",
        project_id="project-1",
        direction="import",
        object_types=["warehouse_row"],
        mode="dry_run",
        status="queued",
        requested_by_user_id="user-1",
        records_seen=0,
        records_imported=0,
        records_exported=0,
        records_skipped=0,
        records_failed=0,
        artifact_ids=[],
        warnings=[],
        error_summary=None,
        metadata={},
    )
    record = SyncRecord(
        sync_record_id="sync-record-1",
        sync_job_id=job.sync_job_id,
        external_ref=external_ref,
        internal_entity_type=None,
        internal_entity_id=None,
        action="skipped",
        status="skipped",
        validation_errors=[],
        warnings=[],
        raw_payload_artifact_id=None,
        created_at=datetime.now(UTC),
        metadata={},
    )
    event = IntegrationAuditEvent(
        event_id="evt-1",
        external_system_id="ext-warehouse",
        sync_job_id=job.sync_job_id,
        actor_user_id="user-1",
        event_type="sync_queued",
        timestamp=datetime.now(UTC),
        object_type="sync_job",
        object_id=job.sync_job_id,
        summary="Queued sync.",
        metadata={},
    )

    assert record.action == "skipped"
    assert event.object_id == "sync-1"

    with pytest.raises(ValidationError):
        SyncJob.model_validate(
            {
                "sync_job_id": "sync-bad",
                "external_system_id": "ext-warehouse",
                "direction": "delete",
                "object_types": [],
                "mode": "dry_run",
                "status": "queued",
                "records_seen": 0,
                "records_imported": 0,
                "records_exported": 0,
                "records_skipped": 0,
                "records_failed": 0,
                "artifact_ids": [],
                "warnings": [],
                "metadata": {},
            }
        )


def test_data_contract_schema_fields_are_explicit() -> None:
    contract = DataContract(
        contract_id="assay-result-v1",
        name="Assay result",
        object_type="assay_result",
        version="1",
        required_fields=["assay_name", "outcome"],
        optional_fields=["value", "unit"],
        field_types={"assay_name": "string", "value": "number"},
        controlled_vocabularies={"outcome": ["positive", "negative", "inconclusive"]},
        identifier_fields=["external_result_id"],
        validation_rules=[{"rule": "no_invented_results"}],
        metadata={"source": "integration"},
    )

    assert contract.required_fields == ["assay_name", "outcome"]
    assert contract.controlled_vocabularies["outcome"] == [
        "positive",
        "negative",
        "inconclusive",
    ]
