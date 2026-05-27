from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from molecule_ranker.platform.rbac import can_access_project, require_project_access
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, workspace_store
from molecule_ranker.workspace.schemas import ProjectWorkspace
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["projects"])


class ProjectCreateRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None


@router.get("/projects")
def list_projects(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    if not store.workspace_path.exists():
        return {"projects": []}
    workspace = store.load()
    if bool(request.app.state.hosted_mode) and not can_access_project(
        request.app.state.platform_database,
        user,
        project_id=workspace.workspace_id,
        action="read",
    ):
        return {"projects": []}
    return {"projects": [_project_summary(workspace)]}


@router.post("/projects")
def create_project(
    request: ProjectCreateRequest,
    http_request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    existed = store.workspace_path.exists()
    if bool(http_request.app.state.hosted_mode) and existed:
        existing = store.load()
        require_project_access(
            http_request.app.state.platform_database,
            user,
            project_id=existing.workspace_id,
            action="admin",
        )
    workspace = store.create(workspace_id=request.workspace_id, name=request.name)
    if bool(http_request.app.state.hosted_mode) and not existed:
        http_request.app.state.platform_database.grant_project_permission(
            project_id=workspace.workspace_id,
            role="owner",
            actor_user_id=user.user_id,
            user_id=user.user_id,
        )
    return workspace.model_dump(mode="json")


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    if bool(request.app.state.hosted_mode):
        require_project_access(
            request.app.state.platform_database,
            user,
            project_id=project_id,
            action="read",
        )
    return workspace.model_dump(mode="json")


def _project_summary(workspace: ProjectWorkspace) -> dict[str, object]:
    return {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "root_dir": workspace.root_dir,
        "run_count": len(workspace.runs),
        "artifact_count": len(workspace.artifacts),
    }
