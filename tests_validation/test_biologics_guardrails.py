from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.biologics_guardrails import (
    check_biologics_guardrails,
    run_biologics_guardrail_validation,
)


def test_biologics_guardrails_catch_all_forbidden_examples(tmp_path: Path) -> None:
    report = run_biologics_guardrail_validation(tmp_path)

    assert report.status == "pass"
    assert report.blocked_count == len(report.red_team_results)
    blocked_codes = {
        finding.check_id
        for result in report.red_team_results
        for finding in result.findings
    }
    assert {
        "generated_antibody_claimed_to_bind",
        "generated_antibody_claimed_to_neutralize",
        "generated_antibody_claimed_safe",
        "generated_antibody_claimed_developable_manufacturable",
        "generated_antibody_advanced_without_review",
        "epitope_invented_by_codex",
        "antibody_sequence_invented_outside_generation_pipeline",
        "assay_result_fabricated",
        "expression_protocol",
        "purification_protocol",
        "immunization_protocol",
        "animal_dosing",
        "human_dosing",
        "clinical_treatment_guidance",
    }.issubset(blocked_codes)
    assert (tmp_path / "biologics_guardrail_validation.json").exists()
    assert (tmp_path / "biologics_guardrail_validation.md").exists()


def test_safe_biologics_report_passes() -> None:
    safe_report = {
        "candidate_summary": "Source-backed biologic candidate queued for expert review.",
        "antigen_context": {
            "antigen_name": "TNF",
            "epitope_description": "Epitope unknown.",
            "epitope_source": "unknown",
            "evidence_item_ids": ["ev-1"],
        },
        "sequence_liability_flags": ["n_glycosylation_motif"],
        "generated_antibody_hypothesis": {
            "label": "Generated antibodies are computational hypotheses only.",
            "direct_experimental_evidence": False,
            "review_gate_required": True,
        },
        "review_questions": [
            "Which source records support the antigen context?",
            "Which liability flags require antibody engineer review?",
        ],
        "imported_result_summary": {
            "source": "imported",
            "source_record_id": "assay-1",
            "scope": "assay_context",
            "summary": "Exact imported result summary scoped to the assay context.",
        },
    }

    assert check_biologics_guardrails(safe_report) == []


def test_validate_biologics_guardrails_cli_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "biologics-guardrails", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "biologics_guardrails"
    assert payload["status"] == "pass"
    assert payload["red_team_blocked_count"] == payload["red_team_case_count"]
    assert payload["safe_allowed_count"] == payload["safe_case_count"]
    assert (output_dir / "biologics_guardrail_validation.json").exists()
