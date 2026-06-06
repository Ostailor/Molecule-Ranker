from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.v3.result_bundle import V3ResultBundle

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)
runner = CliRunner()


def _bundle_payload() -> dict:
    return {
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
        },
        "candidate_summary": {"section": "candidate_summary"},
        "generated_molecule_summary": {
            "section": "generated_molecule_summary",
            "label": "computational_hypotheses_only",
        },
        "biologics_summary": {"section": "biologics_summary"},
        "evidence_summary": {"section": "evidence_summary"},
        "literature_summary": {"section": "literature_summary"},
        "developability_summary": {"section": "developability_summary"},
        "experimental_evidence_summary": {"section": "experimental_evidence_summary"},
        "model_prediction_summary": {"section": "model_prediction_summary"},
        "structure_summary": {"section": "structure_summary"},
        "graph_summary": {
            "section": "graph_summary",
            "label": "graph_inference_not_graph_fact",
        },
        "hypothesis_summary": {"section": "hypothesis_summary"},
        "portfolio_summary": {"section": "portfolio_summary"},
        "campaign_summary": {"section": "campaign_summary", "activated": False},
        "review_summary": {"section": "review_summary"},
        "evaluation_summary": {
            "section": "evaluation_summary",
            "clinical_validation": False,
        },
        "integration_summary": {"section": "integration_summary", "external_writes": 0},
        "codex_agent_summary": {"section": "codex_agent_summary", "approved_tools_only": True},
        "governance_summary": {"section": "governance_summary"},
        "approval_summary": {"section": "approval_summary"},
        "lineage_summary": {"section": "lineage_summary"},
        "validation_summary": {"section": "validation_summary", "passed": True},
        "limitations": [
            "This bundle is a research-planning result, not biomedical evidence.",
            "This bundle is not clinical validation.",
            "No lab protocol, synthesis, or dosing guidance is provided.",
        ],
        "required_next_human_decisions": ["Review generated hypotheses before advancement."],
        "artifact_manifest": [
            {
                "artifact_id": "candidates",
                "filename": "candidates.json",
                "section": "candidate_summary",
            }
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
            "evidence_sections": ["evidence_summary", "literature_summary"],
            "prediction_sections": ["model_prediction_summary", "structure_summary"],
            "review_sections": ["review_summary"],
            "codex_sections": ["codex_agent_summary"],
            "graph_sections": ["graph_summary"],
            "generated_sections": ["generated_molecule_summary", "hypothesis_summary"],
            "evaluation_sections": ["evaluation_summary"],
        },
    }


def test_v3_result_bundle_has_required_sections_and_disclaimers() -> None:
    bundle = V3ResultBundle.model_validate(_bundle_payload())

    payload = bundle.model_dump(mode="json")
    for section in [
        "evidence_summary",
        "model_prediction_summary",
        "review_summary",
        "codex_agent_summary",
        "graph_summary",
        "generated_molecule_summary",
        "evaluation_summary",
    ]:
        assert section in payload

    limitation_text = " ".join(bundle.limitations).lower()
    assert "research-planning result" in limitation_text
    assert "not biomedical evidence" in limitation_text
    assert "not clinical validation" in limitation_text
    assert "no lab protocol" in limitation_text
    assert "synthesis" in limitation_text
    assert "dosing" in limitation_text


def test_v3_result_bundle_rejects_forbidden_claims() -> None:
    payload = _bundle_payload()
    payload["generated_molecule_summary"] = {
        "claim": "Generated molecule is a validated binder with proven efficacy."
    }

    with pytest.raises(ValidationError, match="forbidden V3 bundle claim"):
        V3ResultBundle.model_validate(payload)


def test_v3_result_bundle_requires_limitations_and_disclaimers() -> None:
    payload = _bundle_payload()
    payload["limitations"] = ["Research planning only."]

    with pytest.raises(ValidationError, match="not clinical validation"):
        V3ResultBundle.model_validate(payload)


def test_discover_writes_v3_bundle_artifacts(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((tmp_path / "v3_result_bundle.json").read_text())
    bundle = V3ResultBundle.model_validate(payload)

    assert bundle.product_version == "3.0.0"
    assert (tmp_path / "v3_result_bundle.md").exists()
    assert (tmp_path / "v3_result_bundle.zip").exists()
    with zipfile.ZipFile(tmp_path / "v3_result_bundle.zip") as archive:
        assert "v3_result_bundle.json" in archive.namelist()
        assert "v3_result_bundle.md" in archive.namelist()
