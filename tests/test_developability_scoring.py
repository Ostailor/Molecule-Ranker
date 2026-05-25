from __future__ import annotations

from molecule_ranker.developability.schemas import (
    ADMETPrediction,
    ChemistryAlert,
    DockingAssessment,
    PhysChemProfile,
    SynthesizabilityAssessment,
)
from molecule_ranker.developability.scoring import (
    admet_score,
    alert_score,
    physchem_score,
    score_developability,
    structure_score,
    synthesizability_score,
    toxicity_score,
)
from molecule_ranker.schemas import EvidenceItem


def _profile(**overrides):
    payload = {
        "canonical_smiles": "CCO",
        "inchi_key": None,
        "molecular_weight": 250.0,
        "logp": 2.0,
        "tpsa": 60.0,
        "hbd": 1,
        "hba": 3,
        "rotatable_bonds": 4,
        "aromatic_rings": 1,
        "heavy_atom_count": 18,
        "formal_charge": 0,
        "fraction_csp3": 0.5,
        "qed": 0.6,
        "lipinski_violations": 0,
        "veber_violations": 0,
        "ghose_violations": 0,
        "egan_violations": 0,
        "muegge_violations": 0,
        "metadata": {},
    }
    payload.update(overrides)
    return PhysChemProfile(**payload)


def _alert(severity: str = "medium", alert_type: str = "toxicophore") -> ChemistryAlert:
    return ChemistryAlert(
        alert_id=f"{alert_type}-{severity}",
        alert_type=alert_type,  # type: ignore[arg-type]
        alert_name=f"{severity} alert",
        severity=severity,  # type: ignore[arg-type]
        matched_smarts="[N+](=O)[O-]",
        description="Computational risk flag requiring expert review.",
        source="test",
        metadata={},
    )


def _prediction(endpoint: str = "solubility_risk", risk_level: str = "low") -> ADMETPrediction:
    return ADMETPrediction(
        endpoint=endpoint,
        value=None,
        probability=None,
        risk_level=risk_level,  # type: ignore[arg-type]
        model_name="test_rule_model",
        model_version=None,
        prediction_method="rule_based",
        applicability_domain="unknown",
        confidence=0.5,
        metadata={},
    )


def _synth(risk_level: str = "low") -> SynthesizabilityAssessment:
    return SynthesizabilityAssessment(
        sa_score=0.8,
        retrosynthesis_available=False,
        route_count=None,
        estimated_complexity="low" if risk_level == "low" else "high",
        starting_material_availability="unknown",
        risk_level=risk_level,  # type: ignore[arg-type]
        method="descriptor_based_fallback",
        confidence=0.35,
        warnings=[],
        metadata={},
    )


def _disabled_docking() -> DockingAssessment:
    return DockingAssessment(
        enabled=False,
        target_symbol="TEST",
        structure_source=None,
        structure_id=None,
        ligand_id="ligand-1",
        docking_engine=None,
        docking_score=None,
        score_units=None,
        binding_site_method=None,
        pose_file=None,
        confidence=0.0,
        warnings=[],
        metadata={},
    )


def _safety_warning(summary: str = "General safety warning.") -> EvidenceItem:
    return EvidenceItem(
        source="ChEMBL",
        source_record_id="warning-1",
        title="Safety warning",
        evidence_type="safety_warning",
        summary=summary,
        confidence=0.8,
        metadata={"warning_type": "safety"},
    )


def test_component_and_overall_scores_are_bounded():
    profile = _profile()
    alerts = [_alert("low"), _alert("medium")]
    predictions = [
        _prediction("solubility_risk", "low"),
        _prediction("herg_liability_risk", "medium"),
    ]
    synth = _synth()
    docking = [_disabled_docking()]

    component_values = [
        physchem_score(profile),
        admet_score(predictions),
        toxicity_score(alerts, predictions),
        synthesizability_score(synth),
        alert_score(alerts),
        structure_score(docking)[0],
    ]
    assessment = score_developability(
        molecule_id="m1",
        molecule_name="ethanol",
        origin="existing",
        canonical_smiles="CCO",
        physchem=profile,
        alerts=alerts,
        admet_predictions=predictions,
        synthesizability=synth,
        docking=docking,
    )

    assert all(0.0 <= score <= 1.0 for score in component_values)
    assert 0.0 <= assessment.overall_developability_score <= 1.0
    assert 0.0 <= assessment.confidence <= 1.0


def test_critical_alert_triggers_critical_risk():
    assessment = score_developability(
        molecule_id="m1",
        molecule_name="test molecule",
        origin="existing",
        canonical_smiles="CCO",
        physchem=_profile(),
        alerts=[_alert("critical")],
        admet_predictions=[_prediction()],
        synthesizability=_synth(),
        docking=[],
    )

    assert assessment.risk_level == "critical"
    assert assessment.recommendation == "reject"


def test_safety_warning_lowers_score():
    inputs = {
        "molecule_id": "m1",
        "molecule_name": "test molecule",
        "origin": "existing",
        "canonical_smiles": "CCO",
        "physchem": _profile(),
        "alerts": [],
        "admet_predictions": [
            _prediction("herg_liability_risk", "low"),
            _prediction("dili_risk", "low"),
            _prediction("general_toxicity_risk", "low"),
        ],
        "synthesizability": _synth(),
        "docking": [],
    }

    baseline = score_developability(**inputs)
    warned = score_developability(
        **inputs,
        warning_evidence=[
            _safety_warning("Black box warning with fatal hepatic injury concern.")
        ],
    )

    assert warned.overall_developability_score < baseline.overall_developability_score
    assert warned.risk_level == "critical"


def test_generated_molecule_confidence_is_conservative_without_direct_evidence():
    common = {
        "molecule_id": "m1",
        "molecule_name": "generated candidate",
        "canonical_smiles": "CCO",
        "physchem": _profile(),
        "alerts": [],
        "admet_predictions": [_prediction()],
        "synthesizability": _synth(),
        "docking": [],
    }

    existing = score_developability(origin="existing", **common)
    generated = score_developability(origin="generated", **common)

    assert generated.confidence < existing.confidence
    assert generated.confidence <= 0.55
    assert "no direct experimental evidence" in " ".join(generated.warnings).lower()


def test_unavailable_docking_does_not_fail_scoring():
    assessment = score_developability(
        molecule_id="m1",
        molecule_name="test molecule",
        origin="existing",
        canonical_smiles="CCO",
        physchem=_profile(),
        alerts=[],
        admet_predictions=[_prediction()],
        synthesizability=_synth(),
        docking=[],
    )

    assert 0.0 <= assessment.overall_developability_score <= 1.0
    assert assessment.metadata["structure_available"] is False
    assert assessment.metadata["component_scores"]["structure_score"] == 0.5
