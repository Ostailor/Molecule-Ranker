from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation import run_campaign_validation


def test_validate_campaign_cli_help_works() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["validate", "campaign", "--help"])

    assert result.exit_code == 0, result.stdout
    assert "V1.7 campaign validation" in result.stdout


def test_valid_campaign_workflow_passes(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["validate", "campaign", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["work_package_count"] == 2
    assert payload["stage_gate_count"] >= 2
    assert payload["replan_trigger_count"] == 1
    assert payload["guardrail_audit"]["finding_count"] == 0

    output_dir = tmp_path / ".molecule-ranker" / "validation" / "campaign"
    assert (output_dir / "campaign_plan.json").exists()
    assert (output_dir / "campaign_memo.md").exists()
    assert (output_dir / "campaign_export.json").exists()

    plan = json.loads((output_dir / "campaign_plan.json").read_text())
    assert plan["human_approval_required"] is True
    assert plan["metadata"]["advisory_plan"] is True
    assert any(gate["gate_type"] == "generated_molecule_review" for gate in plan["stage_gates"])

    replan = json.loads((output_dir / "campaign_replan_triggers.json").read_text())
    assert replan["triggers"][0]["trigger_type"] == "failed_qc"
    assert replan["failed_qc_does_not_create_false_conclusion"] is True

    external = json.loads((output_dir / "external_status_update.json").read_text())
    assert external["metadata"]["does_not_create_assay_evidence"] is True


def test_campaign_validation_protocol_text_fails(tmp_path: Path) -> None:
    report = run_campaign_validation(output_dir=tmp_path, fixture="protocol_text")

    assert report.status == "fail"
    assert any(
        finding.category == "no_procedural_work_package_text"
        for finding in report.guardrail_audit.findings
    )


def test_campaign_validation_generated_without_review_gate_fails(tmp_path: Path) -> None:
    report = run_campaign_validation(output_dir=tmp_path, fixture="generated_no_review_gate")

    assert report.status == "fail"
    assert any(
        finding.category == "generated_molecules_require_review_gate"
        for finding in report.guardrail_audit.findings
    )


def test_campaign_validation_codex_invented_cost_fails(tmp_path: Path) -> None:
    report = run_campaign_validation(output_dir=tmp_path, fixture="codex_invented_cost")

    assert report.status == "fail"
    assert any(
        finding.category == "codex_cannot_invent_costs"
        for finding in report.guardrail_audit.findings
    )
