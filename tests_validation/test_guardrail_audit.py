from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.guardrail_audit import run_guardrail_audit


def test_guardrail_audit_catches_forbidden_phrases(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text(
        "# Report\n\nSyntheticCandidate cures ExampleDisease and includes dosing guidance.\n"
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "fail"
    assert {finding.check_id for finding in report.findings} >= {
        "no_cure_treat_claims",
        "no_dosing_or_patient_treatment_guidance",
    }
    assert (tmp_path / "guardrail_audit.json").exists()
    assert (tmp_path / "guardrail_audit.md").exists()


def test_guardrail_audit_valid_cautious_report_passes(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text(
        "# Cautious Report\n\n"
        "Internal research use only.\n\n"
        "## Limitations\n\n"
        "This synthetic report does not establish treatment benefit, safety, binding, "
        "or biomedical evidence. No dosing, synthesis, or lab protocol content is included.\n"
    )
    (tmp_path / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "C1",
                        "evidence": [
                            {
                                "source": "synthetic_fixture",
                                "source_record_id": "SRC-1",
                                "evidence_type": "literature",
                            }
                        ],
                    }
                ],
                "literature": [
                    {
                        "source": "synthetic_fixture",
                        "source_record_id": "SRC-1",
                        "title": "Synthetic",
                    }
                ],
            }
        )
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "pass"
    assert report.findings == []


def test_guardrail_audit_allows_omitted_lab_protocol_language(tmp_path: Path) -> None:
    (tmp_path / "golden_validation_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "required_checks": [
                    "dossier contains source provenance and limitations",
                    "handoff omits lab protocol content",
                    "report excludes synthesis instructions",
                ],
            }
        )
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "pass"
    assert report.findings == []


def test_guardrail_audit_codex_fabricated_citation_fails(tmp_path: Path) -> None:
    (tmp_path / "codex_backbone.json").write_text(
        json.dumps(
            {
                "artifact_type": "codex_backbone",
                "summary": "Codex says this is supported by PMID:99999999.",
                "results": [{"provider": "NullCodexProvider"}],
            }
        )
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "fail"
    assert any(finding.check_id == "no_fake_citations" for finding in report.findings)
    assert any(finding.category == "Codex output integrity" for finding in report.findings)


def test_guardrail_audit_generated_molecule_overclaim_fails(tmp_path: Path) -> None:
    (tmp_path / "generated_candidates.json").write_text(
        json.dumps(
            {
                "generated": [
                    {
                        "generated_id": "G1",
                        "origin": "generated",
                        "label": "validated active",
                        "evidence": [{"source_record_id": "missing-result"}],
                    }
                ]
            }
        )
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "fail"
    check_ids = {finding.check_id for finding in report.findings}
    assert "no_generated_molecule_called_active" in check_ids
    assert "no_generated_direct_evidence_without_imported_result" in check_ids


def test_validate_guardrails_cli_writes_reports(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("# Report\n\nNo claims; limitations apply.\n")

    result = CliRunner().invoke(app, ["validate", "guardrails", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert (tmp_path / "guardrail_audit.json").exists()
    assert (tmp_path / "guardrail_audit.md").exists()
