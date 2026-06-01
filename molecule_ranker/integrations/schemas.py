from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ExternalSystemType = Literal[
    "eln",
    "lims",
    "compound_registry",
    "assay_provider",
    "data_warehouse",
    "instrument_gateway",
    "generic_rest",
    "generic_file",
]
ExternalSystemMode = Literal["read_only", "dry_run", "write_enabled"]
CredentialType = Literal[
    "api_key",
    "bearer_token",
    "oauth_client",
    "basic_auth",
    "database_password",
    "service_account",
    "ssh_key_reference",
]
InternalEntityType = Literal[
    "candidate",
    "generated_molecule",
    "target",
    "disease",
    "assay_result",
    "review_item",
    "experiment",
    "project",
    "campaign",
    "campaign_work_package",
]
MappingMethod = Literal[
    "exact_id",
    "inchi_key",
    "canonical_smiles",
    "registry_id",
    "name_exact",
    "user_confirmed",
    "codex_suggested_pending_validation",
    "manual",
]
MappingStatus = Literal["active", "pending_review", "rejected", "stale"]
SyncDirection = Literal["import", "export", "bidirectional"]
SyncMode = Literal["read_only", "dry_run", "write_enabled"]
SyncJobStatus = Literal["queued", "running", "succeeded", "failed", "partial", "cancelled"]
SyncRecordAction = Literal[
    "imported",
    "exported",
    "skipped",
    "failed",
    "mapped",
    "unmapped",
    "updated",
]
SyncRecordStatus = Literal["succeeded", "failed", "skipped", "pending_review"]
HealthStatus = Literal["ok", "degraded", "unconfigured", "blocked"]

SECRET_CONFIG_KEYS = {
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


class IntegrationModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ExternalSystem(IntegrationModel):
    external_system_id: str
    name: str
    system_type: ExternalSystemType
    vendor: str | None = None
    base_url: str | None = None
    enabled: bool = True
    default_mode: ExternalSystemMode = "dry_run"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_secret_metadata(self) -> Self:
        _reject_secret_config(self.metadata)
        return self


class IntegrationCredential(IntegrationModel):
    credential_id: str
    external_system_id: str
    credential_type: CredentialType
    secret_ref: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_secret_reference_only(self) -> Self:
        lowered = self.secret_ref.lower()
        if any(lowered.startswith(prefix) for prefix in ("sk-", "ghp_", "akia")):
            raise ValueError("secret_ref must point to secure storage, not contain a secret value")
        _reject_secret_config(self.metadata)
        return self


class ExternalRecordRef(IntegrationModel):
    external_system_id: str
    external_record_type: str
    external_record_id: str
    external_url: str | None = None
    external_version: str | None = None
    retrieved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityMapping(IntegrationModel):
    mapping_id: str
    project_id: str | None = None
    internal_entity_type: InternalEntityType
    internal_entity_id: str
    external_ref: ExternalRecordRef
    mapping_method: MappingMethod
    mapping_confidence: float = Field(ge=0.0, le=1.0)
    status: MappingStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keep_codex_suggestions_pending(self) -> Self:
        if self.mapping_method == "codex_suggested_pending_validation" and self.status == "active":
            if self.metadata.get("deterministic_validation") is not True:
                raise ValueError("Codex-suggested mappings must remain pending until validated")
        return self


class SyncJob(IntegrationModel):
    sync_job_id: str
    external_system_id: str
    project_id: str | None = None
    direction: SyncDirection
    object_types: list[str] = Field(default_factory=list)
    mode: SyncMode = "dry_run"
    status: SyncJobStatus = "queued"
    requested_by_user_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    records_seen: int = Field(default=0, ge=0)
    records_imported: int = Field(default=0, ge=0)
    records_exported: int = Field(default=0, ge=0)
    records_skipped: int = Field(default=0, ge=0)
    records_failed: int = Field(default=0, ge=0)
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncRecord(IntegrationModel):
    sync_record_id: str
    sync_job_id: str
    external_ref: ExternalRecordRef
    internal_entity_type: str | None = None
    internal_entity_id: str | None = None
    action: SyncRecordAction
    status: SyncRecordStatus
    validation_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_payload_artifact_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationAuditEvent(IntegrationModel):
    event_id: str
    external_system_id: str | None = None
    sync_job_id: str | None = None
    actor_user_id: str | None = None
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    object_type: str
    object_id: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataContract(IntegrationModel):
    contract_id: str
    name: str
    object_type: str
    version: str
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    field_types: dict[str, str] = Field(default_factory=dict)
    controlled_vocabularies: dict[str, list[str]] = Field(default_factory=dict)
    identifier_fields: list[str] = Field(default_factory=list)
    validation_rules: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataContractIssue(IntegrationModel):
    row_index: int | None = None
    field: str | None = None
    issue: str


class DataContractValidationReport(IntegrationModel):
    contract_id: str
    contract_name: str
    valid: bool
    row_count: int
    issue_count: int
    issues: list[DataContractIssue] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntegrationCredentialRef(IntegrationModel):
    credential_id: str
    backend: Literal["env", "vault", "platform_hash"] = "platform_hash"
    key_ref: str | None = None
    configured: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntegrationCredentialCreate(IntegrationModel):
    name: str
    external_system_id: str | None = None
    connector_id: str | None = None
    secret_value: str | None = Field(default=None, exclude=True)
    secret_env_var: str | None = None
    vault_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_secret_reference_or_value(self) -> Self:
        configured = [self.secret_value, self.secret_env_var, self.vault_ref]
        if sum(bool(value) for value in configured) != 1:
            raise ValueError("Provide exactly one secret_value, secret_env_var, or vault_ref.")
        return self


class ConnectorConfig(IntegrationModel):
    connector_id: str = Field(default_factory=lambda: f"int-{uuid4().hex[:16]}")
    name: str
    provider: Literal[
        "benchling",
        "generic_rest",
        "generic_csv_sftp",
        "postgresql",
        "databricks_sql",
        "snowflake",
        "sila_metadata",
    ]
    kind: Literal[
        "eln_lims",
        "compound_registry",
        "assay_result_provider",
        "data_warehouse",
        "webhook",
        "generic_rest",
        "csv_sftp",
        "metadata_adapter",
    ]
    mode: Literal["read_only", "dry_run", "sandbox", "write_enabled"] = "dry_run"
    direction: SyncDirection = "import"
    base_url: str | None = None
    credential_ref: IntegrationCredentialRef | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    allow_writes: bool = False
    explicit_write_permission: bool = False
    sandbox: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_integration_safety(self) -> Self:
        _reject_secret_config(self.config)
        _reject_secret_config(self.metadata)
        if self.allow_writes or self.direction in {"export", "bidirectional"}:
            if self.mode != "write_enabled" or not self.explicit_write_permission:
                raise ValueError(
                    "External writes/exports require mode='write_enabled' and "
                    "explicit_write_permission=True."
                )
        if self.mode in {"dry_run", "sandbox", "read_only"} and self.allow_writes:
            raise ValueError("Read-only, dry-run, and sandbox connectors cannot allow writes.")
        if self.provider == "sila_metadata" and self.direction != "import":
            raise ValueError("SiLA metadata adapter is metadata-only; device control is blocked.")
        return self

    def as_external_system(self) -> ExternalSystem:
        system_type = _system_type_from_connector_kind(self.kind)
        vendor = _vendor_from_provider(self.provider)
        return ExternalSystem(
            external_system_id=self.connector_id,
            name=self.name,
            system_type=system_type,
            vendor=vendor,
            base_url=self.base_url,
            enabled=True,
            default_mode="dry_run" if self.mode == "sandbox" else self.mode,
            created_at=self.created_at,
            updated_at=self.updated_at,
            metadata=self.metadata,
        )


class IntegrationHealthStatus(IntegrationModel):
    connector_id: str
    provider: str
    status: HealthStatus
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str
    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


ConnectorHealth = IntegrationHealthStatus


class ExternalRecordProvenance(IntegrationModel):
    source_system: str
    source_record_id: str
    sync_job_id: str
    source_updated_at: datetime | None = None
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_source_identity(self) -> Self:
        if not self.source_system.strip() or not self.source_record_id.strip():
            raise ValueError("Imported records require source_system and source_record_id.")
        return self


class ExternalRecordEnvelope(IntegrationModel):
    record_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: ExternalRecordProvenance

    @property
    def external_ref(self) -> ExternalRecordRef:
        return ExternalRecordRef(
            external_system_id=self.provenance.source_system,
            external_record_type=self.record_type,
            external_record_id=self.provenance.source_record_id,
            retrieved_at=self.provenance.imported_at,
            metadata=self.provenance.raw_metadata,
        )


class ExternalIdMapping(IntegrationModel):
    mapping_id: str = Field(default_factory=lambda: f"map-{uuid4().hex[:16]}")
    connector_id: str
    internal_id: str
    external_id: str
    source_system: str
    source_record_id: str
    mapping_method: Literal["deterministic", "user_confirmed", "codex_suggested"] = "deterministic"
    status: Literal["suggested", "confirmed", "rejected"] = "suggested"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def codex_suggestions_must_remain_pending_or_confirmed_by_evidence(self) -> Self:
        if self.mapping_method == "codex_suggested" and self.status == "confirmed":
            if self.validation_evidence.get("deterministic_match") is not True:
                raise ValueError("Codex-suggested mappings need deterministic validation.")
        return self


class MappingSuggestionRequest(IntegrationModel):
    connector_id: str
    source_system: str
    suggestions: list[ExternalIdMapping] = Field(default_factory=list)
    observed_records: list[ExternalRecordEnvelope] = Field(default_factory=list)


class MappingSuggestionReport(IntegrationModel):
    accepted: list[ExternalIdMapping] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SyncJobRecord(IntegrationModel):
    sync_job_id: str = Field(default_factory=lambda: f"sync-{uuid4().hex[:16]}")
    connector_id: str
    org_id: str = "default"
    project_id: str | None = None
    direction: SyncDirection = "import"
    mode: Literal["read_only", "dry_run", "sandbox", "write_enabled"] = "dry_run"
    status: Literal["planned", "running", "succeeded", "failed", "dry_run", "blocked"] = "planned"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rows_seen: int = Field(default=0, ge=0)
    rows_valid: int = Field(default=0, ge=0)
    rows_rejected: int = Field(default=0, ge=0)
    contract_report: DataContractValidationReport | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebhookIngestRequest(IntegrationModel):
    source_system: str
    event_type: str
    source_record_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    source_updated_at: datetime | None = None


def _system_type_from_connector_kind(kind: str) -> ExternalSystemType:
    if kind == "eln_lims":
        return "eln"
    if kind == "compound_registry":
        return "compound_registry"
    if kind == "assay_result_provider":
        return "assay_provider"
    if kind == "data_warehouse":
        return "data_warehouse"
    if kind == "generic_rest":
        return "generic_rest"
    if kind == "csv_sftp":
        return "generic_file"
    return "generic_rest"


def _vendor_from_provider(provider: str) -> str:
    return {
        "generic_csv_sftp": "generic",
        "databricks_sql": "databricks",
        "sila_metadata": "generic",
    }.get(provider, provider)


def _reject_secret_config(value: dict[str, Any], prefix: str = "") -> None:
    for key, raw in value.items():
        normalized = str(key).lower().replace("-", "_")
        path = f"{prefix}.{key}" if prefix else str(key)
        if normalized in SECRET_CONFIG_KEYS or any(
            marker in normalized for marker in SECRET_CONFIG_KEYS
        ):
            raise ValueError(f"Secret-like integration config key is not allowed: {path}")
        if isinstance(raw, dict):
            _reject_secret_config(raw, path)
