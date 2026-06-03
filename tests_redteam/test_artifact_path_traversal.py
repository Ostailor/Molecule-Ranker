from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def test_path_traversal_artifact_download_is_blocked(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    headers = _login(client)
    created = client.post(
        "/api/v2/projects",
        json={"workspace_id": "redteam-project", "name": "Red-team Project"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    outside = tmp_path.parent / "redteam-outside-secret.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    _append_artifact(tmp_path, outside, artifact_id="outside-artifact")

    outside_response = client.get(
        "/api/v2/projects/redteam-project/artifacts/outside-artifact/download",
        headers=headers,
    )
    traversal_response = client.get(
        "/api/v2/projects/redteam-project/artifacts/..%2F.env/download",
        headers=headers,
    )

    assert outside_response.status_code == 403
    assert traversal_response.status_code in {403, 404}


def _app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v2/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _append_artifact(root: Path, path: Path, *, artifact_id: str) -> None:
    store = ProjectWorkspaceStore(root)
    workspace = store.load()
    data = path.read_bytes()
    workspace.artifacts.append(
        ArtifactRecord(
            artifact_id=artifact_id,
            workspace_id=workspace.workspace_id,
            path=str(path.resolve()),
            artifact_type="report",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
    )
    store.save(workspace)
