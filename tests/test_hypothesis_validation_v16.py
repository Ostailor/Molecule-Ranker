from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation import run_hypothesis_validation


def test_validate_hypotheses_cli_help_works() -> None:
    result = CliRunner().invoke(app, ["validate", "hypotheses", "--help"])

    assert result.exit_code == 0, result.output
    assert "V1.6 hypothesis" in result.output


def test_valid_hypothesis_workflow_passes(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "hypotheses", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["hypothesis_count"] > 0
    assert payload["generated_molecule_count"] > 0
    assert payload["evidence_gap_count"] > 0
    assert payload["falsification_criterion_count"] > 0
    assert payload["research_question_count"] > 0
    assert payload["lifecycle_event_count"] > 0
    assert payload["guardrail_audit"]["finding_count"] == 0

    output_dir = tmp_path / ".molecule-ranker" / "validation" / "hypotheses"
    for name in [
        "knowledge_graph.json",
        "hypotheses.json",
        "evidence_gaps.json",
        "falsification_criteria.json",
        "research_questions.json",
        "hypothesis_lifecycle.json",
        "hypothesis_report.md",
        "hypothesis_guardrail_audit.json",
        "hypothesis_validation_report.json",
    ]:
        assert (output_dir / name).exists()
    report = (output_dir / "hypothesis_report.md").read_text(encoding="utf-8")
    assert "## Hypothesis Summary" in report
    assert "Generated molecules remain computational hypotheses" in report
    assert "Generated no-direct-evidence warning" in report


def test_invented_relation_fails(tmp_path: Path) -> None:
    report = run_hypothesis_validation(
        output_dir=tmp_path / "hypotheses",
        fixture="invented_relation",
    )

    assert report.status == "fail"
    messages = _finding_messages(report.guardrail_audit.as_dict())
    assert any("Unknown relation" in message for message in messages)


def test_protocol_text_fails(tmp_path: Path) -> None:
    report = run_hypothesis_validation(
        output_dir=tmp_path / "hypotheses",
        fixture="protocol_text",
    )

    assert report.status == "fail"
    messages = _finding_messages(report.guardrail_audit.as_dict())
    assert any("lab protocols" in message or "procedural" in message for message in messages)


def test_generated_activity_claim_fails(tmp_path: Path) -> None:
    report = run_hypothesis_validation(
        output_dir=tmp_path / "hypotheses",
        fixture="generated_activity_claim",
    )

    assert report.status == "fail"
    messages = _finding_messages(report.guardrail_audit.as_dict())
    assert any("activity" in message or "safety" in message for message in messages)


def _finding_messages(payload: dict[str, object]) -> list[str]:
    findings = payload["findings"]
    assert isinstance(findings, list)
    return [str(item["message"]) for item in findings if isinstance(item, dict)]
