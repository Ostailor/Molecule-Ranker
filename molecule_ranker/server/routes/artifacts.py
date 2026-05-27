from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from molecule_ranker.codex_backbone.guardrails import is_secret_path
from molecule_ranker.server.dependencies import workspace_store
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["artifacts"])

CACHE_MARKERS = (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")


@router.get("/projects/{project_id}/artifacts")
def get_project_artifacts(
    project_id: str,
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    artifacts = [
        artifact
        for artifact in store.artifact_manifest(workspace)
        if _safe_artifact_path(Path(str(artifact["path"])))
    ]
    return {"workspace_id": workspace.workspace_id, "artifacts": artifacts}


def _safe_artifact_path(path: Path) -> bool:
    lowered = str(path).lower()
    if any(marker in lowered for marker in CACHE_MARKERS):
        return False
    return not is_secret_path(path)
