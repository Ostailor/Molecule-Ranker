from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.design import (
    run_design_guardrail_audit,
    run_design_validation,
)


def test_design_validation_workflow_passes(tmp_path: Path) -> None:
    report = run_design_validation(output_dir=tmp_path)

    assert report.status == "pass"
    assert (tmp_path / "design_guardrail_audit.json").exists()
    assert (tmp_path / "design_guardrail_audit.md").exists()
    assert (tmp_path / "design_validation_report.json").exists()
    assert "design plan built" in report.required_steps


def test_validate_design_cli_writes_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "design", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "design"
    assert payload["status"] == "pass"
    assert (output_dir / "design_guardrail_audit.json").exists()
    assert (output_dir / "design_guardrail_audit.md").exists()


def test_design_guardrail_fake_evidence_fails(tmp_path: Path) -> None:
    _write_minimal_source_artifacts(tmp_path)
    (tmp_path / "generated_candidates_v2.json").write_text(
        json.dumps(
            {
                "generated_molecules": [
                    {
                        "generated_id": "GEN-1",
                        "origin": "generated",
                        "conditioned_targets": ["SYN1"],
                        "label": "computational_hypothesis",
                        "evidence": [{"source": "generated"}],
                    }
                ]
            }
        )
    )

    report = run_design_guardrail_audit(tmp_path)

    assert report.status == "fail"
    assert {
        "no_invented_evidence",
        "no_generated_direct_evidence_without_exact_imported_result",
    } <= {finding.check_id for finding in report.findings}


def test_design_guardrail_overclaim_fails(tmp_path: Path) -> None:
    _write_minimal_source_artifacts(tmp_path)
    (tmp_path / "generated_report.md").write_text(
        "GEN-1 is active and safe. Use 10 mg/kg in follow-up dosing.\n"
    )

    report = run_design_guardrail_audit(tmp_path)

    check_ids = {finding.check_id for finding in report.findings}
    assert report.status == "fail"
    assert "no_activity_claims" in check_ids
    assert "no_safety_claims" in check_ids
    assert "no_dosing" in check_ids


def test_design_guardrail_unsafe_codex_output_fails(tmp_path: Path) -> None:
    _write_minimal_source_artifacts(tmp_path)
    (tmp_path / "design_plan.json").write_text(
        json.dumps(
            {
                "design_plan_id": "codex-plan",
                "codex_task_result_id": "codex-plan-1",
                "artifact_manifests": [{"path": "missing_artifact.json"}],
                "design_objectives": [{"target_symbol": "FAKE1"}],
            }
        )
    )

    report = run_design_guardrail_audit(tmp_path)

    check_ids = {finding.check_id for finding in report.findings}
    assert report.status == "fail"
    assert "no_codex_plan_with_unsupported_artifacts" in check_ids
    assert "no_invented_targets" in check_ids


def test_design_guardrail_generated_export_as_validated_fails(tmp_path: Path) -> None:
    _write_minimal_source_artifacts(tmp_path)
    (tmp_path / "generated_export.json").write_text(
        json.dumps(
            {
                "export_type": "generated_candidates_v2",
                "records": [
                    {
                        "generated_id": "GEN-1",
                        "origin": "generated",
                        "validated": True,
                        "label": "validated compound",
                    }
                ],
            }
        )
    )

    report = run_design_guardrail_audit(tmp_path)

    assert report.status == "fail"
    assert any(
        finding.check_id == "no_generated_molecule_exported_as_validated_compound"
        for finding in report.findings
    )


def _write_minimal_source_artifacts(path: Path) -> None:
    (path / "candidates.json").write_text(
        json.dumps(
            {
                "targets": [{"symbol": "SYN1", "source_record_id": "target-1"}],
                "candidates": [
                    {
                        "candidate_id": "SEED-1",
                        "known_targets": ["SYN1"],
                        "evidence": [
                            {
                                "source": "synthetic_validation_fixture",
                                "source_record_id": "evidence-1",
                            }
                        ],
                    }
                ],
            }
        )
    )
