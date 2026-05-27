from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from molecule_ranker.server.dependencies import workspace_store
from molecule_ranker.workspace.schemas import ProjectWorkspace
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["projects"])


class ProjectCreateRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None


@router.get("/projects")
def list_projects(
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    if not store.workspace_path.exists():
        return {"projects": []}
    workspace = store.load()
    return {"projects": [_project_summary(workspace)]}


@router.post("/projects")
def create_project(
    request: ProjectCreateRequest,
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.create(workspace_id=request.workspace_id, name=request.name)
    return workspace.model_dump(mode="json")


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return workspace.model_dump(mode="json")


def _project_summary(workspace: ProjectWorkspace) -> dict[str, object]:
    return {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "root_dir": workspace.root_dir,
        "run_count": len(workspace.runs),
        "artifact_count": len(workspace.artifacts),
    }
