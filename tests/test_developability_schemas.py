from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.developability.schemas import (
    ADMETPrediction,
    ChemistryAlert,
    DevelopabilityAssessment,
    DevelopabilityRun,
    DockingAssessment,
    PhysChemProfile,
    SynthesizabilityAssessment,
)


def _physchem(**overrides: Any) -> PhysChemProfile:
    payload: dict[str, Any] = {
        "canonical_smiles": "CCO",
        "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "molecular_weight": 46.069,
        "logp": -0.001,
        "tpsa": 20.23,
        "hbd": 1,
        "hba": 1,
        "rotatable_bonds": 0,
        "aromatic_rings": 0,
        "heavy_atom_count": 3,
        "formal_charge": 0,
        "fraction_csp3": 1.0,
        "qed": 0.4,
        "lipinski_violations": 0,
        "veber_violations": 0,
        "ghose_violations": 1,
        "egan_violations": 0,
        "muegge_violations": 1,
        "metadata": {"descriptor_source": "rdkit"},
    }
    payload.update(overrides)
    return PhysChemProfile(**payload)


def _alert(**overrides: Any) -> ChemistryAlert:
    payload: dict[str, Any] = {
        "alert_id": "alert-1",
        "alert_type": "toxicophore",
        "alert_name": "Nitro group",
        "severity": "medium",
        "matched_smarts": "[N+](=O)[O-]",
        "description": "Computational risk flag requiring expert review.",
        "source": "rule_based",
        "metadata": {},
    }
    payload.update(overrides)
    return ChemistryAlert(**payload)


def _admet(**overrides: Any) -> ADMETPrediction:
    payload: dict[str, Any] = {
        "endpoint": "ames",
        "value": True,
        "probability": 0.62,
        "risk_level": "medium",
        "model_name": "rule_based_ames_flag",
        "model_version": "0.4",
        "prediction_method": "rule_based",
        "applicability_domain": "unknown",
        "confidence": 0.55,
        "metadata": {},
    }
    payload.update(overrides)
    return ADMETPrediction(**payload)


def _synth(**overrides: Any) -> SynthesizabilityAssessment:
    payload: dict[str, Any] = {
        "sa_score": 0.72,
        "retrosynthesis_available": False,
        "route_count": None,
        "estimated_complexity": "medium",
        "starting_material_availability": "unknown",
        "risk_level": "medium",
        "method": "heuristic_complexity_score",
        "confidence": 0.5,
        "warnings": ["Heuristic only; requires expert review."],
        "metadata": {"no_routes_returned": True},
    }
    payload.update(overrides)
    return SynthesizabilityAssessment(**payload)


def _docking(**overrides: Any) -> DockingAssessment:
    payload: dict[str, Any] = {
        "enabled": False,
        "target_symbol": "MAOB",
        "structure_source": None,
        "structure_id": None,
        "ligand_id": "CHEMBL_TEST",
        "docking_engine": None,
        "docking_score": None,
        "score_units": None,
        "binding_site_method": None,
        "pose_file": None,
        "confidence": 0.0,
        "warnings": ["Docking not run; no binding claim is made."],
        "metadata": {},
    }
    payload.update(overrides)
    return DockingAssessment(**payload)


def _assessment(**overrides: Any) -> DevelopabilityAssessment:
    payload: dict[str, Any] = {
        "molecule_id": "mol-1",
        "molecule_name": "Example molecule",
        "origin": "existing",
        "canonical_smiles": "CCO",
        "physchem": _physchem(),
        "alerts": [_alert()],
        "admet_predictions": [_admet()],
        "synthesizability": _synth(),
        "docking": [_docking()],
        "overall_developability_score": 0.66,
        "risk_summary": "Computational triage found review-level risk flags.",
        "risk_level": "medium",
        "confidence": 0.58,
        "recommendation": "expert_review_required",
        "warnings": ["Computational triage only."],
        "metadata": {"assessment_policy": "v0.4"},
    }
    payload.update(overrides)
    return DevelopabilityAssessment(**payload)


def test_developability_schema_round_trip_serializes_nested_models():
    run = DevelopabilityRun(
        enabled=True,
        assessed_existing_count=1,
        assessed_generated_count=0,
        retained_count=0,
        deprioritized_count=1,
        rejected_count=0,
        assessments=[_assessment()],
        warnings=["Requires expert review."],
        metadata={"pipeline_stage": "DevelopabilityAssessmentAgent"},
    )

    payload = run.model_dump(mode="json")

    assert payload["assessments"][0]["physchem"]["canonical_smiles"] == "CCO"
    assert payload["assessments"][0]["alerts"][0]["alert_type"] == "toxicophore"
    assert payload["assessments"][0]["admet_predictions"][0]["endpoint"] == "ames"
    assert payload["assessments"][0]["synthesizability"]["retrosynthesis_available"] is False
    assert payload["assessments"][0]["docking"][0]["enabled"] is False


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _physchem(fraction_csp3=1.1), "fraction_csp3"),
        (lambda: _physchem(qed=-0.1), "qed"),
        (lambda: _admet(probability=1.2), "probability"),
        (lambda: _admet(confidence=-0.01), "confidence"),
        (lambda: _synth(sa_score=1.5), "sa_score"),
        (lambda: _synth(confidence=1.2), "confidence"),
        (lambda: _docking(docking_score=-2.0), "docking_score"),
        (lambda: _docking(confidence=1.01), "confidence"),
        (lambda: _assessment(overall_developability_score=1.01), "overall_developability_score"),
        (lambda: _assessment(confidence=-0.1), "confidence"),
    ],
)
def test_score_and_confidence_fields_are_bounded(factory, field_name):
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _alert(alert_type="unsupported"), "alert_type"),
        (lambda: _alert(severity="unknown"), "severity"),
        (lambda: _admet(risk_level="critical"), "risk_level"),
        (lambda: _admet(prediction_method="manual"), "prediction_method"),
        (lambda: _admet(applicability_domain="near_domain"), "applicability_domain"),
        (lambda: _synth(estimated_complexity="trivial"), "estimated_complexity"),
        (
            lambda: _synth(starting_material_availability="available"),
            "starting_material_availability",
        ),
        (lambda: _assessment(origin="natural"), "origin"),
        (lambda: _assessment(risk_level="minimal"), "risk_level"),
        (lambda: _assessment(recommendation="approve"), "recommendation"),
    ],
)
def test_literal_fields_reject_unknown_values(factory, field_name):
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)


def test_synthesizability_rejects_actionable_route_metadata():
    with pytest.raises(ValidationError) as error:
        _synth(metadata={"route_steps": ["not allowed"]})

    assert "actionable routes" in str(error.value)


def test_synthesizability_rejects_procedural_text():
    with pytest.raises(ValidationError) as error:
        _synth(warnings=["Add reagent and stir."])

    assert "synthesis instructions" in str(error.value)
