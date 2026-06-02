from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from molecule_ranker.platform import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.server import create_app

EVALUATION_JOB_TYPES = {
    "eval_dataset_build",
    "eval_split",
    "eval_benchmark_run",
    "eval_prospective_freeze",
    "eval_prospective_evaluate",
    "eval_guardrail_benchmark",
    "eval_reproducibility",
    "eval_trend_report",
}


def test_evaluation_job_types_and_permissions_are_supported(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    owner = database.create_user(email="owner@example.com", password="Owner-password-1")
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="workspace-a",
        role="project_owner",
        actor_user_id=owner.user_id,
        user_id=owner.user_id,
    )
    database.grant_project_permission(
        project_id="workspace-a",
        role="viewer",
        actor_user_id=owner.user_id,
        user_id=viewer.user_id,
    )
    queue = PlatformJobQueue(database)

    assert has_permission(owner, "evaluation:run", project_id="workspace-a", database=database)
    assert has_permission(owner, "evaluation:export", project_id="workspace-a", database=database)
    assert has_permission(viewer, "evaluation:read", project_id="workspace-a", database=database)
    assert not has_permission(viewer, "evaluation:run", project_id="workspace-a", database=database)

    for job_type in EVALUATION_JOB_TYPES:
        job = queue.enqueue(
            job_type=job_type,
            requested_by=owner,
            project_id="workspace-a",
            config_snapshot={"acknowledge_not_evidence": True},
        )
        assert job.job_type == job_type
        assert job.metadata["evaluation_reports_are_not_evidence"] is True


def test_prospective_freeze_jobs_are_immutable(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    owner = database.create_user(email="owner@example.com", password="Owner-password-1")
    database.grant_project_permission(
        project_id="workspace-a",
        role="project_owner",
        actor_user_id=owner.user_id,
        user_id=owner.user_id,
    )

    with pytest.raises(Exception, match="cannot be edited"):
        PlatformJobQueue(database).enqueue(
            job_type="eval_prospective_freeze",
            requested_by=owner,
            project_id="workspace-a",
            config_snapshot={"edit_existing": True, "frozen_prediction_set_id": "fps-1"},
        )


def test_evaluation_api_jobs_enforce_rbac_and_boundaries(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
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
    viewer_headers = _api_login(client, "viewer@example.com", "Viewer-password-1")

    forbidden = client.post(
        "/projects/workspace-a/evaluation/jobs",
        json={"job_type": "eval_benchmark_run"},
        headers=viewer_headers,
    )
    queued = client.post(
        "/projects/workspace-a/evaluation/jobs",
        json={"job_type": "eval_benchmark_run"},
        headers=admin_headers,
    )

    assert forbidden.status_code == 403
    assert "evaluation:run" in forbidden.text
    assert queued.status_code == 200, queued.text
    payload = queued.json()
    assert payload["job"]["job_type"] == "eval_benchmark_run"
    assert payload["evaluation_boundary"] == "evaluation_reports_are_not_evidence"


def test_evaluation_dashboard_pages_and_outcome_visibility(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
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
    _seed_evaluation_artifact(cast(Any, client.app).state.platform_database, tmp_path)

    client.cookies.clear()
    _web_login(client, "viewer@example.com", "Viewer-password-1")
    overview = client.get("/dashboard/projects/workspace-a/evaluation")
    outcomes = client.get("/dashboard/projects/workspace-a/evaluation/prospective-validation-runs")

    assert overview.status_code == 200, overview.text
    assert "Evaluation overview" in overview.text
    assert "Evaluation reports are not evidence" in overview.text
    assert "Benchmark suites" in overview.text
    assert outcomes.status_code == 403

    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")
    admin_outcomes = client.get(
        "/dashboard/projects/workspace-a/evaluation/prospective-validation-runs"
    )
    assert admin_outcomes.status_code == 200, admin_outcomes.text
    assert "Prospective validation runs" in admin_outcomes.text
    assert "Only authorized users can view imported outcomes" in admin_outcomes.text


def test_evaluation_dashboard_empty_state_and_navigation_are_clean(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    assert (
        client.post(
            "/projects",
            json={"workspace_id": "workspace-a", "name": "Research"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    _web_login(client, "admin@example.com", "Admin-password-1")

    project = client.get("/dashboard/projects/workspace-a")
    overview = client.get("/dashboard/projects/workspace-a/evaluation")
    guardrails = client.get(
        "/dashboard/projects/workspace-a/evaluation/guardrail-benchmark-reports"
    )

    assert project.status_code == 200, project.text
    assert '/dashboard/projects/workspace-a/evaluation">Evaluation</a>' in project.text
    assert "Evaluation reports measure platform performance" in project.text
    assert overview.status_code == 200, overview.text
    assert overview.text.count("<h1>Evaluation overview</h1>") == 1
    assert '<nav class="nav">' in overview.text
    assert "No evaluation artifacts are available yet." in overview.text
    assert guardrails.status_code == 200, guardrails.text
    assert "Guardrail benchmark reports surface failures." in guardrails.text


def _app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _web_login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _seed_evaluation_artifact(database: PlatformDatabase, tmp_path: Path) -> None:
    artifact_path = tmp_path / "evaluation_report.json"
    artifact_path.write_text(
        json.dumps(
            {
                "evaluation_id": "eval-1",
                "limitations": ["Benchmark results are evaluation artifacts."],
                "metadata": {"contains_outcomes": True},
            }
        ),
        encoding="utf-8",
    )
    job = PlatformJobQueue(database).enqueue(
        job_type="eval_benchmark_run",
        requested_by=database.authenticate_user(
            email="admin@example.com",
            password="Admin-password-1",
        ),
        project_id="workspace-a",
        config_snapshot={},
    )
    PlatformJobQueue(database).register_artifact(
        job,
        artifact_path,
        artifact_type="evaluation_report",
    )
