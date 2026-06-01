from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation import run_graph_validation


def test_graph_validation_passes(tmp_path: Path) -> None:
    report = run_graph_validation(output_dir=tmp_path / "graph-validation")

    assert report.status == "pass"
    assert report.entity_count > 0
    assert report.relation_count > 0
    assert report.mechanism_count > 0
    assert report.contradiction_count > 0
    assert report.stale_relation_count > 0
    assert report.recommendation_count > 0
    assert (report.output_dir / "knowledge_graph.ttl").exists()
    assert (report.output_dir / "dashboard" / "index.html").exists()
    assert report.guardrail_audit.findings == []


def test_validate_graph_cli_passes(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "graph", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["required_steps"][-1] == "graph guardrails verified"


def test_graph_validation_fake_relation_fails(tmp_path: Path) -> None:
    report = run_graph_validation(
        output_dir=tmp_path / "graph-validation",
        fixture="fake_relation",
    )

    assert report.status == "fail"
    assert any(
        finding.check_id == "invented_graph_reference"
        and "rel:invented-validation-fake" in finding.message
        for finding in report.guardrail_audit.findings
    )


def test_graph_validation_overclaim_fails(tmp_path: Path) -> None:
    report = run_graph_validation(
        output_dir=tmp_path / "graph-validation",
        fixture="overclaim",
    )

    check_ids = {finding.check_id for finding in report.guardrail_audit.findings}
    assert report.status == "fail"
    assert "generated_molecule_overclaim" in check_ids
    assert "model_prediction_called_evidence" in check_ids
    assert "review_decision_called_evidence" in check_ids
    assert "synthesis_lab_or_dosing_text" in check_ids


def test_graph_validation_unproven_causality_claim_fails(tmp_path: Path) -> None:
    report = run_graph_validation(
        output_dir=tmp_path / "graph-validation",
        fixture="causality_claim",
    )

    assert report.status == "fail"
    assert any(
        finding.check_id == "graph_path_causality_overclaim"
        for finding in report.guardrail_audit.findings
    )
