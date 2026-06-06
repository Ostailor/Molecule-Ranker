from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from molecule_ranker.e2e.hosted import (
    HostedE2EWorkflowCreateRequest,
    HostedE2EWorkflowStore,
    hosted_e2e_record_payload,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, platform_database

router = APIRouter(prefix="/e2e", tags=["e2e"])


@router.post("/workflows")
def create_e2e_workflow(
    request_body: HostedE2EWorkflowCreateRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:run")
    record = _store(request).create(
        request_body.model_copy(update={"requested_by": request_body.requested_by or user.email})
    )
    return {"workflow": hosted_e2e_record_payload(record)}


@router.get("/workflows")
def list_e2e_workflows(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:read")
    return {
        "workflows": [
            hosted_e2e_record_payload(record)
            for record in _store(request).list()
        ]
    }


@router.get("/workflows/{workflow_id}")
def get_e2e_workflow(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:read")
    return {"workflow": hosted_e2e_record_payload(_record_or_404(request, workflow_id))}


@router.post("/workflows/{workflow_id}/resume")
def resume_e2e_workflow(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:resume")
    try:
        record = _store(request).resume(workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found.") from exc
    return {"workflow": hosted_e2e_record_payload(record)}


@router.post("/workflows/{workflow_id}/cancel")
def cancel_e2e_workflow(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:cancel")
    try:
        record = _store(request).cancel(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found.") from exc
    return {"workflow": hosted_e2e_record_payload(record)}


@router.get("/workflows/{workflow_id}/lineage")
def get_e2e_lineage(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:read")
    record = _record_or_404(request, workflow_id)
    return {
        "lineage": [
            lineage.model_dump(mode="json")
            for lineage in record.result.lineage_records
        ]
    }


@router.get("/workflows/{workflow_id}/bundle")
def get_e2e_bundle(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:export")
    record = _record_or_404(request, workflow_id)
    if record.result.bundle is None:
        raise HTTPException(status_code=404, detail="Result bundle not available.")
    return {"bundle": record.result.bundle.model_dump(mode="json")}


@router.post("/workflows/{workflow_id}/validate")
def validate_e2e_workflow(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_e2e_permission(user, database, "e2e:run")
    try:
        record = _store(request).validate(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found.") from exc
    return {"validation": record.validation.model_dump(mode="json")}


def _store(request: Request) -> HostedE2EWorkflowStore:
    store = getattr(request.app.state, "e2e_workflow_store", None)
    if store is None:
        store = HostedE2EWorkflowStore()
        request.app.state.e2e_workflow_store = store
    return store


def _record_or_404(request: Request, workflow_id: str):
    try:
        return _store(request).require(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found.") from exc


def _require_e2e_permission(
    user: UserAccount,
    database: PlatformDatabase,
    permission: str,
) -> None:
    metadata_permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in metadata_permissions
        or "e2e:admin" in metadata_permissions
        or permission in metadata_permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"{permission} permission denied.")


__all__ = ["router"]
