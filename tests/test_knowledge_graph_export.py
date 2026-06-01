from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.knowledge_graph.export import (
    export_graph_csv,
    export_graph_json,
    export_graph_turtle,
)
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph


def test_json_export_sanitizes_and_labels_generated_and_predictions(tmp_path: Path) -> None:
    graph = _export_graph()
    output = export_graph_json(graph, tmp_path / "graph.json")

    payload = json.loads(output.read_text())
    text = output.read_text()

    assert payload["graph_id"] == "kg-export-test"
    assert "super-secret-token" not in text
    assert "cache payload" not in text
    assert "copyrighted article" not in text
    generated = next(
        entity for entity in payload["entities"] if entity["entity_id"] == "generated_molecule:gen1"
    )
    assert generated["metadata"]["computational_hypothesis"] is True
    prediction = next(
        entity for entity in payload["entities"] if entity["entity_id"] == "model_prediction:pred1"
    )
    assert prediction["metadata"]["prediction_not_evidence"] is True


def test_csv_export_writes_nodes_and_edges_with_labels(tmp_path: Path) -> None:
    paths = export_graph_csv(_export_graph(), tmp_path / "csv")

    assert paths["nodes"].exists()
    assert paths["edges"].exists()
    nodes = list(csv.DictReader(paths["nodes"].open()))
    edges = list(csv.DictReader(paths["edges"].open()))

    generated = next(row for row in nodes if row["entity_id"] == "generated_molecule:gen1")
    assert generated["computational_hypothesis"] == "True"
    assert any(row["predicate"] == "hypothesizes" for row in edges)
    assert "super-secret-token" not in paths["nodes"].read_text()
    assert "cache payload" not in paths["edges"].read_text()


def test_ttl_export_uses_stable_iris_provenance_and_labels(tmp_path: Path) -> None:
    output = export_graph_turtle(_export_graph(), tmp_path / "graph.ttl")
    ttl = output.read_text()

    assert "<https://molecule-ranker.local/kg/entity/generated_molecule%3Agen1>" in ttl
    assert 'mr:computationalHypothesis "true"^^xsd:boolean' in ttl
    assert 'mr:predictionNotEvidence "true"^^xsd:boolean' in ttl
    assert 'prov:wasDerivedFrom "artifact:gen"' in ttl
    assert "owl:sameAs" in ttl
    assert "super-secret-token" not in ttl
    assert "copyrighted article" not in ttl


def test_graph_export_cli_writes_requested_format(tmp_path: Path) -> None:
    graph_path = tmp_path / "input-graph.json"
    graph_path.write_text(_export_graph().model_dump_json(), encoding="utf-8")
    output = tmp_path / "cli-graph.ttl"

    result = CliRunner().invoke(
        app,
        [
            "graph",
            "export",
            "--input",
            str(graph_path),
            "--format",
            "ttl",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "mr:computationalHypothesis" in output.read_text()


def _export_graph() -> KnowledgeGraph:
    now = datetime.now(UTC)
    return KnowledgeGraph(
        graph_id="kg-export-test",
        entities=[
            GraphEntity(
                entity_id="generated_molecule:gen1",
                entity_type="generated_molecule",
                name="Generated MAOB 1",
                metadata={
                    "api_token": "super-secret-token",
                    "cache_payload": "cache payload",
                    "full_text": "copyrighted article",
                    "family": "generated-maob",
                },
            ),
            GraphEntity(
                entity_id="model_prediction:pred1",
                entity_type="model_prediction",
                name="Prediction 1",
                metadata={"score": 0.8, "client_secret": "super-secret-token"},
            ),
            GraphEntity(entity_id="target:MAOB", entity_type="target", name="MAOB"),
            GraphEntity(entity_id="molecule:seed", entity_type="molecule", name="Seed"),
        ],
        relations=[
            GraphRelation(
                relation_id="rel-gen-target",
                subject_entity_id="generated_molecule:gen1",
                predicate="hypothesizes",
                object_entity_id="target:MAOB",
                relation_type="inferred",
                confidence=0.6,
                source_artifact_ids=["artifact:gen"],
                source_record_ids=["gen1"],
                created_at=now,
                updated_at=now,
                metadata={"cache_payload": "cache payload"},
            ),
            GraphRelation(
                relation_id="rel-pred",
                subject_entity_id="model_prediction:pred1",
                predicate="predicted_by_model",
                object_entity_id="generated_molecule:gen1",
                relation_type="model_prediction",
                confidence=0.8,
                source_artifact_ids=["artifact:model"],
                source_record_ids=["pred1"],
                created_at=now,
                updated_at=now,
            ),
            GraphRelation(
                relation_id="rel-same",
                subject_entity_id="generated_molecule:gen1",
                predicate="same_as",
                object_entity_id="molecule:seed",
                relation_type="ontology_mapping",
                confidence=0.5,
                source_artifact_ids=["artifact:mapping"],
                source_record_ids=["map1"],
                created_at=now,
                updated_at=now,
            ),
        ],
    )
