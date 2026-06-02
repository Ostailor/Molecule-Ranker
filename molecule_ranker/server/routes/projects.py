from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from molecule_ranker.platform.rbac import can_access_project, require_project_access
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, workspace_store
from molecule_ranker.utils.pagination import normalize_limit_offset
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
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filter: str | None = Query(default=None),
    sort: str = Query(default="name"),
) -> dict[str, object]:
    if not store.workspace_path.exists():
        page = normalize_limit_offset(limit=limit, offset=offset, max_limit=500)
        return {"projects": [], "pagination": page.model_dump()}
    workspace = store.load()
    if bool(request.app.state.hosted_mode) and not can_access_project(
        request.app.state.platform_database,
        user,
        project_id=workspace.workspace_id,
        action="read",
    ):
        page = normalize_limit_offset(limit=limit, offset=offset, max_limit=500)
        return {"projects": [], "pagination": page.model_dump()}
    projects = [_project_summary(workspace)]
    if filter:
        needle = filter.lower()
        projects = [
            project
            for project in projects
            if needle in str(project["workspace_id"]).lower()
            or needle in str(project["name"]).lower()
        ]
    reverse = sort.startswith("-")
    sort_key = sort.removeprefix("-")
    if sort_key in {"workspace_id", "name", "run_count", "artifact_count"}:
        projects = sorted(
            projects,
            key=lambda item: _project_sort_value(item, sort_key),
            reverse=reverse,
        )
    page = normalize_limit_offset(limit=limit, offset=offset, max_limit=500)
    page_projects = projects[page.offset : page.offset + page.limit]
    page = page.__class__(limit=page.limit, offset=page.offset, count=len(page_projects))
    return {"projects": page_projects, "pagination": page.model_dump()}


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


def _project_sort_value(project: dict[str, object], key: str) -> str | int:
    value = project[key]
    return value if isinstance(value, int) else str(value)
