from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from molecule_ranker.codex_backbone.guardrails import is_secret_path
from molecule_ranker.platform.isolation import (
    IsolationViolation,
    require_workspace_artifact_access,
)
from molecule_ranker.platform.rbac import has_permission, require_project_access
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, workspace_store
from molecule_ranker.server.security import reject_suspicious_identifier, safe_artifact_path
from molecule_ranker.utils.pagination import normalize_limit_offset
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["artifacts"])

CACHE_MARKERS = (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")


@router.get("/projects/{project_id}/artifacts")
def get_project_artifacts(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
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
    all_artifacts = [
        artifact
        for artifact in store.artifact_manifest(workspace)
        if _safe_artifact_path(Path(str(artifact["path"])))
    ]
    page = normalize_limit_offset(limit=limit, offset=offset, max_limit=500)
    artifacts = all_artifacts[page.offset : page.offset + page.limit]
    page = page.__class__(limit=page.limit, offset=page.offset, count=len(artifacts))
    return {
        "workspace_id": workspace.workspace_id,
        "artifacts": artifacts,
        "pagination": page.model_dump(),
    }


@router.get("/projects/{project_id}/artifacts/{artifact_id}/download")
def download_project_artifact(
    project_id: str,
    artifact_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> FileResponse:
    reject_suspicious_identifier(artifact_id, label="artifact ID")
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    if bool(request.app.state.hosted_mode):
        database = request.app.state.platform_database
        require_project_access(
            database,
            user,
            project_id=project_id,
            action="read",
        )
    artifact = next((item for item in workspace.artifacts if item.artifact_id == artifact_id), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if bool(request.app.state.hosted_mode):
        database = request.app.state.platform_database
        try:
            require_workspace_artifact_access(
                database,
                user,
                workspace=workspace,
                artifact=artifact,
                permission="project:read",
            )
        except IsolationViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if _is_pose_artifact(artifact.artifact_type) and not has_permission(
            user,
            "structure:export",
            project_id=project_id,
            database=database,
        ):
            raise HTTPException(status_code=403, detail="Missing permission structure:export.")
    path = safe_artifact_path(Path(artifact.path), root_dir=store.root_dir)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
        headers={"X-Artifact-ID": artifact.artifact_id},
    )


def _safe_artifact_path(path: Path) -> bool:
    lowered = str(path).lower()
    if any(marker in lowered for marker in CACHE_MARKERS):
        return False
    return not is_secret_path(path)


def _is_pose_artifact(artifact_type: str) -> bool:
    normalized = artifact_type.lower().replace("-", "_")
    return normalized in {"docking_pose", "pose_file", "structure_pose"} or (
        "pose" in normalized and "docking" in normalized
    )
