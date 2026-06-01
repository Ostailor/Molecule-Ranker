from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph


def test_hypothesis_cli_help_works() -> None:
    runner = CliRunner()
    for args in [
        ["hypothesis", "--help"],
        ["hypothesis", "generate", "--help"],
        ["hypothesis", "questions", "--help"],
        ["hypothesis", "gaps", "--help"],
        ["hypothesis", "falsification", "--help"],
        ["hypothesis", "rank", "--help"],
        ["hypothesis", "review", "--help"],
        ["hypothesis", "report", "--help"],
        ["hypothesis", "lifecycle", "--help"],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_hypothesis_cli_generate_from_synthetic_graph(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.json"
    output = tmp_path / "hypotheses.json"
    _write_graph(graph_path)

    result = CliRunner().invoke(
        app,
        [
            "hypothesis",
            "generate",
            "--from-graph",
            str(graph_path),
            "--max-hypotheses",
            "5",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["hypotheses"]
    assert (tmp_path / "hypotheses.sqlite").exists()


def test_hypothesis_cli_questions_contain_no_protocols(tmp_path: Path) -> None:
    hypotheses = _generate_hypotheses(tmp_path)
    output = tmp_path / "research_questions.json"

    result = CliRunner().invoke(
        app,
        [
            "hypothesis",
            "questions",
            "--hypotheses",
            str(hypotheses),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    text = output.read_text().lower()
    assert "questions" in json.loads(output.read_text())
    for forbidden in ["step 1", "10 nm", "30 minutes", "mg/kg"]:
        assert forbidden not in text


def test_hypothesis_cli_review_writes_lifecycle_event(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        graph_path = Path("graph.json")
        hypotheses_path = Path("hypotheses.json")
        _write_graph(graph_path)
        generated = runner.invoke(
            app,
            [
                "hypothesis",
                "generate",
                "--from-graph",
                str(graph_path),
                "--output",
                str(hypotheses_path),
            ],
        )
        assert generated.exit_code == 0, generated.output
        hypothesis_id = json.loads(hypotheses_path.read_text())["hypotheses"][0][
            "hypothesis_id"
        ]
        result = runner.invoke(
            app,
            [
                "hypothesis",
                "review",
                "--hypothesis-id",
                hypothesis_id,
                "--decision",
                "needs_more_evidence",
                "--reviewer-id",
                "reviewer-1",
                "--rationale",
                "Needs more graph-backed context.",
            ],
        )
        lifecycle = runner.invoke(
            app,
            ["hypothesis", "lifecycle", "--hypothesis-id", hypothesis_id, "--json"],
        )

    assert result.exit_code == 0, result.output
    assert lifecycle.exit_code == 0, lifecycle.output
    events = json.loads(lifecycle.output)
    assert any(event["event_type"] == "reviewed" for event in events)
    assert any(event["event_type"] == "updated" for event in events)


def test_hypothesis_cli_report_generated(tmp_path: Path) -> None:
    hypotheses = _generate_hypotheses(tmp_path)
    output = tmp_path / "hypothesis_report.md"

    result = CliRunner().invoke(
        app,
        [
            "hypothesis",
            "report",
            "--hypotheses",
            str(hypotheses),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    report = output.read_text()
    assert "## Hypothesis Summary" in report
    assert "## Testable Research Questions" in report


def _generate_hypotheses(tmp_path: Path) -> Path:
    graph_path = tmp_path / "graph.json"
    hypotheses_path = tmp_path / "hypotheses.json"
    _write_graph(graph_path)
    result = CliRunner().invoke(
        app,
        [
            "hypothesis",
            "generate",
            "--from-graph",
            str(graph_path),
            "--output",
            str(hypotheses_path),
        ],
    )
    assert result.exit_code == 0, result.output
    return hypotheses_path


def _write_graph(path: Path) -> None:
    graph = KnowledgeGraph(
        graph_id="graph-cli-hypothesis",
        entities=[
            _entity("molecule:seed", "molecule", "Seed molecule"),
            _entity(
                "generated_molecule:analog",
                "generated_molecule",
                "Generated analog",
                metadata={"design_score": 0.91, "readiness_score": 0.88},
            ),
        ],
        relations=[
            GraphRelation(
                relation_id="rel:generated-lineage",
                subject_entity_id="generated_molecule:analog",
                predicate="generated_from",
                object_entity_id="molecule:seed",
                relation_type="generated_lineage",
                confidence=0.9,
                source_artifact_ids=["artifact:kg"],
            )
        ],
    )
    path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True))


def _entity(
    entity_id: str,
    entity_type: str,
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> GraphEntity:
    return GraphEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        source_artifact_ids=["artifact:kg"],
        metadata=metadata or {},
    )
