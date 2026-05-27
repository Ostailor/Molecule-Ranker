from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


class FakeCodexProvider:
    def __init__(self) -> None:
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        now = datetime.now(UTC)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text='{"summary": "ok", "artifact_refs": []}',
            output_json={"summary": "ok", "artifact_refs": task.input_artifact_paths},
            stdout='{"summary": "ok"}',
            stderr="",
            return_code=0,
            artifacts_read=list(task.input_artifact_paths),
            artifacts_written=[],
            commands_observed=[],
            guardrail_warnings=[],
            usage_summary={},
            started_at=now,
            completed_at=now,
            metadata={"fake": True},
        )


def test_api_health(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["local_only"] is True
    assert response.json()["codex_enabled"] is False


def test_codex_endpoint_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))
    task = _task(tmp_path)

    response = client.post("/codex/run-task", json=task.model_dump(mode="json"))

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_codex_endpoint_works_with_mocked_provider(tmp_path: Path) -> None:
    provider = FakeCodexProvider()
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            enable_codex_backbone=True,
            codex_provider=provider,
        )
    )
    task = _task(tmp_path)

    response = client.post("/codex/run-task", json=task.model_dump(mode="json"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["result"]["output_json"]["summary"] == "ok"
    assert provider.tasks[0].task_id == "api-test"


def test_project_codex_summarize_works_with_mocked_provider(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    store.register_run_dir(run_dir, run_id="run-a", workspace=workspace)
    provider = FakeCodexProvider()
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            enable_codex_backbone=True,
            codex_provider=provider,
        )
    )

    response = client.post("/projects/workspace-a/codex/summarize")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert provider.tasks[0].task_type == "summarize_project"


def test_api_does_not_expose_secrets_or_cache_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-1234567890")
    secret_path = tmp_path / ".env"
    cache_path = tmp_path / ".cache" / "artifact.json"
    secret_path.write_text("OPENAI_API_KEY=sk-test-secret-value-1234567890\n")
    cache_path.parent.mkdir()
    cache_path.write_text("{}")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    workspace.artifacts = [
        ArtifactRecord(
            artifact_id="secret-env",
            workspace_id="workspace-a",
            path=str(secret_path),
            artifact_type="secret",
            sha256="x",
            size_bytes=secret_path.stat().st_size,
        ),
        ArtifactRecord(
            artifact_id="cache-artifact",
            workspace_id="workspace-a",
            path=str(cache_path),
            artifact_type="cache",
            sha256="y",
            size_bytes=cache_path.stat().st_size,
        ),
    ]
    store.save(workspace)
    client = TestClient(create_app(root_dir=tmp_path))

    response = client.get("/projects/workspace-a/artifacts")

    assert response.status_code == 200
    raw = response.text
    assert "sk-test-secret-value" not in raw
    assert ".env" not in raw
    assert ".cache" not in raw
    assert response.json()["artifacts"] == []


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path, api_key="local-secret"))

    unauthorized = client.get("/projects")
    authorized = client.get("/projects", headers={"X-API-Key": "local-secret"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def _task(tmp_path: Path) -> CodexTask:
    return CodexTask(
        task_id="api-test",
        task_type="summarize_run",
        prompt="Summarize provided artifacts only.",
        working_directory=str(tmp_path),
        input_artifact_paths=[],
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=30,
        require_json=True,
        metadata={},
    )


def _write_run(run_dir: Path) -> Path:
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
    return run_dir
