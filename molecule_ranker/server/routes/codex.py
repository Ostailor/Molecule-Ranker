from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from molecule_ranker.codex_backbone.artifact_context import select_relevant_artifacts
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.server.dependencies import codex_provider, workspace_store
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["codex"])


class ExplainRunRequest(BaseModel):
    candidate: str | None = None


@router.post("/projects/{project_id}/codex/summarize")
def summarize_project(
    project_id: str,
    provider: Annotated[Any, Depends(codex_provider)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    _workspace, result, output_path = store.run_codex_project_task(
        "summarize_project",
        config=store_config(store),
        provider=provider,
    )
    return {"status": result.status, "output_path": str(output_path), "result": _result(result)}


@router.post("/runs/{run_id}/codex/explain")
def explain_run_candidate(
    run_id: str,
    request: ExplainRunRequest,
    provider: Annotated[Any, Depends(codex_provider)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    run = next((item for item in workspace.runs if item.run_id == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    task = _explain_candidate_task(run_id, Path(run.run_dir), request.candidate)
    result = provider.run_task(task)
    return {"status": result.status, "result": _result(result)}


@router.post("/codex/run-task")
def run_codex_task(
    task: CodexTask,
    provider: Annotated[Any, Depends(codex_provider)],
) -> dict[str, object]:
    result = provider.run_task(task)
    return {"status": result.status, "result": _result(result)}


def store_config(store: ProjectWorkspaceStore) -> Any:
    from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig

    return CodexBackboneConfig(
        enable_codex_backbone=True,
        codex_working_dir=store.root_dir,
        codex_dry_run=False,
    )


def _explain_candidate_task(
    run_id: str,
    run_dir: Path,
    candidate: str | None,
) -> CodexTask:
    artifacts = select_relevant_artifacts("explain_ranking", run_dir)
    prompt = {
        "task": "Explain a candidate ranking using existing artifacts only.",
        "run_id": run_id,
        "candidate": candidate,
        "constraints": [
            "Use only selected run artifacts.",
            "Do not fabricate biomedical evidence, molecules, citations, assay results, or scores.",
            "Do not provide medical advice, dosing, synthesis routes, or lab protocols.",
        ],
    }
    return CodexTask(
        task_id=f"api-explain-{run_id}",
        task_type="explain_ranking",
        prompt=json.dumps(prompt, indent=2, sort_keys=True),
        working_directory=str(run_dir.resolve()),
        input_artifact_paths=[str(path.resolve()) for path in artifacts],
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=300,
        require_json=True,
        metadata={"run_id": run_id, "candidate": candidate},
    )


def _result(result: CodexTaskResult) -> dict[str, object]:
    return result.model_dump(mode="json")


def disabled_result(task: CodexTask) -> CodexTaskResult:
    now = datetime.now(UTC)
    return CodexTaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="disabled",
        stderr="Codex backbone is disabled.",
        started_at=now,
        completed_at=now,
    )
