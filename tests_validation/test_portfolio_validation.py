from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.portfolio import run_portfolio_validation


def test_golden_portfolio_workflow_passes(tmp_path: Path) -> None:
    report = run_portfolio_validation(output_dir=tmp_path)

    assert report.status == "pass"
    assert "synthetic portfolio candidates built" in report.required_steps
    assert "greedy optimization completed" in report.required_steps
    assert "portfolio artifacts and guardrails validated" in report.required_steps
    assert (tmp_path / "portfolio_candidates.json").exists()
    assert (tmp_path / "portfolio_optimization.json").exists()
    assert (tmp_path / "scenario_analysis.json").exists()
    assert (tmp_path / "portfolio_batch.json").exists()
    assert (tmp_path / "stage_gate_decisions.json").exists()
    assert (tmp_path / "program_decision_memo.md").exists()
    assert (tmp_path / "portfolio_guardrail_audit.json").exists()
    assert not report.guardrail_audit.findings

    candidates = json.loads((tmp_path / "portfolio_candidates.json").read_text())[
        "portfolio_candidates"
    ]
    generated = [candidate for candidate in candidates if candidate["origin"] == "generated"]
    assert generated
    assert all(candidate["generated_without_direct_evidence"] is True for candidate in generated)


def test_validate_portfolio_cli_writes_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "portfolio", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "portfolio"
    assert payload["status"] == "pass"
    assert (output_dir / "portfolio_guardrail_audit.json").exists()
    assert (output_dir / "portfolio_validation_report.md").exists()


def test_fake_evidence_fixture_fails(tmp_path: Path) -> None:
    report = run_portfolio_validation(output_dir=tmp_path, fixture="fake_evidence")

    assert report.status == "fail"
    assert "portfolio_result_not_biomedical_evidence" in _check_ids(report)


def test_generated_high_priority_without_approval_fails_when_policy_forbids(
    tmp_path: Path,
) -> None:
    report = run_portfolio_validation(
        output_dir=tmp_path,
        fixture="generated_without_approval",
    )

    assert report.status == "fail"
    assert "generated_high_priority_requires_approval" in _check_ids(report)


def test_portfolio_protocol_text_fixture_fails(tmp_path: Path) -> None:
    report = run_portfolio_validation(output_dir=tmp_path, fixture="protocol_text")

    assert report.status == "fail"
    assert "no_protocol_synthesis_or_care_details" in _check_ids(report)


def _check_ids(report: object) -> set[str]:
    return {finding.check_id for finding in report.guardrail_audit.findings}  # type: ignore[attr-defined]
