from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.credentials import (
    SecretRef,
    redact_secret_values,
    validate_secret_ref,
)
from molecule_ranker.integrations.schemas import (
    DataContract,
    DataContractValidationReport,
    EntityMapping,
    ExternalRecordRef,
    ExternalSystem,
    IntegrationAuditEvent,
    IntegrationCredential,
    SyncJob,
    SyncRecord,
)
from molecule_ranker.integrations.validation import validate_data_contract
from molecule_ranker.platform.database import (
    PlatformDatabase,
    artifact_records,
    integration_credentials,
    metadata,
)
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount

DEFAULT_ORG_ID = "default"
RAW_PAYLOAD_ARTIFACT_LIMIT_BYTES = 4096


external_systems = Table(
    "external_systems",
    metadata,
    Column("external_system_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("name", String(255), nullable=False),
    Column("system_type", String(64), nullable=False, index=True),
    Column("vendor", String(64), nullable=True, index=True),
    Column("base_url", Text, nullable=True),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("default_mode", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

entity_mappings = Table(
    "entity_mappings",
    metadata,
    Column("mapping_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("internal_entity_type", String(64), nullable=False, index=True),
    Column("internal_entity_id", String(255), nullable=False, index=True),
    Column("external_system_id", String(128), nullable=False, index=True),
    Column("external_record_type", String(128), nullable=False, index=True),
    Column("external_record_id", String(255), nullable=False, index=True),
    Column("external_ref_json", JSON, nullable=False, default=dict),
    Column("mapping_method", String(128), nullable=False, index=True),
    Column("mapping_confidence", Integer, nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String(128), nullable=True, index=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

sync_jobs = Table(
    "sync_jobs",
    metadata,
    Column("sync_job_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("external_system_id", String(128), nullable=False, index=True),
    Column("direction", String(64), nullable=False),
    Column("object_types_json", JSON, nullable=False, default=list),
    Column("mode", String(64), nullable=False, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("requested_by_user_id", String(128), nullable=True, index=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("records_seen", Integer, nullable=False, default=0),
    Column("records_imported", Integer, nullable=False, default=0),
    Column("records_exported", Integer, nullable=False, default=0),
    Column("records_skipped", Integer, nullable=False, default=0),
    Column("records_failed", Integer, nullable=False, default=0),
    Column("artifact_ids_json", JSON, nullable=False, default=list),
    Column("warnings_json", JSON, nullable=False, default=list),
    Column("error_summary", Text, nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

sync_records = Table(
    "sync_records",
    metadata,
    Column("sync_record_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("sync_job_id", String(128), nullable=False, index=True),
    Column("external_system_id", String(128), nullable=False, index=True),
    Column("external_record_type", String(128), nullable=False, index=True),
    Column("external_record_id", String(255), nullable=False, index=True),
    Column("external_ref_json", JSON, nullable=False, default=dict),
    Column("internal_entity_type", String(64), nullable=True, index=True),
    Column("internal_entity_id", String(255), nullable=True, index=True),
    Column("action", String(64), nullable=False, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("validation_errors_json", JSON, nullable=False, default=list),
    Column("warnings_json", JSON, nullable=False, default=list),
    Column("raw_payload_artifact_id", String(128), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

integration_audit_events = Table(
    "integration_audit_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("external_system_id", String(128), nullable=True, index=True),
    Column("sync_job_id", String(128), nullable=True, index=True),
    Column("actor_user_id", String(128), nullable=True, index=True),
    Column("event_type", String(128), nullable=False, index=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("object_type", String(128), nullable=False),
    Column("object_id", String(255), nullable=False, index=True),
    Column("summary", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

data_contracts = Table(
    "data_contracts",
    metadata,
    Column("contract_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("name", String(255), nullable=False),
    Column("object_type", String(128), nullable=False, index=True),
    Column("version", String(64), nullable=False),
    Column("required_fields_json", JSON, nullable=False, default=list),
    Column("optional_fields_json", JSON, nullable=False, default=list),
    Column("field_types_json", JSON, nullable=False, default=dict),
    Column("controlled_vocabularies_json", JSON, nullable=False, default=dict),
    Column("identifier_fields_json", JSON, nullable=False, default=list),
    Column("validation_rules_json", JSON, nullable=False, default=list),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

webhook_events = Table(
    "webhook_events",
    metadata,
    Column("webhook_event_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("external_system_id", String(128), nullable=True, index=True),
    Column("event_type", String(128), nullable=False, index=True),
    Column("source_event_id", String(255), nullable=True, index=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("payload_artifact_id", String(128), nullable=True, index=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
    extend_existing=True,
)

INTEGRATION_STORE_TABLES = [
    external_systems,
    entity_mappings,
    sync_jobs,
    sync_records,
    integration_audit_events,
    data_contracts,
    webhook_events,
]


class IntegrationStore:
    """Persistent V0.9 integration store backed by the platform database."""

    def __init__(
        self,
        database: PlatformDatabase,
        *,
        user: UserAccount | None = None,
        org_id: str = DEFAULT_ORG_ID,
        project_id: str | None = None,
        artifact_inline_limit: int = RAW_PAYLOAD_ARTIFACT_LIMIT_BYTES,
    ) -> None:
        self.database = database
        self.user = user
        self.org_id = org_id
        self.project_id = project_id
        self.artifact_inline_limit = artifact_inline_limit
        for table in INTEGRATION_STORE_TABLES:
            table.create(self.database.engine, checkfirst=True)

    def create_external_system(
        self,
        system: ExternalSystem,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> ExternalSystem:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:write", org_id=org_scope, project_id=project_scope)
        stored = system.model_copy(update={"metadata": _redact_json(system.metadata)})
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(external_systems).values(
                    external_system_id=stored.external_system_id,
                    org_id=org_scope,
                    project_id=project_scope,
                    name=stored.name,
                    system_type=stored.system_type,
                    vendor=stored.vendor,
                    base_url=stored.base_url,
                    enabled=stored.enabled,
                    default_mode=stored.default_mode,
                    created_at=stored.created_at,
                    updated_at=stored.updated_at,
                    metadata_json=stored.metadata,
                )
            )
        self._audit_write(
            event_type="external_system_created",
            object_type="external_system",
            object_id=stored.external_system_id,
            external_system_id=stored.external_system_id,
            summary=f"Created external system {stored.external_system_id}.",
            org_id=org_scope,
            project_id=project_scope,
            metadata={"system_type": stored.system_type, "vendor": stored.vendor},
        )
        return stored

    def get_external_system(self, external_system_id: str) -> ExternalSystem | None:
        self._require("integration:read")
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(external_systems).where(
                        external_systems.c.external_system_id == external_system_id
                    )
                )
                .mappings()
                .first()
            )
        return _external_system(row) if row else None

    def list_external_systems(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[ExternalSystem]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:read", org_id=org_scope, project_id=project_scope)
        statement = select(external_systems).where(external_systems.c.org_id == org_scope)
        if project_scope is not None:
            statement = statement.where(external_systems.c.project_id == project_scope)
        if enabled is not None:
            statement = statement.where(external_systems.c.enabled == enabled)
        statement = statement.order_by(external_systems.c.created_at.desc())
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_external_system(row) for row in rows]

    def update_external_system(self, external_system_id: str, **updates: Any) -> ExternalSystem:
        current = self.get_external_system(external_system_id)
        if current is None:
            raise KeyError(f"External system not found: {external_system_id}")
        self._require("integration:write")
        now = datetime.now(UTC)
        allowed = {
            "name",
            "system_type",
            "vendor",
            "base_url",
            "enabled",
            "default_mode",
            "metadata",
        }
        clean_updates = {key: value for key, value in updates.items() if key in allowed}
        clean_updates["updated_at"] = now
        if "metadata" in clean_updates:
            clean_updates["metadata"] = _redact_json(clean_updates["metadata"])
        updated = current.model_copy(update=clean_updates)
        with self.database.engine.begin() as connection:
            connection.execute(
                update(external_systems)
                .where(external_systems.c.external_system_id == external_system_id)
                .values(
                    name=updated.name,
                    system_type=updated.system_type,
                    vendor=updated.vendor,
                    base_url=updated.base_url,
                    enabled=updated.enabled,
                    default_mode=updated.default_mode,
                    updated_at=updated.updated_at,
                    metadata_json=updated.metadata,
                )
            )
        self._audit_write(
            event_type="external_system_updated",
            object_type="external_system",
            object_id=external_system_id,
            external_system_id=external_system_id,
            summary=f"Updated external system {external_system_id}.",
            metadata={"updated_fields": sorted(clean_updates)},
        )
        return updated

    def create_credential_reference(
        self,
        credential: IntegrationCredential,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> IntegrationCredential:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:manage_credentials", org_id=org_scope, project_id=project_scope)
        secret_ref = validate_secret_ref(credential.secret_ref)
        stored = credential.model_copy(update={"secret_ref": secret_ref.as_string()})
        metadata_json = {
            "external_system_id": stored.external_system_id,
            "credential_type": stored.credential_type,
            "secret_ref": stored.secret_ref,
            "updated_at": stored.updated_at.isoformat(),
            "expires_at": stored.expires_at.isoformat() if stored.expires_at else None,
            "last_used_at": stored.last_used_at.isoformat() if stored.last_used_at else None,
            "metadata": _redact_json(stored.metadata),
            "org_id": org_scope,
            "project_id": project_scope,
        }
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(integration_credentials).values(
                    credential_id=stored.credential_id,
                    connector_id=stored.external_system_id,
                    name=stored.credential_type,
                    backend=secret_ref.ref_type,
                    key_ref=secret_ref.reference,
                    secret_hash=None,
                    secret_salt=None,
                    created_by_user_id=self._actor_user_id(),
                    created_at=stored.created_at,
                    revoked_at=None,
                    metadata_json=_redact_json(metadata_json),
                )
            )
        self._audit_write(
            event_type="integration_credential_reference_created",
            object_type="integration_credential",
            object_id=stored.credential_id,
            external_system_id=stored.external_system_id,
            summary=f"Created credential reference {stored.credential_id}.",
            org_id=org_scope,
            project_id=project_scope,
            metadata={"credential_id": stored.credential_id, "backend": secret_ref.ref_type},
        )
        return stored

    def create_mapping(
        self,
        mapping: EntityMapping,
        *,
        org_id: str | None = None,
    ) -> EntityMapping:
        org_scope = org_id or self.org_id
        self._require("integration:write", org_id=org_scope, project_id=mapping.project_id)
        stored = mapping.model_copy(update={"metadata": _redact_json(mapping.metadata)})
        external_ref = stored.external_ref.model_dump(mode="json")
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(entity_mappings).values(
                    mapping_id=stored.mapping_id,
                    org_id=org_scope,
                    project_id=stored.project_id,
                    internal_entity_type=stored.internal_entity_type,
                    internal_entity_id=stored.internal_entity_id,
                    external_system_id=stored.external_ref.external_system_id,
                    external_record_type=stored.external_ref.external_record_type,
                    external_record_id=stored.external_ref.external_record_id,
                    external_ref_json=_redact_json(external_ref),
                    mapping_method=stored.mapping_method,
                    mapping_confidence=int(stored.mapping_confidence * 1_000_000),
                    status=stored.status,
                    created_at=stored.created_at,
                    updated_at=stored.updated_at,
                    created_by=stored.created_by,
                    metadata_json=stored.metadata,
                )
            )
        self._audit_write(
            event_type="entity_mapping_created",
            object_type="entity_mapping",
            object_id=stored.mapping_id,
            external_system_id=stored.external_ref.external_system_id,
            summary=f"Created entity mapping {stored.mapping_id}.",
            org_id=org_scope,
            project_id=stored.project_id,
            metadata={
                "mapping_method": stored.mapping_method,
                "status": stored.status,
                "internal_entity_type": stored.internal_entity_type,
            },
        )
        return stored

    def get_mapping(self, mapping_id: str) -> EntityMapping | None:
        self._require("integration:read")
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(entity_mappings).where(entity_mappings.c.mapping_id == mapping_id)
                )
                .mappings()
                .first()
            )
        return _entity_mapping(row) if row else None

    def update_mapping_status(
        self,
        mapping_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> EntityMapping:
        current = self.get_mapping(mapping_id)
        if current is None:
            raise KeyError(f"Entity mapping not found: {mapping_id}")
        self._require("integration:approve_mapping", project_id=current.project_id)
        updated_metadata = _redact_json({**current.metadata, **(metadata or {})})
        updated = current.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(UTC),
                "metadata": updated_metadata,
            }
        )
        with self.database.engine.begin() as connection:
            connection.execute(
                update(entity_mappings)
                .where(entity_mappings.c.mapping_id == mapping_id)
                .values(
                    status=updated.status,
                    updated_at=updated.updated_at,
                    metadata_json=updated.metadata,
                )
            )
        self._audit_write(
            event_type=f"entity_mapping_{status}",
            object_type="entity_mapping",
            object_id=mapping_id,
            external_system_id=updated.external_ref.external_system_id,
            summary=f"Updated entity mapping {mapping_id} to {status}.",
            project_id=updated.project_id,
            metadata={"status": status},
        )
        return updated

    def find_mappings(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        internal_entity_type: str | None = None,
        internal_entity_id: str | None = None,
        external_system_id: str | None = None,
        external_record_id: str | None = None,
        status: str | None = None,
    ) -> list[EntityMapping]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:read", org_id=org_scope, project_id=project_scope)
        statement = select(entity_mappings).where(entity_mappings.c.org_id == org_scope)
        if project_scope is not None:
            statement = statement.where(entity_mappings.c.project_id == project_scope)
        if internal_entity_type is not None:
            statement = statement.where(
                entity_mappings.c.internal_entity_type == internal_entity_type
            )
        if internal_entity_id is not None:
            statement = statement.where(entity_mappings.c.internal_entity_id == internal_entity_id)
        if external_system_id is not None:
            statement = statement.where(entity_mappings.c.external_system_id == external_system_id)
        if external_record_id is not None:
            statement = statement.where(entity_mappings.c.external_record_id == external_record_id)
        if status is not None:
            statement = statement.where(entity_mappings.c.status == status)
        statement = statement.order_by(entity_mappings.c.updated_at.desc())
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_entity_mapping(row) for row in rows]

    def create_sync_job(
        self,
        job: SyncJob,
        *,
        org_id: str | None = None,
    ) -> SyncJob:
        org_scope = org_id or self.org_id
        self._require("integration:sync", org_id=org_scope, project_id=job.project_id)
        stored = job.model_copy(update={"metadata": _redact_json(job.metadata)})
        with self.database.engine.begin() as connection:
            connection.execute(insert(sync_jobs).values(**_sync_job_values(stored, org_scope)))
        self._audit_write(
            event_type="sync_job_created",
            object_type="sync_job",
            object_id=stored.sync_job_id,
            external_system_id=stored.external_system_id,
            sync_job_id=stored.sync_job_id,
            summary=f"Created sync job {stored.sync_job_id}.",
            org_id=org_scope,
            project_id=stored.project_id,
            metadata={"direction": stored.direction, "mode": stored.mode, "status": stored.status},
        )
        return stored

    def update_sync_job(self, sync_job_id: str, **updates: Any) -> SyncJob:
        current = self._get_sync_job(sync_job_id)
        if current is None:
            raise KeyError(f"Sync job not found: {sync_job_id}")
        self._require("integration:sync", project_id=current.project_id)
        allowed = {
            "status",
            "started_at",
            "completed_at",
            "records_seen",
            "records_imported",
            "records_exported",
            "records_skipped",
            "records_failed",
            "artifact_ids",
            "warnings",
            "error_summary",
            "metadata",
        }
        clean_updates = {key: value for key, value in updates.items() if key in allowed}
        if "metadata" in clean_updates:
            clean_updates["metadata"] = _redact_json(clean_updates["metadata"])
        updated = current.model_copy(update=clean_updates)
        with self.database.engine.begin() as connection:
            connection.execute(
                update(sync_jobs)
                .where(sync_jobs.c.sync_job_id == sync_job_id)
                .values(
                    status=updated.status,
                    started_at=updated.started_at,
                    completed_at=updated.completed_at,
                    records_seen=updated.records_seen,
                    records_imported=updated.records_imported,
                    records_exported=updated.records_exported,
                    records_skipped=updated.records_skipped,
                    records_failed=updated.records_failed,
                    artifact_ids_json=updated.artifact_ids,
                    warnings_json=_redact_json(updated.warnings),
                    error_summary=redact_secret_values(updated.error_summary or "")
                    if updated.error_summary
                    else None,
                    metadata_json=updated.metadata,
                )
            )
        self._audit_write(
            event_type="sync_job_updated",
            object_type="sync_job",
            object_id=sync_job_id,
            external_system_id=updated.external_system_id,
            sync_job_id=sync_job_id,
            summary=f"Updated sync job {sync_job_id} to {updated.status}.",
            project_id=updated.project_id,
            metadata={"updated_fields": sorted(clean_updates), "status": updated.status},
        )
        return updated

    def add_sync_record(self, record: SyncRecord, *, raw_payload: Any | None = None) -> SyncRecord:
        job = self._get_sync_job(record.sync_job_id)
        if job is None:
            raise KeyError(f"Sync job not found: {record.sync_job_id}")
        self._require("integration:sync", project_id=job.project_id)
        metadata_json = _redact_json(record.metadata)
        artifact_id = record.raw_payload_artifact_id
        if raw_payload is not None:
            artifact = self._store_raw_payload_artifact(
                raw_payload,
                sync_job_id=record.sync_job_id,
                org_id=self.org_id,
                project_id=job.project_id,
            )
            artifact_id = artifact["artifact_id"]
            metadata_json = {
                **metadata_json,
                "raw_payload_artifact": artifact,
            }
        stored = record.model_copy(
            update={"raw_payload_artifact_id": artifact_id, "metadata": metadata_json}
        )
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(sync_records).values(
                    sync_record_id=stored.sync_record_id,
                    org_id=self.org_id,
                    project_id=job.project_id,
                    sync_job_id=stored.sync_job_id,
                    external_system_id=stored.external_ref.external_system_id,
                    external_record_type=stored.external_ref.external_record_type,
                    external_record_id=stored.external_ref.external_record_id,
                    external_ref_json=_redact_json(stored.external_ref.model_dump(mode="json")),
                    internal_entity_type=stored.internal_entity_type,
                    internal_entity_id=stored.internal_entity_id,
                    action=stored.action,
                    status=stored.status,
                    validation_errors_json=_redact_json(stored.validation_errors),
                    warnings_json=_redact_json(stored.warnings),
                    raw_payload_artifact_id=stored.raw_payload_artifact_id,
                    created_at=stored.created_at,
                    metadata_json=stored.metadata,
                )
            )
        self._audit_write(
            event_type="sync_record_added",
            object_type="sync_record",
            object_id=stored.sync_record_id,
            external_system_id=stored.external_ref.external_system_id,
            sync_job_id=stored.sync_job_id,
            summary=f"Added sync record {stored.sync_record_id}.",
            project_id=job.project_id,
            metadata={"action": stored.action, "status": stored.status},
        )
        return stored

    def list_sync_jobs(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        external_system_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SyncJob]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:read", org_id=org_scope, project_id=project_scope)
        statement = select(sync_jobs).where(sync_jobs.c.org_id == org_scope)
        if project_scope is not None:
            statement = statement.where(sync_jobs.c.project_id == project_scope)
        if external_system_id is not None:
            statement = statement.where(sync_jobs.c.external_system_id == external_system_id)
        if status is not None:
            statement = statement.where(sync_jobs.c.status == status)
        statement = statement.order_by(sync_jobs.c.started_at.desc()).limit(limit)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_sync_job(row) for row in rows]

    def get_sync_job(self, sync_job_id: str) -> SyncJob | None:
        job = self._get_sync_job(sync_job_id)
        if job is not None:
            self._require("integration:read", project_id=job.project_id)
        return job

    def list_sync_records(
        self,
        *,
        sync_job_id: str | None = None,
        external_system_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[SyncRecord]:
        self._require("integration:read")
        statement = select(sync_records)
        if sync_job_id is not None:
            statement = statement.where(sync_records.c.sync_job_id == sync_job_id)
        if external_system_id is not None:
            statement = statement.where(sync_records.c.external_system_id == external_system_id)
        if status is not None:
            statement = statement.where(sync_records.c.status == status)
        statement = statement.order_by(sync_records.c.created_at.desc()).limit(limit)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_sync_record(row) for row in rows]

    def list_credential_references(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        include_revoked: bool = False,
    ) -> list[IntegrationCredential]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:manage_credentials", org_id=org_scope, project_id=project_scope)
        statement = select(integration_credentials)
        if not include_revoked:
            statement = statement.where(integration_credentials.c.revoked_at.is_(None))
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        credentials = [_credential(row) for row in rows]
        return [
            credential
            for credential in credentials
            if (credential.metadata.get("org_id") or org_scope) == org_scope
            and (
                project_scope is None
                or credential.metadata.get("project_id") in {None, project_scope}
            )
        ]

    def list_webhook_events(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        external_system_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:read", org_id=org_scope, project_id=project_scope)
        statement = select(webhook_events).where(webhook_events.c.org_id == org_scope)
        if project_scope is not None:
            statement = statement.where(webhook_events.c.project_id == project_scope)
        if external_system_id is not None:
            statement = statement.where(webhook_events.c.external_system_id == external_system_id)
        statement = statement.order_by(webhook_events.c.received_at.desc()).limit(limit)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_public_row(row) for row in rows]

    def list_data_contracts(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> list[DataContract]:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:read", org_id=org_scope, project_id=project_scope)
        statement = select(data_contracts).where(data_contracts.c.org_id == org_scope)
        if project_scope is not None:
            statement = statement.where(data_contracts.c.project_id == project_scope)
        statement = statement.order_by(data_contracts.c.name)
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_data_contract(row) for row in rows]

    def write_audit_event(
        self,
        event: IntegrationAuditEvent,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> IntegrationAuditEvent:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:view_audit", org_id=org_scope, project_id=project_scope)
        stored = event.model_copy(
            update={
                "summary": redact_secret_values(event.summary),
                "metadata": _redact_json(event.metadata),
            }
        )
        self._insert_audit_event(stored, org_id=org_scope, project_id=project_scope)
        self._write_platform_audit(stored, org_id=org_scope, project_id=project_scope)
        return stored

    def create_data_contract(
        self,
        contract: DataContract,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> DataContract:
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._require("integration:write", org_id=org_scope, project_id=project_scope)
        stored = contract.model_copy(update={"metadata": _redact_json(contract.metadata)})
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(data_contracts).values(
                    contract_id=stored.contract_id,
                    org_id=org_scope,
                    project_id=project_scope,
                    name=stored.name,
                    object_type=stored.object_type,
                    version=stored.version,
                    required_fields_json=stored.required_fields,
                    optional_fields_json=stored.optional_fields,
                    field_types_json=stored.field_types,
                    controlled_vocabularies_json=stored.controlled_vocabularies,
                    identifier_fields_json=stored.identifier_fields,
                    validation_rules_json=_redact_json(stored.validation_rules),
                    metadata_json=stored.metadata,
                )
            )
        self._audit_write(
            event_type="data_contract_created",
            object_type="data_contract",
            object_id=stored.contract_id,
            summary=f"Created data contract {stored.contract_id}.",
            org_id=org_scope,
            project_id=project_scope,
            metadata={"object_type": stored.object_type, "version": stored.version},
        )
        return stored

    def validate_against_contract(
        self,
        contract_id: str,
        rows: list[dict[str, Any]],
    ) -> DataContractValidationReport:
        self._require("integration:read")
        contract = self._get_data_contract(contract_id)
        if contract is None:
            raise KeyError(f"Data contract not found: {contract_id}")
        report = validate_data_contract(rows, contract)
        self._audit_write(
            event_type="data_contract_validated",
            object_type="data_contract",
            object_id=contract_id,
            summary=f"Validated {report.row_count} rows against data contract {contract_id}.",
            metadata={
                "row_count": report.row_count,
                "issue_count": report.issue_count,
                "valid": report.valid,
            },
        )
        return report

    def _get_sync_job(self, sync_job_id: str) -> SyncJob | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(sync_jobs).where(sync_jobs.c.sync_job_id == sync_job_id))
                .mappings()
                .first()
            )
        return _sync_job(row) if row else None

    def _get_data_contract(self, contract_id: str) -> DataContract | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(data_contracts).where(data_contracts.c.contract_id == contract_id)
                )
                .mappings()
                .first()
            )
        return _data_contract(row) if row else None

    def _require(
        self,
        permission: str,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        if self.user is None:
            return
        if self.user.is_admin:
            return
        if not has_permission(
            self.user,
            permission,
            org_id=org_id or self.org_id,
            project_id=project_id if project_id is not None else self.project_id,
            database=self.database,
        ):
            raise PermissionError(f"User {self.user.user_id} lacks {permission}")

    def _actor_user_id(self) -> str | None:
        return self.user.user_id if self.user else None

    def _audit_write(
        self,
        *,
        event_type: str,
        object_type: str,
        object_id: str,
        summary: str,
        external_system_id: str | None = None,
        sync_job_id: str | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IntegrationAuditEvent:
        event = IntegrationAuditEvent(
            event_id=f"iaudit-{uuid.uuid4().hex[:16]}",
            external_system_id=external_system_id,
            sync_job_id=sync_job_id,
            actor_user_id=self._actor_user_id(),
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            summary=summary,
            metadata=_redact_json(metadata or {}),
        )
        org_scope = org_id or self.org_id
        project_scope = project_id if project_id is not None else self.project_id
        self._insert_audit_event(event, org_id=org_scope, project_id=project_scope)
        self._write_platform_audit(event, org_id=org_scope, project_id=project_scope)
        return event

    def _insert_audit_event(
        self,
        event: IntegrationAuditEvent,
        *,
        org_id: str,
        project_id: str | None,
    ) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(integration_audit_events).values(
                    event_id=event.event_id,
                    org_id=org_id,
                    project_id=project_id,
                    external_system_id=event.external_system_id,
                    sync_job_id=event.sync_job_id,
                    actor_user_id=event.actor_user_id,
                    event_type=event.event_type,
                    timestamp=event.timestamp,
                    object_type=event.object_type,
                    object_id=event.object_id,
                    summary=redact_secret_values(event.summary),
                    metadata_json=_redact_json(event.metadata),
                )
            )

    def _write_platform_audit(
        self,
        event: IntegrationAuditEvent,
        *,
        org_id: str,
        project_id: str | None,
    ) -> None:
        self.database.write_audit(
            event.event_type,
            actor_user_id=event.actor_user_id,
            org_id=org_id,
            project_id=project_id,
            object_type=event.object_type,
            object_id=event.object_id,
            summary=event.summary,
            metadata=event.metadata,
        )

    def _store_raw_payload_artifact(
        self,
        payload: Any,
        *,
        sync_job_id: str,
        org_id: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        serialized = json.dumps(_redact_json(payload), sort_keys=True, default=str).encode()
        digest = hashlib.sha256(serialized).hexdigest()
        artifact_id = f"artifact-{digest[:16]}"
        artifact_dir = self.database.root_dir / ".molecule-ranker" / "integration-artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{artifact_id}.json"
        path.write_bytes(serialized)
        artifact = {
            "artifact_id": artifact_id,
            "sha256": digest,
            "path": str(path),
            "size_bytes": len(serialized),
        }
        with self.database.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(artifact_records.c.artifact_id).where(
                        artifact_records.c.artifact_id == artifact_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(
                    insert(artifact_records).values(
                        artifact_id=artifact_id,
                        org_id=org_id,
                        project_id=project_id,
                        run_id=None,
                        artifact_type="integration_raw_payload",
                        path=str(path),
                        sha256=digest,
                        size_bytes=len(serialized),
                        provenance_json={"sync_job_id": sync_job_id},
                        created_at=datetime.now(UTC),
                        metadata_json={},
                    )
                )
        return artifact


def _external_system(row: Any) -> ExternalSystem:
    return ExternalSystem(
        external_system_id=str(row["external_system_id"]),
        name=str(row["name"]),
        system_type=str(row["system_type"]),  # type: ignore[arg-type]
        vendor=row["vendor"],
        base_url=row["base_url"],
        enabled=bool(row["enabled"]),
        default_mode=str(row["default_mode"]),  # type: ignore[arg-type]
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _credential(row: Any) -> IntegrationCredential:
    metadata_json = dict(row["metadata_json"] or {})
    secret_ref = str(metadata_json.get("secret_ref") or _secret_ref_from_row(row))
    credential_type = str(metadata_json.get("credential_type") or row["name"])
    if credential_type not in {
        "api_key",
        "bearer_token",
        "oauth_client",
        "basic_auth",
        "database_password",
        "service_account",
        "ssh_key_reference",
    }:
        credential_type = "api_key"
    nested_metadata = {
        **cast(dict[str, Any], metadata_json.get("metadata") or {}),
        "org_id": metadata_json.get("org_id"),
        "project_id": metadata_json.get("project_id"),
        "display_name": row["name"],
    }
    return IntegrationCredential(
        credential_id=str(row["credential_id"]),
        external_system_id=str(
            metadata_json.get("external_system_id") or row["connector_id"] or ""
        ),
        credential_type=credential_type,  # type: ignore[arg-type]
        secret_ref=secret_ref,
        created_at=_aware(row["created_at"]),
        updated_at=_aware(metadata_json.get("updated_at") or row["created_at"]),
        expires_at=_aware(metadata_json["expires_at"]) if metadata_json.get("expires_at") else None,
        last_used_at=_aware(metadata_json["last_used_at"])
        if metadata_json.get("last_used_at")
        else None,
        metadata=nested_metadata,
    )


def _entity_mapping(row: Any) -> EntityMapping:
    return EntityMapping(
        mapping_id=str(row["mapping_id"]),
        project_id=row["project_id"],
        internal_entity_type=str(row["internal_entity_type"]),  # type: ignore[arg-type]
        internal_entity_id=str(row["internal_entity_id"]),
        external_ref=ExternalRecordRef.model_validate(row["external_ref_json"]),
        mapping_method=str(row["mapping_method"]),  # type: ignore[arg-type]
        mapping_confidence=int(row["mapping_confidence"]) / 1_000_000,
        status=str(row["status"]),  # type: ignore[arg-type]
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        created_by=row["created_by"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _sync_job(row: Any) -> SyncJob:
    return SyncJob(
        sync_job_id=str(row["sync_job_id"]),
        external_system_id=str(row["external_system_id"]),
        project_id=row["project_id"],
        direction=str(row["direction"]),  # type: ignore[arg-type]
        object_types=list(row["object_types_json"] or []),
        mode=str(row["mode"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        requested_by_user_id=row["requested_by_user_id"],
        started_at=_aware(row["started_at"]) if row["started_at"] else None,
        completed_at=_aware(row["completed_at"]) if row["completed_at"] else None,
        records_seen=int(row["records_seen"] or 0),
        records_imported=int(row["records_imported"] or 0),
        records_exported=int(row["records_exported"] or 0),
        records_skipped=int(row["records_skipped"] or 0),
        records_failed=int(row["records_failed"] or 0),
        artifact_ids=list(row["artifact_ids_json"] or []),
        warnings=list(row["warnings_json"] or []),
        error_summary=row["error_summary"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _sync_record(row: Any) -> SyncRecord:
    return SyncRecord(
        sync_record_id=str(row["sync_record_id"]),
        sync_job_id=str(row["sync_job_id"]),
        external_ref=ExternalRecordRef.model_validate(row["external_ref_json"]),
        internal_entity_type=row["internal_entity_type"],
        internal_entity_id=row["internal_entity_id"],
        action=str(row["action"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        validation_errors=list(row["validation_errors_json"] or []),
        warnings=list(row["warnings_json"] or []),
        raw_payload_artifact_id=row["raw_payload_artifact_id"],
        created_at=_aware(row["created_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _data_contract(row: Any) -> DataContract:
    return DataContract(
        contract_id=str(row["contract_id"]),
        name=str(row["name"]),
        object_type=str(row["object_type"]),
        version=str(row["version"]),
        required_fields=list(row["required_fields_json"] or []),
        optional_fields=list(row["optional_fields_json"] or []),
        field_types=dict(row["field_types_json"] or {}),
        controlled_vocabularies=dict(row["controlled_vocabularies_json"] or {}),
        identifier_fields=list(row["identifier_fields_json"] or []),
        validation_rules=list(row["validation_rules_json"] or []),
        metadata=dict(row["metadata_json"] or {}),
    )


def _sync_job_values(job: SyncJob, org_id: str) -> dict[str, Any]:
    return {
        "sync_job_id": job.sync_job_id,
        "org_id": org_id,
        "project_id": job.project_id,
        "external_system_id": job.external_system_id,
        "direction": job.direction,
        "object_types_json": job.object_types,
        "mode": job.mode,
        "status": job.status,
        "requested_by_user_id": job.requested_by_user_id,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "records_seen": job.records_seen,
        "records_imported": job.records_imported,
        "records_exported": job.records_exported,
        "records_skipped": job.records_skipped,
        "records_failed": job.records_failed,
        "artifact_ids_json": job.artifact_ids,
        "warnings_json": _redact_json(job.warnings),
        "error_summary": redact_secret_values(job.error_summary or "")
        if job.error_summary
        else None,
        "metadata_json": job.metadata,
    }


def _secret_ref_from_row(row: Any) -> str:
    if row["backend"] == "platform_hash":
        return f"external_secret_manager:platform_hash/{row['credential_id']}"
    return SecretRef(ref_type=row["backend"], reference=str(row["key_ref"] or "")).as_string()


def _public_row(row: Any) -> dict[str, Any]:
    return _redact_json(dict(row))


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secret_values(redact_secrets(value))
    return value


__all__ = [
    "IntegrationStore",
    "data_contracts",
    "entity_mappings",
    "external_systems",
    "integration_audit_events",
    "sync_jobs",
    "sync_records",
    "webhook_events",
]
