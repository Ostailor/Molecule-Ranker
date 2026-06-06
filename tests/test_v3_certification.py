from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.v3.certification import (
    certify_v3_result_bundle,
    write_v3_result_certification,
)
from molecule_ranker.v3.result_bundle import V3ResultBundle

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _bundle() -> V3ResultBundle:
    return V3ResultBundle.model_validate(
        {
            "bundle_id": "v3-bundle-1",
            "product_version": "3.0.0",
            "product_contract_version": "v3.product-contract.1",
            "workflow_id": "workflow-1",
            "project_id": "project-1",
            "disease_name": "Parkinson disease",
            "mode": "mocked",
            "created_at": NOW.isoformat(),
            "executive_summary": {
                "scope": "research planning",
                "not_biomedical_evidence": True,
                "not_clinical_validation": True,
            },
            "candidate_summary": {"count": 1},
            "generated_molecule_summary": {
                "label": "computational_hypotheses_only",
                "advanced_without_review": False,
                "review_required": True,
            },
            "biologics_summary": {
                "antibody_generation_enabled": False,
                "generated_antibodies_advanced_without_review": 0,
                "generated_antibodies_with_direct_evidence": 0,
            },
            "evidence_summary": {"source_backed_items": 1},
            "literature_summary": {"literature_steps_completed": 1},
            "developability_summary": {"section_boundary": "developability_triage"},
            "experimental_evidence_summary": {"validated_records": 0},
            "model_prediction_summary": {
                "section_boundary": "model_predictions_not_evidence",
                "model_predictions_are_separate": True,
            },
            "structure_summary": {
                "section_boundary": "structure_assessments_not_docking_evidence",
                "docking_scores_are_separate": True,
            },
            "graph_summary": {
                "section_boundary": "graph_inference_not_graph_fact",
                "graph_inferences_are_separate": True,
            },
            "hypothesis_summary": {
                "section_boundary": "hypotheses_for_human_review",
                "generated_hypotheses_are_separate": True,
            },
            "portfolio_summary": {"planning_only": True},
            "campaign_summary": {"activated": False, "stage_gate_approved_by_codex": False},
            "review_summary": {"review_steps_completed": 1},
            "evaluation_summary": {
                "section_boundary": "software_workflow_evaluation",
                "evaluation_outputs_are_separate": True,
                "clinical_validation": False,
            },
            "integration_summary": {
                "external_writes_performed": 0,
                "planned_external_writes": 0,
            },
            "codex_agent_summary": {
                "approved_tools_only": True,
                "codex_outputs_are_separate": True,
            },
            "governance_summary": {"external_writes_enabled": False},
            "approval_summary": {"approval_ids": []},
            "lineage_summary": {"lineage_record_count": 1},
            "validation_summary": {
                "passed": True,
                "required_artifacts_present": True,
                "artifact_contracts_valid": True,
                "lineage_complete": True,
                "guardrails_passed": True,
                "approvals_satisfied": True,
                "metadata": {"checks": {"v3_product_contract_valid": True}},
            },
            "limitations": [
                "This bundle is a research-planning result, not biomedical evidence.",
                "This bundle is not clinical validation.",
                "No lab protocol, synthesis, or dosing guidance is provided.",
            ],
            "required_next_human_decisions": [
                "Review generated hypotheses before advancement."
            ],
            "artifact_manifest": [
                {"artifact_id": "candidates", "filename": "candidates.json"},
                {"artifact_id": "lineage", "filename": "e2e_lineage.json"},
                {"artifact_id": "validation", "filename": "e2e_validation.json"},
            ],
            "contract_validation": {
                "product_contract_valid": True,
                "sections_separated": True,
            },
            "guardrail_validation": {
                "forbidden_claims_absent": True,
                "generated_hypotheses_labeled": True,
            },
            "metadata": {
                "product_contract": {
                    "product_name": "molecule-ranker",
                    "product_version": "3.0.0",
                    "product_contract_version": "v3.product-contract.1",
                },
                "evidence_sections": ["evidence_summary", "literature_summary"],
                "prediction_sections": ["model_prediction_summary", "structure_summary"],
                "review_sections": ["review_summary"],
                "codex_sections": ["codex_agent_summary"],
                "graph_sections": ["graph_summary"],
                "generated_sections": [
                    "generated_molecule_summary",
                    "hypothesis_summary",
                ],
                "evaluation_sections": ["evaluation_summary"],
                "reproducibility_manifest": {"lineage_record_count": 1},
                "safety_case_link": "v3_safety_case.md",
            },
        }
    )


def test_valid_mocked_certification_passes(tmp_path: Path) -> None:
    certification = certify_v3_result_bundle(_bundle(), now=lambda: NOW)

    assert certification.certified is True
    assert certification.certification_level == "mocked_validated"
    assert all(certification.checks.values())
    assert "not clinical validation" in " ".join(certification.limitations).lower()

    artifacts = write_v3_result_certification(certification, output_dir=tmp_path)
    assert Path(artifacts["v3_result_certification.json"]).exists()
    assert Path(artifacts["v3_result_certification.md"]).exists()
    payload = json.loads(Path(artifacts["v3_result_certification.json"]).read_text())
    assert payload["certified"] is True


def test_missing_lineage_fails() -> None:
    bundle = _bundle().model_copy(update={"lineage_summary": {"lineage_record_count": 0}})

    certification = certify_v3_result_bundle(bundle, now=lambda: NOW)

    assert certification.certified is False
    assert certification.certification_level == "failed"
    assert certification.checks["lineage_complete"] is False
    assert any("lineage" in finding for finding in certification.findings)


def test_unapproved_external_write_fails() -> None:
    bundle = _bundle().model_copy(
        update={
            "mode": "write_approved_live",
            "integration_summary": {"external_writes_performed": 1},
            "approval_summary": {"approval_ids": []},
            "validation_summary": {
                **_bundle().validation_summary,
                "approvals_satisfied": False,
            },
        }
    )

    certification = certify_v3_result_bundle(bundle, now=lambda: NOW)

    assert certification.certified is False
    assert certification.checks["external_writes_absent_or_approved"] is False
    assert certification.checks["human_approvals_satisfied"] is False


def test_generated_overclaim_fails() -> None:
    bundle = _bundle().model_copy(
        update={
            "generated_molecule_summary": {
                "label": "computational_hypotheses_only",
                "claim": "Generated molecule is a validated binder with proven efficacy.",
            }
        }
    )

    certification = certify_v3_result_bundle(bundle, now=lambda: NOW)

    assert certification.certified is False
    assert certification.checks["no_forbidden_text"] is False
    assert any("forbidden text" in finding for finding in certification.findings)
