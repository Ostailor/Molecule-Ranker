from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.server import create_app
from molecule_ranker.server.security import public_bind_allowed
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def test_health_ready_version_and_secure_headers(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))

    response = client.get("/health", headers={"X-Request-ID": "test-request"})
    ready = client.get("/ready")
    version = client.get("/version")
    favicon = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert ready.status_code == 200
    assert ready.json()["ok"] is True
    assert version.status_code == 200
    assert version.json()["version"]
    assert favicon.status_code == 204


def test_hosted_api_requires_auth(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))

    response = client.get("/projects")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_permission_denied_returns_structured_403(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, tmp_path, admin_headers)
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    response = client.get("/projects/workspace-a", headers=viewer_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_artifact_download_blocks_path_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    workspace.artifacts.append(
        ArtifactRecord(
            artifact_id="outside-secret",
            workspace_id="workspace-a",
            path=str(outside),
            artifact_type="report",
            sha256="x",
            size_bytes=outside.stat().st_size,
        )
    )
    store.save(workspace)
    client = TestClient(create_app(root_dir=tmp_path))

    response = client.get("/projects/workspace-a/artifacts/outside-secret/download")
    suspicious = client.get("/projects/workspace-a/artifacts/..%2F.env/download")

    assert response.status_code == 403
    assert suspicious.status_code in {403, 404}


def test_request_size_limit_is_enforced(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path, max_request_bytes=16))

    response = client.post(
        "/codex/run-task",
        content=b'{"payload":"' + (b"x" * 64) + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_file_upload_size_limit_is_enforced(tmp_path: Path) -> None:
    client = TestClient(
        create_app(root_dir=tmp_path, max_request_bytes=1_000, max_upload_bytes=16)
    )

    response = client.post(
        "/projects",
        content=b"--x\r\n" + (b"a" * 64) + b"\r\n--x--\r\n",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_codex_endpoint_blocked_without_permission(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, tmp_path, admin_headers)
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    user_id = created.json()["user"]["user_id"]
    shared = client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": user_id},
        headers=admin_headers,
    )
    assert shared.status_code == 200, shared.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    response = client.post("/projects/workspace-a/codex/summarize", headers=viewer_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_public_bind_requires_explicit_opt_in() -> None:
    try:
        public_bind_allowed("0.0.0.0", allow_public_bind=False)
    except ValueError as exc:
        assert "allow_public_bind" in str(exc)
    else:
        raise AssertionError("Expected public bind to require explicit opt in.")

    public_bind_allowed("0.0.0.0", allow_public_bind=True)


def _hosted_app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_project(client: TestClient, tmp_path: Path, headers: dict[str, str]) -> None:
    response = client.post(
        "/projects",
        json={"workspace_id": "workspace-a", "name": "Research"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    run_dir = tmp_path / "run-a"
    run_dir.mkdir()
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB"}],
        "candidates": [{"name": "Rasagiline", "score": 0.82}],
        "generated_molecule_hypotheses": [],
        "summary": {"candidate_count": 1, "generated_candidate_count": 0, "target_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    store.register_run_dir(run_dir, run_id="run-a", workspace=workspace)
