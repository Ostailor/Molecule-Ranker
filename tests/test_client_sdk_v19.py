from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from molecule_ranker.client import (
    AuthenticationError,
    MoleculeRankerClient,
    PermissionDeniedError,
)
from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def test_client_against_testclient_core_workflow(tmp_path: Path) -> None:
    test_client = _hosted_test_client(tmp_path)
    service_token = _admin_service_token(test_client)
    sdk = MoleculeRankerClient(session=test_client, service_token=service_token)

    project = sdk.create_project(workspace_id="sdk-project", name="SDK Project")
    report_path = tmp_path / "evaluation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "report_id": "eval-report",
                "summary": "Synthetic pilot SDK evaluation artifact.",
                "not_biomedical_evidence": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _append_artifact(
        tmp_path,
        report_path,
        artifact_id="eval-report",
        artifact_type="evaluation_report",
    )

    projects = sdk.list_projects(limit=1, offset=0, sort="name")
    job = sdk.submit_job(
        project_id="sdk-project",
        job_type="ranking",
        config={"run_id": "run-1", "service_token": "mrs-secret-value"},
        idempotency_key="run-1-ranking",
    )
    polled = sdk.poll_job(job.job_id)
    jobs = sdk.list_jobs(project_id="sdk-project", limit=1)
    artifacts = sdk.list_artifacts(project_id="sdk-project", limit=1)
    downloaded = sdk.download_artifact(project_id="sdk-project", artifact_id="eval-report")
    feedback = sdk.submit_feedback(
        project_id="sdk-project",
        page_or_command="sdk/test",
        feedback_type="usability_issue",
        severity="low",
        text="Token: mrs-secret-value should be redacted.",
    )
    readiness = sdk.run_readiness()
    evaluation = sdk.retrieve_evaluation_report(
        project_id="sdk-project",
        report_id="eval-report",
    )

    assert project["workspace_id"] == "sdk-project"
    assert projects.pagination.count == 1
    assert projects.projects[0].workspace_id == "sdk-project"
    assert job.status == "queued"
    assert polled.job_id == job.job_id
    assert "mrs-secret-value" not in json.dumps(polled.model_dump(mode="json"))
    assert jobs.pagination.count == 1
    assert artifacts.pagination.count == 1
    assert downloaded == report_path.read_bytes()
    assert feedback.metadata["not_scientific_evidence"] is True
    assert readiness["version"] == "2.8.0"
    assert evaluation.evaluation_boundary == "evaluation_reports_are_not_biomedical_evidence"
    assert evaluation.report["not_biomedical_evidence"] is True


def test_client_auth_failure_preserves_request_id(tmp_path: Path) -> None:
    test_client = _hosted_test_client(tmp_path)
    sdk = MoleculeRankerClient(session=test_client, service_token="mrs-invalid")

    with pytest.raises(AuthenticationError) as exc_info:
        sdk.list_projects()

    assert exc_info.value.status_code == 401
    assert exc_info.value.request_id
    assert exc_info.value.error_code == "unauthorized"


def test_client_pagination_for_projects_and_artifacts(tmp_path: Path) -> None:
    test_client = _hosted_test_client(tmp_path)
    service_token = _admin_service_token(test_client)
    sdk = MoleculeRankerClient(session=test_client, service_token=service_token)
    sdk.create_project(workspace_id="sdk-project", name="SDK Project")
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"name":"first"}\n', encoding="utf-8")
    second.write_text('{"name":"second"}\n', encoding="utf-8")
    _append_artifact(tmp_path, first, artifact_id="first-artifact")
    _append_artifact(tmp_path, second, artifact_id="second-artifact")

    projects = sdk.list_projects(limit=1, offset=0, filter="sdk")
    artifacts = sdk.list_artifacts(project_id="sdk-project", limit=1, offset=1)

    assert projects.pagination.limit == 1
    assert projects.pagination.count == 1
    assert artifacts.pagination.limit == 1
    assert artifacts.pagination.offset == 1
    assert artifacts.pagination.count == 1


def test_client_artifact_download_permission(tmp_path: Path) -> None:
    test_client = _hosted_test_client(tmp_path)
    admin_token = _admin_service_token(test_client)
    admin_sdk = MoleculeRankerClient(session=test_client, service_token=admin_token)
    admin_sdk.create_project(workspace_id="sdk-project", name="SDK Project")
    pose_path = tmp_path / "pose.json"
    pose_path.write_text('{"artifact":"pose"}\n', encoding="utf-8")
    _append_artifact(
        tmp_path,
        pose_path,
        artifact_id="pose-artifact",
        artifact_type="docking_pose",
    )
    viewer_token = _viewer_service_token(test_client, project_id="sdk-project")
    viewer_sdk = MoleculeRankerClient(session=test_client, service_token=viewer_token)

    with pytest.raises(PermissionDeniedError) as exc_info:
        viewer_sdk.download_artifact(project_id="sdk-project", artifact_id="pose-artifact")

    assert exc_info.value.status_code == 403
    assert exc_info.value.request_id
    assert "structure:export" in exc_info.value.message


def _hosted_test_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            platform_db_path=tmp_path / "platform.sqlite",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _admin_service_token(client: TestClient) -> str:
    headers = _login(client)
    user = client.get("/auth/me", headers=headers)
    assert user.status_code == 200, user.text
    response = client.post(
        "/auth/token/create",
        json={
            "name": "sdk-admin",
            "user_id": user.json()["user"]["user_id"],
            "scopes": [
                "admin:manage_users",
                "admin:view_audit",
                "project:read",
                "project:create",
                "run:create",
                "evaluation:read",
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _viewer_service_token(client: TestClient, *, project_id: str) -> str:
    admin_headers = _login(client)
    created = client.post(
        "/admin/users",
        json={
            "email": "viewer@example.com",
            "password": "Viewer-password-1",
            "roles": ["user"],
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["user"]["user_id"]
    shared = client.post(
        f"/projects/{project_id}/share",
        json={"role": "viewer", "user_id": user_id},
        headers=admin_headers,
    )
    assert shared.status_code == 200, shared.text
    token = client.post(
        "/auth/token/create",
        json={"name": "sdk-viewer", "user_id": user_id, "scopes": ["project:read"]},
        headers=admin_headers,
    )
    assert token.status_code == 200, token.text
    return str(token.json()["access_token"])


def _append_artifact(
    tmp_path: Path,
    path: Path,
    *,
    artifact_id: str,
    artifact_type: str = "report",
) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    data = path.read_bytes()
    workspace.artifacts.append(
        ArtifactRecord(
            artifact_id=artifact_id,
            workspace_id=workspace.workspace_id,
            path=str(path.resolve()),
            artifact_type=artifact_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
    )
    store.save(workspace)


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"
