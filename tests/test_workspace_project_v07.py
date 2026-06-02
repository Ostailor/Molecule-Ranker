from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig
from molecule_ranker.workspace import ArtifactRegistry, ProjectWorkspaceStore, compare_project_runs


def test_workspace_project_store_registers_runs(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    store = ProjectWorkspaceStore(tmp_path)

    workspace = store.create(workspace_id="parkinson-project", name="Parkinson Project")
    workspace = store.register_run_dir(run_dir, run_id="run-a", workspace=workspace)
    loaded = store.load()

    assert loaded.workspace_id == "parkinson-project"
    assert loaded.name == "Parkinson Project"
    assert loaded.runs[0].run_id == "run-a"
    assert loaded.runs[0].workspace_id == "parkinson-project"
    assert loaded.artifacts
    assert workspace.updated_at <= loaded.updated_at


def test_workspace_artifact_registry_manifest(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    registry = ArtifactRegistry(tmp_path, workspace_id="workspace-a")

    record = registry.register_path(run_dir / "report.md", run_id="run-a")
    manifest = registry.manifest([record])

    assert record.workspace_id == "workspace-a"
    assert record.artifact_type == "report"
    assert record.size_bytes > 0
    assert manifest[0]["artifact_id"] == record.artifact_id
    assert manifest[0]["sha256"] == record.sha256


def test_workspace_run_comparison_reports_overlap(tmp_path: Path) -> None:
    run_a = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    run_b = _write_run(
        tmp_path / "run-b",
        "Parkinson disease",
        [("Rasagiline", 0.77), ("Levodopa", 0.71)],
    )
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    workspace = store.register_run_dir(run_a, run_id="run-a", workspace=workspace)
    workspace = store.register_run_dir(run_b, run_id="run-b", workspace=workspace)

    comparison = compare_project_runs(workspace.runs)

    assert comparison.workspace_id == "workspace-a"
    assert comparison.candidate_overlap == ["Rasagiline"]
    assert comparison.score_deltas[0]["delta"] == 0.05
    assert comparison.run_summaries[0]["artifact_refs"]


def test_workspace_summary_cache_invalidates_after_run_registration(tmp_path: Path) -> None:
    run_a = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    run_b = _write_run(tmp_path / "run-b", "Parkinson disease", [("Levodopa", 0.71)])
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    workspace = store.register_run_dir(run_a, run_id="run-a", workspace=workspace)

    first = store.workspace_summary()
    second = store.workspace_summary()
    workspace = store.register_run_dir(run_b, run_id="run-b", workspace=workspace)
    refreshed = store.workspace_summary()

    assert first["cache_status"] == "miss"
    assert second["cache_status"] == "hit"
    assert refreshed["cache_status"] == "miss"
    assert first["run_count"] == 1
    assert refreshed["run_count"] == 2
    assert refreshed["artifact_count"] > first["artifact_count"]


def test_codex_project_summary_stores_output_separately_with_artifact_refs(
    tmp_path: Path,
) -> None:
    run_dir = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.create(workspace_id="workspace-a")
    store.register_run_dir(run_dir, run_id="run-a", workspace=workspace)

    workspace, result, output_path = store.run_codex_project_task(
        "summarize_project",
        config=CodexBackboneConfig(
            enable_codex_backbone=True,
            codex_working_dir=tmp_path,
            codex_dry_run=True,
        ),
    )

    payload = json.loads(output_path.read_text())
    assert result.status == "succeeded"
    assert output_path.parent.name == "codex_project_outputs"
    assert payload["artifact_refs"]
    assert workspace.codex_outputs
    assert workspace.codex_outputs[0]["artifact_refs"] == payload["artifact_refs"]
    assert all(run.top_candidates for run in workspace.runs)


def test_project_cli_create_run_artifacts_compare_and_codex_summary(tmp_path: Path) -> None:
    run_a = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    run_b = _write_run(tmp_path / "run-b", "Parkinson disease", [("Rasagiline", 0.77)])
    runner = CliRunner()

    create = runner.invoke(
        app,
        [
            "project",
            "create",
            "--root",
            str(tmp_path),
            "--workspace-id",
            "workspace-a",
            "--json",
        ],
    )
    assert create.exit_code == 0, create.stdout
    assert json.loads(create.stdout)["workspace_id"] == "workspace-a"

    for run_id, run_dir in [("run-a", run_a), ("run-b", run_b)]:
        result = runner.invoke(
            app,
            ["project", "run", str(run_dir), "--root", str(tmp_path), "--run-id", run_id],
        )
        assert result.exit_code == 0, result.stdout

    artifacts = runner.invoke(app, ["project", "artifacts", "--root", str(tmp_path), "--json"])
    assert artifacts.exit_code == 0, artifacts.stdout
    assert json.loads(artifacts.stdout)["artifacts"]

    compare = runner.invoke(app, ["project", "compare", "--root", str(tmp_path), "--json"])
    assert compare.exit_code == 0, compare.stdout
    assert json.loads(compare.stdout)["candidate_overlap"] == ["Rasagiline"]

    summary = runner.invoke(
        app,
        [
            "project",
            "summarize",
            "--root",
            str(tmp_path),
            "--use-codex",
            "--mode",
            "dry_run",
            "--json",
        ],
    )
    assert summary.exit_code == 0, summary.stdout
    payload = json.loads(summary.stdout)
    assert payload["status"] == "succeeded"
    assert payload["artifact_refs"]
    assert Path(payload["output_path"]).exists()


def test_project_cli_register_run_updates_dashboard_workspace(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    runner = CliRunner()
    create = runner.invoke(
        app,
        [
            "project",
            "create",
            "--root",
            str(tmp_path),
            "--workspace-id",
            "workspace-a",
            "--json",
        ],
    )
    assert create.exit_code == 0, create.stdout

    registered = runner.invoke(
        app,
        [
            "project",
            "register-run",
            str(run_dir),
            "--root",
            str(tmp_path),
            "--run-id",
            "run-a",
        ],
    )
    workspace = ProjectWorkspaceStore(tmp_path).load()

    assert registered.exit_code == 0, registered.stdout
    assert workspace.runs[0].run_id == "run-a"
    assert workspace.artifacts


def _write_run(run_dir: Path, disease: str, candidates: list[tuple[str, float]]) -> Path:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"input_name": disease, "canonical_name": disease},
        "targets": [
            {
                "symbol": "MAOB",
                "name": "Monoamine oxidase B",
                "disease_relevance_score": 0.8,
            }
        ],
        "candidates": [
            {
                "name": name,
                "origin": "existing",
                "known_targets": ["MAOB"],
                "score": score,
                "score_breakdown": {"confidence": 0.7},
            }
            for name, score in candidates
        ],
        "generated_molecule_hypotheses": [],
        "summary": {
            "target_count": 1,
            "candidate_count": len(candidates),
            "generated_candidate_count": 0,
        },
        "limitations": ["Artifact fixture for tests."],
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")
    (run_dir / "trace.json").write_text(json.dumps({"steps": []}))
    return run_dir
