from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from molecule_ranker.integrations import (
    ConnectorConfig,
    DataContract,
    ExternalRecordEnvelope,
    ExternalRecordProvenance,
    ExternalSystem,
    IntegrationCredentialCreate,
    MappingSuggestionRequest,
    WebhookIngestRequest,
    connector_catalog,
    create_connector,
    validate_mapping_suggestions,
)
from molecule_ranker.integrations.dashboard import render_integration_dashboard
from molecule_ranker.integrations.operations import build_integration_operations_dashboard
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.sync import SyncRequest
from molecule_ranker.integrations.worker import enqueue_integration_sync_job
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.isolation import (
    IsolationViolation,
    require_connector_access,
)
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, platform_database

router = APIRouter(tags=["integrations"])


class ConnectorCreateRequest(BaseModel):
    connector: ConnectorConfig
    org_id: str = "default"
    project_id: str | None = None


class SyncPreviewRequest(BaseModel):
    contract: DataContract
    records: list[ExternalRecordEnvelope] = Field(default_factory=list)
    dry_run: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncEnqueueRequest(BaseModel):
    sync_request: SyncRequest = Field(default_factory=SyncRequest)
    job_type: str = "integration_sync"
    priority: str = "normal"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalSystemCreateRequest(BaseModel):
    system: ExternalSystem
    org_id: str = "default"
    project_id: str | None = None


class ExternalSystemPatchRequest(BaseModel):
    name: str | None = None
    system_type: str | None = None
    vendor: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    default_mode: str | None = None
    metadata: dict[str, Any] | None = None


class SystemSyncRequest(BaseModel):
    sync_request: SyncRequest = Field(default_factory=SyncRequest)
    priority: str = "normal"
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/integrations/catalog")
def integrations_catalog() -> dict[str, Any]:
    return {
        "connectors": connector_catalog(),
        "default_mode": "dry_run",
        "write_policy": "writes require write_enabled mode and explicit_write_permission",
    }


@router.get("/integrations/systems")
def list_external_systems(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    org_id: str = "default",
    project_id: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read", org_id=org_id, project_id=project_id)
    systems = IntegrationStore(
        database,
        user=user,
        org_id=org_id,
        project_id=project_id,
    ).list_external_systems(org_id=org_id, project_id=project_id, enabled=enabled)
    return {"systems": [system.model_dump(mode="json") for system in systems]}


@router.post("/integrations/systems")
def create_external_system(
    request: ExternalSystemCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_permission(
        user,
        database,
        "integration:write",
        org_id=request.org_id,
        project_id=request.project_id,
    )
    system = IntegrationStore(
        database,
        user=user,
        org_id=request.org_id,
        project_id=request.project_id,
    ).create_external_system(request.system, org_id=request.org_id, project_id=request.project_id)
    return {"system": system.model_dump(mode="json")}


@router.get("/integrations/systems/{external_system_id}")
def get_external_system(
    external_system_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read")
    store = IntegrationStore(database, user=user)
    system = store.get_external_system(external_system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="External system not found.")
    return {"system": system.model_dump(mode="json")}


@router.patch("/integrations/systems/{external_system_id}")
def update_external_system(
    external_system_id: str,
    request: ExternalSystemPatchRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_permission(user, database, "integration:write")
    updates = request.model_dump(exclude_none=True)
    try:
        system = IntegrationStore(database, user=user).update_external_system(
            external_system_id,
            **updates,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="External system not found.") from exc
    return {"system": system.model_dump(mode="json")}


@router.post("/integrations/systems/{external_system_id}/health")
def check_external_system_health(
    external_system_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read")
    connector = _connector_for_system(database, external_system_id)
    health = create_connector(connector).health_check()
    return {"health": health.model_dump(mode="json")}


@router.post("/integrations/systems/{external_system_id}/sync")
def enqueue_external_system_sync(
    external_system_id: str,
    request: SystemSyncRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector = _connector_for_system(database, external_system_id)
    sync_request = request.sync_request.model_copy(
        update={
            "org_id": request.sync_request.org_id,
            "project_id": request.sync_request.project_id,
            "requested_by_user_id": user.user_id,
        }
    )
    _require_permission(
        user,
        database,
        "integration:sync",
        org_id=sync_request.org_id,
        project_id=sync_request.project_id,
    )
    job = enqueue_integration_sync_job(
        database=database,
        connector=connector,
        request=sync_request,
        requested_by=user,
        metadata=request.metadata,
        priority=request.priority,
    )
    return {"job": job.model_dump(mode="json")}


@router.get("/integrations/sync-jobs")
def list_sync_jobs(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    org_id: str = "default",
    project_id: str | None = None,
    external_system_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read", org_id=org_id, project_id=project_id)
    store = IntegrationStore(database, user=user, org_id=org_id, project_id=project_id)
    jobs = store.list_sync_jobs(
        org_id=org_id,
        project_id=project_id,
        external_system_id=external_system_id,
        status=status,
        limit=limit,
    )
    return {"sync_jobs": [job.model_dump(mode="json") for job in jobs]}


@router.get("/integrations/sync-jobs/{sync_job_id}")
def get_sync_job(
    sync_job_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read")
    store = IntegrationStore(database, user=user)
    job = store.get_sync_job(sync_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sync job not found.")
    records = store.list_sync_records(sync_job_id=sync_job_id)
    return {
        "sync_job": job.model_dump(mode="json"),
        "records": [record.model_dump(mode="json") for record in records],
    }


@router.get("/integrations/mappings")
def list_mappings(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    org_id: str = "default",
    project_id: str | None = None,
    status: str | None = None,
    external_system_id: str | None = None,
) -> dict[str, Any]:
    _require_permission(user, database, "integration:read", org_id=org_id, project_id=project_id)
    mappings = IntegrationStore(
        database,
        user=user,
        org_id=org_id,
        project_id=project_id,
    ).find_mappings(
        org_id=org_id,
        project_id=project_id,
        status=status,
        external_system_id=external_system_id,
    )
    return {"mappings": [mapping.model_dump(mode="json") for mapping in mappings]}


@router.post("/integrations/mappings/{mapping_id}/approve")
def approve_mapping(
    mapping_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    return _mapping_decision(database, user, mapping_id, status="active")


@router.post("/integrations/mappings/{mapping_id}/reject")
def reject_mapping(
    mapping_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    return _mapping_decision(database, user, mapping_id, status="rejected")


@router.post("/integrations/credentials")
def create_integration_credential(
    request: IntegrationCredentialCreate,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if request.connector_id:
        _require_connector_namespace(
            user,
            database,
            request.connector_id,
            permission="integration:manage_credentials",
        )
    else:
        _require_permission(user, database, "integration:manage_credentials")
    credential = database.create_integration_credential(request, actor_user_id=user.user_id)
    return {"credential": credential.model_dump(mode="json")}


@router.post("/integrations/connectors")
def create_integration_connector(
    request: ConnectorCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if not user.is_admin and not has_permission(
        user,
        "integration:write",
        org_id=request.org_id,
        project_id=request.project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Integration management permission denied.")
    connector = database.create_integration_connector(
        request.connector,
        actor_user_id=user.user_id,
        org_id=request.org_id,
        project_id=request.project_id,
    )
    return {"connector": _public_connector(connector)}


@router.get("/integrations/connectors")
def list_integration_connectors(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    org_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    if not user.is_admin and not has_permission(
        user,
        "integration:read",
        org_id=org_id,
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Integration read permission denied.")
    return {
        "connectors": [
            _public_connector(connector)
            for connector in database.list_integration_connectors(
                org_id=org_id,
                project_id=project_id,
            )
        ]
    }


@router.get("/integrations/connectors/{connector_id}/health")
def integration_connector_health(
    connector_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector_config = _connector_or_404(database, connector_id)
    _require_connector_namespace(user, database, connector_id, permission="integration:read")
    health = create_connector(connector_config).health_check()
    return {"health": health.model_dump(mode="json")}


@router.post("/integrations/connectors/{connector_id}/sync/preview")
def integration_sync_preview(
    connector_id: str,
    request: SyncPreviewRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector_config = _connector_or_404(database, connector_id)
    _require_connector_namespace(user, database, connector_id, permission="integration:sync")
    mode = "dry_run" if request.dry_run else connector_config.mode
    if mode == "write_enabled":
        raise HTTPException(status_code=403, detail="Preview cannot run write-enabled sync.")
    sync_job = database.start_integration_sync_job(
        connector_id=connector_id,
        actor_user_id=user.user_id,
        direction=connector_config.direction,
        mode=mode,
        metadata=request.metadata,
    )
    report = create_connector(connector_config).preview_import(
        rows=request.records,
        contract=request.contract,
    )
    completed = sync_job.model_copy(
        update={
            "status": "dry_run",
            "rows_seen": report.row_count,
            "rows_valid": report.row_count - report.issue_count,
            "rows_rejected": report.issue_count,
            "contract_report": report,
        }
    )
    completed = database.complete_integration_sync_job(completed, records=request.records)
    return {
        "sync_job": completed.model_dump(mode="json"),
        "contract_report": report.model_dump(mode="json"),
    }


@router.post("/integrations/connectors/{connector_id}/sync/jobs")
def enqueue_integration_sync(
    connector_id: str,
    request: SyncEnqueueRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector_config = _connector_or_404(database, connector_id)
    connector_namespace = _require_connector_namespace(
        user,
        database,
        connector_id,
        permission="integration:sync",
    )
    sync_request = request.sync_request.model_copy(
        update={
            "org_id": connector_namespace.org_id or request.sync_request.org_id,
            "project_id": connector_namespace.project_id,
            "requested_by_user_id": user.user_id,
        }
    )
    _require_integration_sync(
        user,
        database,
        org_id=sync_request.org_id,
        project_id=sync_request.project_id,
    )
    job = enqueue_integration_sync_job(
        database=database,
        connector=connector_config,
        request=sync_request,
        requested_by=user,
        job_type=request.job_type,
        priority=request.priority,
        metadata=request.metadata,
    )
    return {"job": job.model_dump(mode="json")}


@router.post("/integrations/connectors/{connector_id}/webhooks/ingest")
def integration_webhook_ingest(
    connector_id: str,
    request: WebhookIngestRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    connector_config = _connector_or_404(database, connector_id)
    connector_namespace = _require_connector_namespace(
        user,
        database,
        connector_id,
        permission="integration:sync",
    )
    sync_job = database.start_integration_sync_job(
        connector_id=connector_id,
        actor_user_id=user.user_id,
        direction="import",
        mode="dry_run" if connector_config.mode != "read_only" else "read_only",
        org_id=connector_namespace.org_id or "default",
        project_id=connector_namespace.project_id,
        metadata={"event_type": request.event_type},
    )
    record = ExternalRecordEnvelope(
        record_type=request.event_type,
        payload={
            **request.payload,
            "source_system": request.source_system,
            "source_record_id": request.source_record_id,
            "sync_job_id": sync_job.sync_job_id,
        },
        provenance=ExternalRecordProvenance(
            source_system=request.source_system,
            source_record_id=request.source_record_id,
            sync_job_id=sync_job.sync_job_id,
            source_updated_at=request.source_updated_at,
            raw_metadata=request.raw_metadata,
        ),
    )
    completed = sync_job.model_copy(
        update={
            "status": "dry_run",
            "rows_seen": 1,
            "rows_valid": 1,
            "rows_rejected": 0,
        }
    )
    completed = database.complete_integration_sync_job(completed, records=[record])
    return {"sync_job": completed.model_dump(mode="json"), "accepted": True}


@router.post("/integrations/mappings/suggest")
def integration_mapping_suggestions(
    request: MappingSuggestionRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_integration_sync(user, database)
    report = validate_mapping_suggestions(request)
    database.save_external_id_mappings(report.accepted)
    return {"report": report.model_dump(mode="json")}


@router.get("/integrations/dashboard", response_class=Response)
def integration_dashboard(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_read(user, database)
    return Response(content=render_integration_dashboard(database.integration_dashboard_summary()))


@router.get("/integrations/operations/dashboard")
def integration_operations_dashboard(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_integration_read(user, database)
    return {"dashboard": build_integration_operations_dashboard()}


def _connector_or_404(database: PlatformDatabase, connector_id: str) -> ConnectorConfig:
    connector = database.get_integration_connector(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Integration connector not found.")
    return connector


def _connector_for_system(database: PlatformDatabase, external_system_id: str) -> ConnectorConfig:
    connector = database.get_integration_connector(external_system_id)
    if connector is not None:
        return connector
    system = IntegrationStore(database).get_external_system(external_system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="External system not found.")
    return _system_connector_config(system)


def _system_connector_config(system: ExternalSystem) -> ConnectorConfig:
    provider = "generic_rest"
    kind = "generic_rest"
    if system.vendor == "benchling":
        provider = "benchling"
        kind = "eln_lims"
    elif system.vendor == "databricks":
        provider = "databricks_sql"
        kind = "data_warehouse"
    elif system.vendor == "snowflake":
        provider = "snowflake"
        kind = "data_warehouse"
    elif system.system_type == "generic_file":
        provider = "generic_csv_sftp"
        kind = "csv_sftp"
    elif system.system_type == "data_warehouse":
        provider = "postgresql"
        kind = "data_warehouse"
    elif system.system_type in {"eln", "lims"}:
        kind = "eln_lims"
    elif system.system_type == "compound_registry":
        kind = "compound_registry"
    elif system.system_type == "assay_provider":
        kind = "assay_result_provider"
    return ConnectorConfig(
        connector_id=system.external_system_id,
        name=system.name,
        provider=provider,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        mode=system.default_mode,
        base_url=system.base_url,
        config=system.metadata,
        sandbox=system.default_mode != "write_enabled",
    )


def _require_connector_namespace(
    user: UserAccount,
    database: PlatformDatabase,
    connector_id: str,
    *,
    permission: str,
):
    try:
        return require_connector_access(
            database,
            user,
            connector_id=connector_id,
            permission=permission,
        )
    except IsolationViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _public_connector(connector: ConnectorConfig) -> dict[str, Any]:
    return connector.model_dump(mode="json", exclude={"credential_ref"})


def _mapping_decision(
    database: PlatformDatabase,
    user: UserAccount,
    mapping_id: str,
    *,
    status: str,
) -> dict[str, Any]:
    _require_permission(user, database, "integration:approve_mapping")
    store = IntegrationStore(database, user=user)
    current = store.get_mapping(mapping_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Mapping not found.")
    if (
        status == "active"
        and current.mapping_method == "codex_suggested_pending_validation"
        and current.metadata.get("deterministic_validation") is not True
    ):
        raise HTTPException(
            status_code=409,
            detail="Codex-suggested mappings need deterministic validation before approval.",
        )
    mapping = store.update_mapping_status(mapping_id, status=status)
    return {"mapping": mapping.model_dump(mode="json")}


def _require_permission(
    user: UserAccount,
    database: PlatformDatabase,
    permission: str,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
) -> None:
    if user.is_admin:
        return
    if not has_permission(
        user,
        permission,
        org_id=org_id,
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail=f"{permission} permission denied.")


def _require_integration_read(user: UserAccount, database: PlatformDatabase) -> None:
    if user.is_admin:
        return
    if not has_permission(user, "integration:read", database=database):
        raise HTTPException(status_code=403, detail="Integration read permission denied.")


def _require_integration_sync(
    user: UserAccount,
    database: PlatformDatabase,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
) -> None:
    if user.is_admin:
        return
    if not has_permission(
        user,
        "integration:sync",
        org_id=org_id,
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Integration sync permission denied.")
