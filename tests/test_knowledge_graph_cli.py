from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_graph_cli_help_works() -> None:
    result = CliRunner().invoke(app, ["graph", "--help"])

    assert result.exit_code == 0, result.output
    assert "build" in result.output
    assert "query" in result.output
    assert "dashboard" in result.output


def test_graph_cli_build_query_export_and_dashboard(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-a"
    run_dir.mkdir()
    _write_json(
        run_dir / "candidates.json",
        {
            "project_id": "project-lrrk2",
            "program_id": "program-lrrk2",
            "disease": {"name": "Parkinson disease"},
            "targets": [
                {
                    "symbol": "LRRK2",
                    "evidence": [{"source_record_id": "ot-lrrk2", "confidence": 0.82}],
                }
            ],
            "candidates": [
                {
                    "candidate_id": "cand-lrrk2-1",
                    "name": "LRRK2 Candidate",
                    "known_targets": ["LRRK2"],
                    "mechanism_of_action": "LRRK2 modulation",
                    "score": 0.81,
                    "direct_evidence_available": True,
                }
            ],
        },
    )
    runner = CliRunner()
    graph_path = tmp_path / "graph.json"

    built = runner.invoke(
        app,
        ["graph", "build", "--from-run", str(run_dir), "--output", str(graph_path)],
    )

    assert built.exit_code == 0, built.output
    assert graph_path.exists()
    graph_payload = json.loads(graph_path.read_text())
    assert graph_payload["entities"]
    assert graph_payload["relations"]

    queried = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--graph",
            str(graph_path),
            "--query",
            "candidates_for_target",
            "--target-symbol",
            "LRRK2",
            "--json",
        ],
    )

    assert queried.exit_code == 0, queried.output
    query_payload = json.loads(queried.output)
    assert query_payload[0]["provenance"]
    assert query_payload[0]["relation_refs"][0]["provenance"]

    mechanisms_path = tmp_path / "mechanisms.json"
    mechanisms = runner.invoke(
        app,
        [
            "graph",
            "mechanism",
            "--graph",
            str(graph_path),
            "--disease",
            "Parkinson disease",
            "--output",
            str(mechanisms_path),
        ],
    )
    assert mechanisms.exit_code == 0, mechanisms.output
    assert mechanisms_path.exists()

    contradiction_path = tmp_path / "contradiction_report.json"
    contradictions = runner.invoke(
        app,
        [
            "graph",
            "contradictions",
            "--graph",
            str(graph_path),
            "--output",
            str(contradiction_path),
        ],
    )
    assert contradictions.exit_code == 0, contradictions.output
    assert "contradiction_relations" in contradiction_path.read_text()

    stale_path = tmp_path / "staleness_report.json"
    stale = runner.invoke(
        app,
        ["graph", "stale", "--graph", str(graph_path), "--output", str(stale_path)],
    )
    assert stale.exit_code == 0, stale.output
    assert "stale_relations" in stale_path.read_text()

    recommendations_path = tmp_path / "graph_recommendations.json"
    recommendations = runner.invoke(
        app,
        [
            "graph",
            "recommend",
            "--graph",
            str(graph_path),
            "--project-id",
            "project-lrrk2",
            "--output",
            str(recommendations_path),
        ],
    )
    assert recommendations.exit_code == 0, recommendations.output
    assert recommendations_path.exists()

    export_path = tmp_path / "graph.ttl"
    exported = runner.invoke(
        app,
        [
            "graph",
            "export",
            "--graph",
            str(graph_path),
            "--format",
            "ttl",
            "--output",
            str(export_path),
        ],
    )

    assert exported.exit_code == 0, exported.output
    assert export_path.exists()
    assert "LRRK2" in export_path.read_text()

    dashboard_dir = tmp_path / "graph_dashboard"
    dashboard = runner.invoke(
        app,
        ["graph", "dashboard", "--graph", str(graph_path), "--output", str(dashboard_dir)],
    )

    assert dashboard.exit_code == 0, dashboard.output
    assert dashboard_dir.joinpath("index.html").exists()
    assert "Cross-program knowledge graph" in dashboard_dir.joinpath("index.html").read_text()


def test_graph_cli_build_from_blank_project_creates_empty_graph(tmp_path: Path) -> None:
    graph_path = tmp_path / "blank_graph.json"
    result = CliRunner().invoke(
        app,
        ["graph", "build", "--from-project", str(tmp_path), "--output", str(graph_path)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(graph_path.read_text())
    assert payload["entities"] == []
    assert payload["relations"] == []
    assert payload["metadata"]["warning"] == "No graph source artifacts found for CLI build."


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
