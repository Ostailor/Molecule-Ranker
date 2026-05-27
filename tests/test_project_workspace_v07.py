from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.project import ProjectWorkspaceStore, compare_project_runs


def test_project_workspace_registers_runs_and_compares_artifacts(tmp_path: Path) -> None:
    run_a = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    run_b = _write_run(
        tmp_path / "run-b",
        "Parkinson disease",
        [("Rasagiline", 0.77), ("Levodopa", 0.71)],
    )
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.register_run_dir(run_a, run_id="run-a")
    workspace = store.register_run_dir(run_b, run_id="run-b", workspace=workspace)

    assert len(workspace.runs) == 2
    assert len(workspace.artifacts) >= 2
    comparison = compare_project_runs(workspace.runs)
    assert comparison.candidate_overlap == ["Rasagiline"]
    assert comparison.score_deltas[0]["candidate_name"] == "Rasagiline"
    assert comparison.score_deltas[0]["delta"] == 0.05
    assert "does not alter scores" in " ".join(comparison.limitations)


def test_project_cli_register_compare_and_dashboard(tmp_path: Path) -> None:
    run_a = _write_run(tmp_path / "run-a", "Parkinson disease", [("Rasagiline", 0.82)])
    run_b = _write_run(tmp_path / "run-b", "Parkinson disease", [("Rasagiline", 0.77)])
    runner = CliRunner()

    for run_id, run_dir in [("run-a", run_a), ("run-b", run_b)]:
        result = runner.invoke(
            app,
            ["project", "register-run", str(run_dir), "--root", str(tmp_path), "--run-id", run_id],
        )
        assert result.exit_code == 0, result.stdout

    compare_result = runner.invoke(
        app,
        ["project", "compare", "--root", str(tmp_path), "--json"],
    )
    assert compare_result.exit_code == 0, compare_result.stdout
    payload = json.loads(compare_result.stdout)
    assert payload["candidate_overlap"] == ["Rasagiline"]

    dashboard_dir = tmp_path / "dashboard"
    dashboard_result = runner.invoke(
        app,
        ["project", "dashboard", "--root", str(tmp_path), "--output-dir", str(dashboard_dir)],
    )
    assert dashboard_result.exit_code == 0, dashboard_result.stdout
    assert (dashboard_dir / "index.html").exists()


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
    return run_dir
