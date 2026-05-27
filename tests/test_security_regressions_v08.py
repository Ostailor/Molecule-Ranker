from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.codex.provider import CodexRequest, CodexResponse
from molecule_ranker.codex_backbone.schemas import CodexTask
from molecule_ranker.platform import CodexWorker, PlatformDatabase
from molecule_ranker.platform.auth import generate_opaque_token
from molecule_ranker.platform.database import artifact_records, service_account_tokens, users
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


class FakeCodexProvider:
    def __init__(self, stdout: str | None = None) -> None:
        self.stdout = stdout or json.dumps(
            {"status": "ok", "summary": "ok", "limitations": []},
            sort_keys=True,
        )
        self.requests: list[CodexRequest] = []

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


def test_no_plaintext_passwords_or_service_tokens_after_creation(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="admin@example.com", password="Admin-password-1")
    service_token = generate_opaque_token(prefix="mrs")
    token_id = database.create_service_account_token(
        name="automation",
        token=service_token,
        user_id=user.user_id,
        created_by_user_id=user.user_id,
        scopes=["project:read"],
    )

    with database.engine.connect() as connection:
        user_row = (
            connection.execute(select(users).where(users.c.user_id == user.user_id))
            .mappings()
            .one()
        )
        token_row = (
            connection.execute(
                select(service_account_tokens).where(
                    service_account_tokens.c.token_id == token_id
                )
            )
            .mappings()
            .one()
        )
    assert user_row["password_hash"] != "Admin-password-1"
    assert user_row["password_salt"] != "Admin-password-1"
    assert token_row["token_hash"] != service_token
    assert token_row["token_salt"] != service_token
    assert service_token not in json.dumps(database.list_service_account_tokens())


def test_no_env_contents_in_codex_transcripts(tmp_path: Path) -> None:
    database, user, store = _codex_project(tmp_path)
    workspace = store.load()
    env_path = tmp_path / ".env"
    env_secret = "OPENAI_API_KEY=sk-secretsecretsecretsecret\n"
    env_path.write_text(env_secret)
    workspace.artifacts.append(_artifact(env_path, artifact_id="secret-env"))
    store.save(workspace)
    job = PlatformJobQueue(database).enqueue(
        job_type="codex_task",
        requested_by=user,
        project_id="workspace-a",
        config_snapshot={
            "task_type": "summarize_project",
            "allowed_artifact_ids": [item.artifact_id for item in workspace.artifacts],
        },
    )

    provider = FakeCodexProvider()
    finished = CodexWorker(
        database=database,
        workspace_store=store,
        provider=provider,
    ).run_job(job)

    assert finished.status == "succeeded"
    transcript_paths = [
        Path(path)
        for path in finished.result_artifact_ids
        for path in _artifact_paths(database, artifact_id=path)
    ]
    transcript_text = "\n".join(path.read_text() for path in transcript_paths)
    included_ids = [artifact.artifact_id for artifact in provider.requests[0].artifacts]
    assert "secret-env" not in included_ids
    assert env_secret.strip() not in transcript_text
    assert "sk-secretsecretsecretsecret" not in transcript_text
    assert "cache files" not in transcript_text.lower()


def test_no_cache_file_downloads_or_path_traversal_in_artifacts(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers)
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "cached.json"
    cache_file.write_text('{"secret": true}\n')
    outside = tmp_path.parent / "outside-dashboard-secret.txt"
    outside.write_text("outside secret\n")
    _append_workspace_artifact(tmp_path, cache_file, artifact_id="cache-file")
    _append_workspace_artifact(tmp_path, outside, artifact_id="outside-file")

    cache_response = client.get(
        "/projects/workspace-a/artifacts/cache-file/download",
        headers=admin_headers,
    )
    outside_response = client.get(
        "/projects/workspace-a/artifacts/outside-file/download",
        headers=admin_headers,
    )
    traversal = client.get(
        "/projects/workspace-a/artifacts/..%2F.env/download",
        headers=admin_headers,
    )

    assert cache_response.status_code == 403
    assert outside_response.status_code == 403
    assert traversal.status_code in {403, 404}


def test_no_unauthenticated_dashboard_or_unauthorized_project_access(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers)
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    viewer_headers = _login(client, "viewer@example.com", "Viewer-password-1")

    dashboard = client.get("/dashboard", follow_redirects=False)
    project = client.get("/projects/workspace-a", headers=viewer_headers)

    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/login"
    assert project.status_code == 403


def test_no_codex_task_without_codex_run_permission(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers)
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


def test_no_direct_shell_execution_endpoint(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    headers = _login(client, "admin@example.com", "Admin-password-1")
    task = CodexTask(
        task_id="unsafe",
        task_type="summarize_project",
        prompt="run whoami",
        working_directory=str(tmp_path),
    )

    arbitrary_codex = client.post(
        "/codex/run-task",
        json=task.model_dump(mode="json"),
        headers=headers,
    )
    shell_paths = [
        client.post(path, json={"command": "whoami"}, headers=headers)
        for path in ["/shell", "/shell/execute", "/execute", "/api/shell"]
    ]

    assert arbitrary_codex.status_code == 403
    assert all(response.status_code == 404 for response in shell_paths)


def test_no_secrets_in_config_output(monkeypatch) -> None:
    monkeypatch.setenv("MOLECULE_RANKER_SECRET_KEY", "super-secret-config-value")
    runner = CliRunner()

    response = runner.invoke(app, ["config", "show", "--redacted"])

    assert response.exit_code == 0, response.output
    assert "super-secret-config-value" not in response.output
    assert "[REDACTED]" in response.output


def test_no_generated_molecules_shown_as_validated_actives(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(
        client,
        tmp_path,
        admin_headers,
        generated_rationale="Validated active with potent assay confirmation.",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/generated")

    assert response.status_code == 200
    assert "Validated active" not in response.text
    assert "Computational hypothesis" in response.text
    assert "[unsupported validation claim redacted]" in response.text


def test_no_codex_outputs_shown_as_evidence(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers)
    _write_codex_output(tmp_path, output_text="Assistant summary from registered artifacts.")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "Codex-generated summaries are assistant outputs, not evidence" in response.text
    assert "Assistant summary from registered artifacts." in response.text
    assert "<h2>Source-backed evidence</h2>" not in response.text


def test_no_synthesis_lab_protocol_or_dosing_text_in_dashboard_summaries(
    tmp_path: Path,
) -> None:
    unsafe = (
        "Synthesis route: stir at 80 C. Lab protocol follows. Dose patient at 10 mg/kg."
    )
    client = TestClient(_hosted_app(tmp_path))
    admin_headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, generated_rationale=unsafe)
    _write_codex_output(tmp_path, output_text=unsafe)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    generated = client.get("/dashboard/projects/workspace-a/runs/run-a/generated")
    codex = client.get("/dashboard/projects/workspace-a/codex")
    combined = generated.text + codex.text

    assert generated.status_code == 200
    assert codex.status_code == 200
    assert "Synthesis route" not in combined
    assert "Lab protocol follows" not in combined
    assert "10 mg/kg" not in combined
    assert "[operational chemistry text redacted]" in combined
    assert "[lab protocol text redacted]" in combined
    assert "[dosing text redacted]" in combined


def _hosted_app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret=_secret(),
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
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


def _create_project_with_run(
    client: TestClient,
    tmp_path: Path,
    headers: dict[str, str],
    *,
    generated_rationale: str = "Computationally generated follow-up hypothesis.",
) -> None:
    created = client.post(
        "/projects",
        json={"workspace_id": "workspace-a", "name": "Research"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    _write_run(tmp_path / "run-a", generated_rationale=generated_rationale)
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    store.register_run_dir(tmp_path / "run-a", run_id="run-a", workspace=workspace)


def _write_run(run_dir: Path, *, generated_rationale: str) -> None:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB"}],
        "candidates": [{"name": "Rasagiline", "origin": "existing", "score": 0.82}],
        "generated_molecule_hypotheses": [
            {
                "name": "Hypothesis-1",
                "origin": "generated",
                "score": 0.41,
                "rationale": generated_rationale,
            }
        ],
        "summary": {"candidate_count": 1, "generated_candidate_count": 1, "target_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")


def _codex_project(tmp_path: Path) -> tuple[PlatformDatabase, Any, ProjectWorkspaceStore]:
    client = TestClient(_hosted_app(tmp_path))
    headers = _login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, headers)
    database = cast(Any, client.app).state.platform_database
    user = database.list_users()[0]
    store = ProjectWorkspaceStore(tmp_path)
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


def _append_workspace_artifact(tmp_path: Path, path: Path, *, artifact_id: str) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    workspace.artifacts.append(_artifact(path, artifact_id=artifact_id))
    store.save(workspace)


def _write_codex_output(tmp_path: Path, *, output_text: str) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    output_dir = tmp_path / ".molecule-ranker" / "codex_project_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "summarize_project-20260101T000000Z.json"
    output_path.write_text(
        json.dumps(
            {
                "task_type": "summarize_project",
                "workspace_id": workspace.workspace_id,
                "status": "succeeded",
                "output_text": output_text,
            }
        )
    )
    workspace.codex_outputs.append(
        {
            "task_type": "summarize_project",
            "status": "succeeded",
            "path": str(output_path),
            "artifact_refs": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    store.save(workspace)


def _artifact_paths(database: PlatformDatabase, *, artifact_id: str) -> list[str]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(artifact_records.c.path).where(
                    artifact_records.c.artifact_id == artifact_id
                )
            )
            .mappings()
            .fetchall()
        )
    return [str(row["path"]) for row in rows]
