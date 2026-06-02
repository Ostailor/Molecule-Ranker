from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import select

from molecule_ranker import __version__
from molecule_ranker.campaigns import Campaign, CampaignStore
from molecule_ranker.codex.provider import CodexCLIProvider, CodexRequest, CodexResponse
from molecule_ranker.codex_backbone.schemas import CodexTask
from molecule_ranker.platform import CodexWorker, PlatformDatabase
from molecule_ranker.platform.database import artifact_records, platform_jobs
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


class FakeCodexProvider:
    def __init__(self, stdout: str | None = None) -> None:
        self.requests: list[CodexRequest] = []
        self.stdout = stdout or json.dumps(
            {"status": "ok", "summary": "ok", "limitations": []},
            sort_keys=True,
        )

    def invoke(self, request: CodexRequest) -> CodexResponse:
        self.requests.append(request)
        now = datetime.now(UTC)
        return CodexResponse(
            request_id="fake-request",
            status="ok",
            stdout=self.stdout,
            stderr="",
            returncode=0,
            parsed_json=json.loads(self.stdout),
            started_at=now,
            completed_at=now,
        )


def test_version_is_v13() -> None:
    assert __version__ == "1.9.0"


def test_hosted_auth_rbac_project_sharing_and_codex_queue(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    project = client.post(
        "/projects",
        json={"workspace_id": "workspace-a", "name": "Research"},
        headers=admin_headers,
    )
    assert project.status_code == 200, project.text

    created = client.post(
        "/admin/users",
        json={
            "email": "scientist@example.com",
            "password": "Scientist-password-1",
            "roles": ["user"],
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["user"]["user_id"]
    user_headers = _login(client, "scientist@example.com", "Scientist-password-1")

    forbidden = client.get("/projects/workspace-a", headers=user_headers)
    assert forbidden.status_code == 403
    self_grant = client.post(
        "/projects",
        json={"workspace_id": "workspace-a", "name": "Research"},
        headers=user_headers,
    )
    assert self_grant.status_code == 403

    shared = client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": user_id},
        headers=admin_headers,
    )
    assert shared.status_code == 200, shared.text
    allowed = client.get("/projects/workspace-a", headers=user_headers)
    assert allowed.status_code == 200
    codex_forbidden = client.post("/projects/workspace-a/codex/summarize", headers=user_headers)
    assert codex_forbidden.status_code == 403

    owner = client.post(
        "/projects/workspace-a/share",
        json={"role": "project_owner", "user_id": user_id},
        headers=admin_headers,
    )
    assert owner.status_code == 200, owner.text
    queued = client.post("/projects/workspace-a/codex/summarize", headers=user_headers)
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "queued"
    assert queued.json()["job"]["job_type"] == "codex_task"


def test_hosted_design_jobs_enforce_permission_approval_and_budget(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    unauthorized = client.post(
        "/projects/workspace-a/design/jobs",
        json={"job_type": "design_generate", "budget": 10},
        headers=viewer_headers,
    )
    assert unauthorized.status_code == 403

    needs_approval = client.post(
        "/projects/workspace-a/design/jobs",
        json={
            "job_type": "design_generate",
            "budget": 250,
            "budget_limit": 250,
            "use_codex_planner": True,
        },
        headers=admin_headers,
    )
    assert needs_approval.status_code == 403

    missing_limit = client.post(
        "/projects/workspace-a/design/jobs",
        json={"job_type": "design_loop", "budget": 250},
        headers=admin_headers,
    )
    assert missing_limit.status_code == 400

    queued = client.post(
        "/projects/workspace-a/design/jobs",
        json={
            "job_type": "design_loop",
            "budget": 50,
            "warning_acknowledged": True,
        },
        headers=admin_headers,
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["job"]["job_type"] == "design_loop"
    assert queued.json()["generated_molecule_label"] == "computational_hypothesis"


def test_hosted_campaign_jobs_permissions_guardrails_and_codex_memo_boundary(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    forbidden = client.post(
        "/projects/workspace-a/campaign/jobs",
        json={"job_type": "campaign_plan"},
        headers=viewer_headers,
    )
    assert forbidden.status_code == 403

    missing_gate = client.post(
        "/projects/workspace-a/campaign/jobs",
        json={"job_type": "campaign_plan", "generated_molecule_followup": True},
        headers=admin_headers,
    )
    assert missing_gate.status_code == 403
    assert "review gate" in missing_gate.text

    protocol_text = client.post(
        "/projects/workspace-a/campaign/jobs",
        json={"job_type": "campaign_plan", "config": {"description": "Run this protocol."}},
        headers=admin_headers,
    )
    assert protocol_text.status_code == 403
    assert "planning objects" in protocol_text.text

    queued = client.post(
        "/projects/workspace-a/campaign/jobs",
        json={
            "job_type": "campaign_plan",
            "generated_molecule_followup": True,
            "generated_review_gate_present": True,
        },
        headers=admin_headers,
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["job"]["config_snapshot"]["deterministic_campaign_plan_required"] is True

    memo = client.post(
        "/projects/workspace-a/campaign/jobs",
        json={"job_type": "campaign_memo", "use_codex": True},
        headers=admin_headers,
    )
    assert memo.status_code == 200, memo.text
    config = memo.json()["job"]["config_snapshot"]
    assert config["codex_memo_label"] == "assistant_output"
    assert config["codex_memo_separate_from_deterministic_plan"] is True


def test_hosted_campaign_stage_gate_approval_requires_permission_and_is_audited(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    gate_id = _seed_campaign_store(tmp_path, "workspace-a")
    viewer = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    reviewer = client.post(
        "/admin/users",
        json={"email": "reviewer@example.com", "password": "Reviewer-password-1"},
        headers=admin_headers,
    )
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer.json()["user"]["user_id"]},
        headers=admin_headers,
    )
    client.post(
        "/projects/workspace-a/share",
        json={"role": "reviewer", "user_id": reviewer.json()["user"]["user_id"]},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")
    reviewer_headers = _login(client, "reviewer@example.com", "Reviewer-password-1")

    forbidden = client.post(
        f"/projects/workspace-a/campaigns/campaign-1/stage-gates/{gate_id}/approve",
        json={"reviewer_id": "reviewer-1", "rationale": "Reviewed artifact links."},
        headers=viewer_headers,
    )
    assert forbidden.status_code == 403

    approved = client.post(
        f"/projects/workspace-a/campaigns/campaign-1/stage-gates/{gate_id}/approve",
        json={"reviewer_id": "reviewer-1", "rationale": "Reviewed artifact links."},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage_gate"]["approval_status"] == "approved"
    database = cast(Any, client.app).state.platform_database
    event_types = [
        event.event_type for event in database.list_audit_events(project_id="workspace-a")
    ]
    assert "campaign_stage_gate_approved" in event_types


def test_hosted_model_jobs_enforce_permissions_and_prediction_boundary(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    blocked = client.post(
        "/projects/workspace-a/model/jobs",
        json={"job_type": "model_train", "dataset_id": "dataset-1"},
        headers=viewer_headers,
    )
    queued_read = client.post(
        "/projects/workspace-a/model/jobs",
        json={"job_type": "model_evaluate", "model_id": "model-1"},
        headers=viewer_headers,
    )
    queued_train = client.post(
        "/projects/workspace-a/model/jobs",
        json={
            "job_type": "model_dataset_build",
            "endpoint_name": "binding_affinity",
            "config": {"target_symbol": "MAOB"},
        },
        headers=admin_headers,
    )
    forbidden_data = client.post(
        "/projects/workspace-a/model/jobs",
        json={
            "job_type": "model_dataset_build",
            "endpoint_name": "binding_affinity",
            "config": {"use_patient_data": True},
        },
        headers=admin_headers,
    )

    assert blocked.status_code == 403
    assert "model:train" in blocked.text
    assert queued_read.status_code == 200, queued_read.text
    assert queued_read.json()["prediction_boundary"] == (
        "model_predictions_are_not_evidence_or_assay_results"
    )
    assert queued_train.status_code == 200, queued_train.text
    assert queued_train.json()["job"]["job_type"] == "model_dataset_build"
    assert forbidden_data.status_code == 403
    assert "patient data" in forbidden_data.text


def test_hosted_structure_jobs_enforce_permissions_acknowledgement_and_budget(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    unauthorized = client.post(
        "/projects/workspace-a/structure/jobs",
        json={"job_type": "structure_select", "target_symbol": "LRRK2"},
        headers=viewer_headers,
    )
    missing_ack = client.post(
        "/projects/workspace-a/structure/jobs",
        json={"job_type": "structure_dock", "target_symbol": "LRRK2", "enable_docking": True},
        headers=admin_headers,
    )
    missing_budget = client.post(
        "/projects/workspace-a/structure/jobs",
        json={
            "job_type": "structure_dock",
            "target_symbol": "LRRK2",
            "enable_docking": True,
            "warning_acknowledged": True,
            "max_ligands": 250,
        },
        headers=admin_headers,
    )
    queued = client.post(
        "/projects/workspace-a/structure/jobs",
        json={
            "job_type": "structure_dock",
            "target_symbol": "LRRK2",
            "enable_docking": True,
            "warning_acknowledged": True,
            "max_ligands": 25,
            "budget_limit": 25,
        },
        headers=admin_headers,
    )

    assert unauthorized.status_code == 403
    assert "structure:run" in unauthorized.text
    assert missing_ack.status_code == 403
    assert "acknowledgement" in missing_ack.text
    assert missing_budget.status_code == 400
    assert "budget_limit" in missing_budget.text
    assert queued.status_code == 200, queued.text
    assert queued.json()["job"]["job_type"] == "structure_dock"
    assert queued.json()["structure_report_boundary"] == "not_binding_evidence"


def test_hosted_graph_jobs_enforce_rbac_scope_and_boundaries(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    build_denied = client.post(
        "/projects/workspace-a/graph/jobs",
        json={"job_type": "graph_build"},
        headers=viewer_headers,
    )
    query = client.post(
        "/projects/workspace-a/graph/jobs",
        json={
            "job_type": "graph_query",
            "query": "generated_molecules_without_direct_evidence",
        },
        headers=viewer_headers,
    )
    cross_program_denied = client.post(
        "/projects/workspace-a/graph/jobs",
        json={
            "job_type": "graph_query",
            "query": "generated_molecules_without_direct_evidence",
            "included_project_ids": ["workspace-a", "workspace-b"],
        },
        headers=viewer_headers,
    )
    export_denied = client.post(
        "/projects/workspace-a/graph/jobs",
        json={"job_type": "graph_export"},
        headers=viewer_headers,
    )
    arbitrary_path_denied = client.post(
        "/projects/workspace-a/graph/jobs",
        json={"job_type": "graph_query", "graph_path": "/tmp/graph.json"},
        headers=admin_headers,
    )
    recommendation = client.post(
        "/projects/workspace-a/graph/jobs",
        json={"job_type": "graph_recommendation"},
        headers=admin_headers,
    )

    assert build_denied.status_code == 403
    assert "graph:build" in build_denied.text
    assert query.status_code == 200, query.text
    assert query.json()["graph_boundary"] == "memory_and_reasoning_layer_not_new_biomedical_truth"
    assert cross_program_denied.status_code == 403
    assert "workspace-b" in cross_program_denied.text
    assert export_denied.status_code == 403
    assert "graph:export" in export_denied.text
    assert arbitrary_path_denied.status_code == 400
    assert "arbitrary file paths" in arbitrary_path_denied.text
    assert recommendation.status_code == 200, recommendation.text
    assert recommendation.json()["recommendation_boundary"] == "advisory_not_automatic_decisions"
    assert recommendation.json()["job"]["metadata"]["graph_recommendations_advisory"] is True


def test_hosted_mode_blocks_arbitrary_codex_tasks(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    headers = _login(client, "admin@example.com", "Admin-password-1")
    task = CodexTask(
        task_id="unsafe-api-task",
        task_type="summarize_run",
        prompt="Run an arbitrary task.",
        working_directory=str(tmp_path),
    )

    response = client.post(
        "/codex/run-task",
        json=task.model_dump(mode="json"),
        headers=headers,
    )

    assert response.status_code == 403
    assert "arbitrary" in response.json()["detail"].lower()


def test_codex_worker_runs_allowlisted_project_jobs(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={"task_type": "summarize_project"},
    )
    provider = FakeCodexProvider()
    worker = CodexWorker(
        database=database,
        workspace_store=store,
        provider=provider,
    )

    finished = worker.run_job(job)

    assert finished.status == "succeeded"
    assert provider.requests[0].metadata["task_type"] == "summarize_project"
    assert all(
        Path(artifact.path).is_relative_to(tmp_path) for artifact in provider.requests[0].artifacts
    )


def test_codex_worker_allows_grounded_model_summary_task(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    model_artifact = tmp_path / "model-bundle.json"
    model_artifact.write_text(
        json.dumps(
            {
                "model_id": "model-1",
                "training_dataset_id": "dataset-1",
                "training_run_id": "training-run-1",
                "evaluation_id": "evaluation-1",
                "batch_id": "batch-1",
                "metrics": {"accuracy": 0.75},
                "predictions": [{"prediction_id": "prediction-1"}],
            },
            sort_keys=True,
        )
    )
    workspace.artifacts.append(_artifact(model_artifact, artifact_id="model-bundle"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_model_card",
            "allowed_artifact_ids": ["model-bundle"],
        },
    )
    stdout = json.dumps(
        {
            "status": "ok",
            "summary": (
                "Model model-1 cites dataset-1, training-run-1, evaluation-1, and batch-1. "
                "accuracy: 0.75. Predictions are not evidence."
            ),
            "limitations": ["Predictions are not evidence."],
            "model_id": "model-1",
            "dataset_id": "dataset-1",
            "training_run_id": "training-run-1",
            "evaluation_id": "evaluation-1",
            "prediction_batch_artifact_id": "batch-1",
        },
        sort_keys=True,
    )
    provider = FakeCodexProvider(stdout)

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=provider,
    ).run_job(job)

    assert finished.status == "succeeded"
    assert provider.requests[0].metadata["task_type"] == "summarize_model_card"
    assert "predictive_model_boundaries" in provider.requests[0].prompt_sections


def test_codex_structure_summary_passes_when_grounded(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    structure_artifact = tmp_path / "structure-bundle.json"
    _write_structure_codex_artifact(structure_artifact)
    workspace.artifacts.append(_artifact(structure_artifact, artifact_id="structure-bundle"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_structure_assessment",
            "allowed_artifact_ids": ["structure-bundle"],
        },
    )
    stdout = _structure_codex_stdout(
        summary=(
            "Assessment for RCSB_PDB:1ABC cites selection-1, receptor-1, dock-1, "
            "pose-1, profile-1, and artifact structure-bundle. Docking score -7.0 is "
            "a computational signal only. Contact A:LYS33 is reported from the artifact."
        )
    )

    provider = FakeCodexProvider(stdout)
    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=provider,
    ).run_job(job)

    assert finished.status == "succeeded"
    assert provider.requests[0].metadata["task_type"] == "summarize_structure_assessment"
    assert "structure_workflow_boundaries" in provider.requests[0].prompt_sections


def test_codex_structure_summary_flags_fake_residue_contact(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    structure_artifact = tmp_path / "structure-bundle.json"
    _write_structure_codex_artifact(structure_artifact)
    workspace.artifacts.append(_artifact(structure_artifact, artifact_id="structure-bundle"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_structure_assessment",
            "allowed_artifact_ids": ["structure-bundle"],
        },
    )
    stdout = _structure_codex_stdout(
        summary=(
            "Assessment cites RCSB_PDB:1ABC, selection-1, receptor-1, dock-1, "
            "pose-1, profile-1, and structure-bundle, but adds residue contact A:ASP999."
        )
    )

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=FakeCodexProvider(stdout),
    ).run_job(job)

    assert finished.status == "guardrail_failed"
    assert "Unbacked structure residue contact" in (finished.error_summary or "")


def test_codex_structure_summary_flags_fake_docking_score(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    structure_artifact = tmp_path / "structure-bundle.json"
    _write_structure_codex_artifact(structure_artifact)
    workspace.artifacts.append(_artifact(structure_artifact, artifact_id="structure-bundle"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_structure_assessment",
            "allowed_artifact_ids": ["structure-bundle"],
        },
    )
    stdout = _structure_codex_stdout(
        summary=(
            "Assessment cites RCSB_PDB:1ABC, selection-1, receptor-1, dock-1, pose-1, "
            "profile-1, and structure-bundle. docking_score: -12.3."
        )
    )

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=FakeCodexProvider(stdout),
    ).run_job(job)

    assert finished.status == "guardrail_failed"
    assert "Unbacked structure docking score" in (finished.error_summary or "")


def test_codex_structure_summary_flags_binding_overclaim(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    structure_artifact = tmp_path / "structure-bundle.json"
    _write_structure_codex_artifact(structure_artifact)
    workspace.artifacts.append(_artifact(structure_artifact, artifact_id="structure-bundle"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_structure_assessment",
            "allowed_artifact_ids": ["structure-bundle"],
        },
    )
    stdout = _structure_codex_stdout(
        summary=(
            "Rasagiline binds MAOB based on pose-1. The report cites RCSB_PDB:1ABC, "
            "selection-1, receptor-1, dock-1, profile-1, and structure-bundle."
        )
    )

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=FakeCodexProvider(stdout),
    ).run_job(job)

    assert finished.status == "guardrail_failed"
    assert "Forbidden biomedical claim" in (finished.error_summary or "")


def test_codex_worker_excludes_forbidden_artifacts(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    secret_path = tmp_path / ".env"
    secret_path.write_text("OPENAI_API_KEY=sk-secretsecretsecretsecret\n")
    workspace.artifacts.append(_artifact(secret_path, artifact_id="secret-env"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_project",
            "allowed_artifact_ids": [artifact.artifact_id for artifact in workspace.artifacts],
        },
    )
    provider = FakeCodexProvider()

    finished = CodexWorker(database=database, workspace_store=store, provider=provider).run_job(job)

    assert finished.status == "succeeded"
    included_ids = [artifact.artifact_id for artifact in provider.requests[0].artifacts]
    assert "secret-env" not in included_ids


def test_codex_worker_records_guardrail_failure(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={"task_type": "summarize_project"},
    )
    stdout = json.dumps(
        {"status": "ok", "summary": "Aspirin treats disease.", "limitations": []},
        sort_keys=True,
    )

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=FakeCodexProvider(stdout),
    ).run_job(job)

    assert finished.status == "guardrail_failed"
    assert "Forbidden biomedical claim" in (finished.error_summary or "")
    with database.engine.connect() as connection:
        row = connection.execute(
            select(platform_jobs).where(platform_jobs.c.job_id == job.job_id)
        ).mappings().one()
    assert row["status"] == "guardrail_failed"


def test_codex_worker_transcript_redacts_secrets(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={"task_type": "summarize_project"},
    )
    secret = "sk-abcdefghijklmnop1234567890"
    stdout = json.dumps(
        {
            "status": "ok",
            "summary": f"token={secret}",
            "limitations": [],
        },
        sort_keys=True,
    )

    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=FakeCodexProvider(stdout),
    ).run_job(job)

    assert finished.status == "guardrail_failed"
    with database.engine.connect() as connection:
        rows = connection.execute(
            select(artifact_records).where(artifact_records.c.artifact_type == "codex_transcript")
        ).mappings().fetchall()
    assert rows
    transcript = Path(rows[0]["path"]).read_text()
    assert secret not in transcript
    assert "[REDACTED" in transcript


def test_codex_worker_default_provider_skips_git_repo_check(tmp_path: Path) -> None:
    database, _user, store = _codex_project(tmp_path)
    worker = CodexWorker(database=database, workspace_store=store)

    provider = worker._provider_for(tmp_path / "isolated-worker-dir")  # noqa: SLF001

    assert isinstance(provider, CodexCLIProvider)
    assert "--skip-git-repo-check" in provider.config.command
    assert "--ignore-user-config" in provider.config.command
    assert "--ignore-rules" in provider.config.command


def test_user_data_export_and_delete_respect_retention_policy(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    admin = database.create_user(
        email="admin@example.com",
        password="Admin-password-1",
        roles=["platform_admin", "user"],
    )
    user = database.create_user(email="user@example.com", password="User-password-1")
    database.enqueue_job(job_type="codex.summarize_project", requested_by_user_id=user.user_id)

    exported = database.export_user_data(user.user_id)
    assert exported["user"]["email"] == "user@example.com"
    assert exported["jobs"][0]["job_type"] == "codex.summarize_project"

    database.delete_user(user.user_id, actor_user_id=admin.user_id)
    assert database.get_user(user.user_id) is None


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"


def _write_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB"}],
        "candidates": [
            {
                "name": "Rasagiline",
                "origin": "existing",
                "known_targets": ["MAOB"],
                "score": 0.82,
                "score_breakdown": {"confidence": 0.7},
            }
        ],
        "generated_molecule_hypotheses": [],
        "summary": {"candidate_count": 1, "generated_candidate_count": 0, "target_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")


def _write_structure_codex_artifact(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "structure_id": "RCSB_PDB:1ABC",
                "selection_id": "selection-1",
                "receptor_prep_id": "receptor-1",
                "docking_run_id": "dock-1",
                "pose_id": "pose-1",
                "interaction_profile_id": "profile-1",
                "artifact_ids": ["structure-bundle"],
                "docking_score": -7.0,
                "key_residue_contacts": ["A:LYS33"],
                "interactions": [{"residue": "A:LYS33", "type": "hydrophobic"}],
                "limitations": ["Docking scores do not prove binding."],
            },
            sort_keys=True,
        )
    )


def _structure_codex_stdout(summary: str) -> str:
    return json.dumps(
        {
            "status": "ok",
            "summary": summary,
            "limitations": ["Docking scores do not prove binding."],
            "structure_id": "RCSB_PDB:1ABC",
            "selection_id": "selection-1",
            "receptor_prep_id": "receptor-1",
            "docking_run_id": "dock-1",
            "pose_id": "pose-1",
            "interaction_profile_id": "profile-1",
            "artifact_ids": ["structure-bundle"],
        },
        sort_keys=True,
    )


def _codex_project(tmp_path: Path):
    _write_run(tmp_path / "run-a")
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="admin@example.com", password="Admin-password-1")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    store.register_run_dir(tmp_path / "run-a", run_id="run-a", workspace=workspace)
    database.grant_project_permission(
        project_id="workspace-a",
        role="project_owner",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    return database, user, store


def _artifact(path: Path, *, artifact_id: str) -> ArtifactRecord:
    data = path.read_bytes()
    return ArtifactRecord(
        artifact_id=artifact_id,
        workspace_id="workspace-a",
        path=str(path.resolve()),
        artifact_type="secret",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _seed_campaign_store(tmp_path: Path, project_id: str) -> str:
    store = CampaignStore(
        tmp_path / ".molecule-ranker" / "campaigns" / project_id / "campaigns.sqlite"
    )
    now = datetime.now(UTC)
    store.create_campaign(
        Campaign(
            campaign_id="campaign-1",
            project_id=project_id,
            program_id="program-1",
            name="Campaign 1",
            description="Planning artifact.",
            disease_focus=["Parkinson disease"],
            target_focus=["MAOB"],
            hypothesis_ids=["hypothesis-1"],
            portfolio_selection_ids=["selection-1"],
            status="under_review",
            created_at=now,
            updated_at=now,
            metadata={},
        )
    )
    gate_id = "gate-generated-review"
    store.add_stage_gate_decision(
        {
            "gate_id": gate_id,
            "campaign_id": "campaign-1",
            "work_package_id": None,
            "gate_type": "generated_molecule_review",
            "required_role": ["scientific_reviewer"],
            "required_permissions": ["campaign:approve"],
            "required_artifacts": ["hypothesis-1"],
            "required_review_decisions": ["generated_molecule_review_decision"],
            "blocking_conditions": ["generated_molecule_human_review_required"],
            "approval_status": "pending",
            "rationale": "Generated molecule follow-up requires review.",
            "audit_event": None,
            "metadata": {"not_codex_approvable": True},
        }
    )
    return gate_id
