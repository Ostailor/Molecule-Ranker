from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from molecule_ranker.codex_backbone.artifact_context import select_relevant_artifacts
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.platform.codex_worker import CodexWorker
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.rbac import require_project_access
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import (
    codex_provider,
    current_user,
    platform_database,
    workspace_store,
)
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["codex"])


class ExplainRunRequest(BaseModel):
    candidate: str | None = None


@router.post("/projects/{project_id}/codex/summarize")
def summarize_project(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    workspace = store.load_or_create()
    if workspace.workspace_id != project_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    if bool(request.app.state.hosted_mode):
        database = request.app.state.platform_database
        require_project_access(database, user, project_id=project_id, action="run_codex")
        job = PlatformJobQueue(database).enqueue(
            job_type="codex_task",
            requested_by=user,
            project_id=project_id,
            config_snapshot={"task_type": "summarize_project"},
        )
        return {"status": "queued", "job": job.model_dump(mode="json")}
    provider = codex_provider(request)
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
    http_request: Request,
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    if bool(http_request.app.state.hosted_mode):
        raise HTTPException(
            status_code=403,
            detail="Hosted mode runs Codex work through queued project Codex jobs.",
        )
    provider = codex_provider(http_request)
    workspace = store.load_or_create()
    run = next((item for item in workspace.runs if item.run_id == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    task = _explain_candidate_task(run_id, Path(run.run_dir), request.candidate)
    result = provider.run_task(task)
    return {"status": result.status, "result": _result(result)}


@router.post("/codex/run-task")
def run_codex_task(
    _task: CodexTask,
    request: Request,
) -> dict[str, object]:
    database = getattr(request.app.state, "platform_database", None)
    if database is not None:
        database.write_audit(
            "codex_arbitrary_task_blocked",
            actor_user_id=getattr(getattr(request.state, "user", None), "user_id", None),
            summary="Blocked arbitrary API-triggered Codex task.",
            object_type="codex_task",
            object_id="arbitrary_api_task",
            metadata={"path": request.url.path},
        )
    raise HTTPException(
        status_code=403,
        detail=(
            "Arbitrary API-triggered Codex tasks are disabled. Use guarded project job "
            "endpoints handled by CodexWorker."
        ),
    )


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, object]:
    job = database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.project_id:
        require_project_access(database, user, project_id=job.project_id, action="read")
    elif "platform_admin" not in user.roles and job.requested_by_user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Job permission denied.")
    return job.model_dump(mode="json")


@router.post("/jobs/run-next")
def run_next_job(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> dict[str, object]:
    if "platform_admin" not in user.roles:
        raise HTTPException(status_code=403, detail="Admin role required.")
    worker = CodexWorker(
        database=database,
        workspace_store=store,
        codex_config=request.app.state.codex_config,
        provider=request.app.state.codex_provider,
    )
    job = worker.run_next()
    return {"job": job.model_dump(mode="json") if job else None}


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
