from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.autonomy_validation.residual_risk import (
    RESIDUAL_RISK_JSON,
    RESIDUAL_RISK_MARKDOWN,
    build_default_residual_risk_register,
    render_residual_risk_register_markdown,
    validate_residual_risk_register,
    write_residual_risk_register,
)
from molecule_ranker.autonomy_validation.schemas import ResidualRisk

NOW = datetime(2026, 6, 6, tzinfo=UTC)


def test_default_residual_risk_register_generated() -> None:
    register = build_default_residual_risk_register(now=lambda: NOW)

    assert len(register.risks) == 15
    assert {risk.risk_type for risk in register.risks} == {
        "scientific_overclaim",
        "generated_molecule_antibody_misuse",
        "codex_prompt_injection",
        "external_integration_misconfiguration",
        "data_provenance_loss",
        "assay_result_mislinking",
        "failed_qc_misinterpretation",
        "overreliance_on_docking_model_predictions",
        "incomplete_live_data",
        "credential_secret_exposure",
        "excessive_autonomy",
        "user_misunderstanding",
        "dashboard_misinterpretation",
        "model_calibration_limitations",
        "biologics_sequence_uncertainty",
    }
    assert all(risk.mitigation for risk in register.risks)


def test_high_risk_requires_mitigation() -> None:
    risk = ResidualRisk(
        risk_id="risk-high-no-mitigation",
        risk_type="scientific_overclaim",
        description="A high risk without mitigation is invalid.",
        severity="high",
        likelihood="possible",
        mitigation="",
        owner_role="governance_owner",
        status="open",
        metadata={},
    )

    with pytest.raises(ValueError, match="requires mitigation"):
        validate_residual_risk_register([risk])


def test_accepted_risks_require_owner_and_rationale() -> None:
    missing_rationale = ResidualRisk(
        risk_id="risk-accepted-no-rationale",
        risk_type="incomplete_live_data",
        description="Accepted risk without rationale.",
        severity="medium",
        likelihood="possible",
        mitigation="Track caveats.",
        owner_role="platform_owner",
        status="accepted",
        metadata={},
    )
    accepted = missing_rationale.model_copy(
        update={"metadata": {"acceptance_rationale": "Known fixture limitation."}}
    )

    with pytest.raises(ValueError, match="acceptance_rationale"):
        validate_residual_risk_register([missing_rationale])
    validate_residual_risk_register([accepted])


def test_write_residual_risk_register_outputs_json_and_markdown(tmp_path: Path) -> None:
    register = write_residual_risk_register(tmp_path, now=lambda: NOW)

    json_path = tmp_path / RESIDUAL_RISK_JSON
    markdown_path = tmp_path / RESIDUAL_RISK_MARKDOWN
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["register_id"] == register.register_id
    assert payload["metadata"]["output_files"] == [
        RESIDUAL_RISK_JSON,
        RESIDUAL_RISK_MARKDOWN,
    ]
    markdown = render_residual_risk_register_markdown(register)
    assert markdown.startswith("# Residual Risk Register")
    assert "not clinical validation" in markdown
