from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore
from molecule_ranker_sdk import (
    AuthenticationError,
    MoleculeRankerV2Client,
    PermissionDeniedError,
)


def test_sdk_v2_works_against_testclient(tmp_path: Path) -> None:
    api = _hosted_test_client(tmp_path)
    sdk = MoleculeRankerV2Client(http_client=api, service_token=_admin_service_token(api))

    project = sdk.create_project(workspace_id="sdk-v2-project", name="SDK V2 Project")
    job = sdk.submit_job(
        project_id=project.workspace_id,
        job_type="ranking",
        config={"run_id": "run-1", "service_token": "mrs-secret-value"},
        idempotency_key="run-1-ranking",
    )
    health = sdk.admin_health()

    assert project.workspace_id == "sdk-v2-project"
    assert job.status == "queued"
    assert job.project_id == "sdk-v2-project"
    assert "mrs-secret-value" not in job.model_dump_json()
    assert health.ok is True
    assert sdk.last_request_id


def test_sdk_v2_auth_error_has_request_id(tmp_path: Path) -> None:
    sdk = MoleculeRankerV2Client(
        http_client=_hosted_test_client(tmp_path),
        service_token="mrs-invalid",
    )

    with pytest.raises(AuthenticationError) as exc_info:
        sdk.list_projects()

    assert exc_info.value.status_code == 401
    assert exc_info.value.request_id
    assert exc_info.value.error_code == "unauthorized"


def test_sdk_v2_pagination_helpers(tmp_path: Path) -> None:
    api = _hosted_test_client(tmp_path)
    sdk = MoleculeRankerV2Client(http_client=api, service_token=_admin_service_token(api))
    project = sdk.create_project(workspace_id="sdk-v2-project", name="SDK V2 Project")
    for index in range(3):
        sdk.submit_job(
            project_id=project.workspace_id,
            job_type="ranking",
            config={"run_id": f"run-{index}"},
            idempotency_key=f"run-{index}",
        )

    first_page = sdk.list_project_jobs(project.workspace_id, limit=2)
    all_jobs = list(
        sdk.paginate(
            lambda page: sdk.list_project_jobs(
                project.workspace_id,
                limit=page.limit,
                offset=page.offset,
            ),
            limit=2,
        )
    )

    assert first_page.pagination.limit == 2
    assert first_page.pagination.count == 2
    assert len(all_jobs) == 3
    assert {job.job_type for job in all_jobs} == {"ranking"}


def test_sdk_v2_artifact_download_respects_permissions(tmp_path: Path) -> None:
    api = _hosted_test_client(tmp_path)
    admin_sdk = MoleculeRankerV2Client(http_client=api, service_token=_admin_service_token(api))
    admin_sdk.create_project(workspace_id="sdk-v2-project", name="SDK V2 Project")
    pose_path = tmp_path / "pose.json"
    pose_path.write_text('{"artifact":"pose"}\n', encoding="utf-8")
    _append_artifact(
        tmp_path,
        pose_path,
        artifact_id="pose-artifact",
        artifact_type="docking_pose",
    )
    viewer_sdk = MoleculeRankerV2Client(
        http_client=api,
        service_token=_viewer_service_token(api, project_id="sdk-v2-project"),
    )

    downloaded = admin_sdk.download_artifact(
        project_id="sdk-v2-project",
        artifact_id="pose-artifact",
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        viewer_sdk.download_artifact(project_id="sdk-v2-project", artifact_id="pose-artifact")

    assert downloaded == pose_path.read_bytes()
    assert exc_info.value.status_code == 403
    assert exc_info.value.request_id
    assert "structure:export" in exc_info.value.message


def test_sdk_v2_retries_safe_get_but_not_plain_writes() -> None:
    flaky = _FlakyHTTPClient()
    sdk = MoleculeRankerV2Client(http_client=flaky, service_token="mrs-test")

    response = sdk._request("GET", "/health")
    with pytest.raises(AuthenticationError):
        sdk._request("POST", "/projects", json={"workspace_id": "p1"})

    assert response.status_code == 200
    assert flaky.calls[("GET", "http://testserver/api/v2/health")] == 2
    assert flaky.calls[("POST", "http://testserver/api/v2/projects")] == 1


def _hosted_test_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret="test-hosted-secret-value-with-at-least-32-chars",
            platform_db_path=tmp_path / "platform.sqlite",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v2/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _admin_service_token(client: TestClient) -> str:
    headers = _login(client)
    user = client.get("/api/v2/auth/me", headers=headers)
    assert user.status_code == 200, user.text
    response = client.post(
        "/api/v2/auth/token/create",
        json={
            "name": "sdk-v2-admin",
            "user_id": user.json()["user"]["user_id"],
            "scopes": [
                "admin:manage_users",
                "admin:view_audit",
                "project:read",
                "project:create",
                "run:create",
                "evaluation:read",
                "structure:export",
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _viewer_service_token(client: TestClient, *, project_id: str) -> str:
    admin_headers = _login(client)
    created = client.post(
        "/api/v2/admin/users",
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
        f"/api/v2/projects/{project_id}/share",
        json={"role": "viewer", "user_id": user_id},
        headers=admin_headers,
    )
    assert shared.status_code == 200, shared.text
    token = client.post(
        "/api/v2/auth/token/create",
        json={"name": "sdk-v2-viewer", "user_id": user_id, "scopes": ["project:read"]},
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


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"X-Request-ID": "req-fake"}
        self.content = b"{}"
        self.text = "{}"

    def json(self) -> dict[str, Any]:
        return self._payload


class _FlakyHTTPClient:
    def __init__(self) -> None:
        self.calls: dict[tuple[str, str], int] = {}

    def request(self, method: str, url: str, **_kwargs: Any) -> _FakeResponse:
        key = (method, url)
        self.calls[key] = self.calls.get(key, 0) + 1
        if method == "GET" and self.calls[key] == 1:
            return _FakeResponse(503, {"detail": "temporary"})
        if method == "POST":
            return _FakeResponse(
                401,
                {
                    "detail": "Bearer token required.",
                    "error": {
                        "code": "unauthorized",
                        "message": "Bearer token required.",
                        "request_id": "req-post",
                    },
                },
                headers={"X-Request-ID": "req-post"},
            )
        return _FakeResponse(200, {"ok": True})
