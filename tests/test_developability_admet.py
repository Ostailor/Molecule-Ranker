from __future__ import annotations

from molecule_ranker.developability.admet import ENDPOINTS, predict_rule_based_admet
from molecule_ranker.developability.schemas import ChemistryAlert, PhysChemProfile
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


def _alert(alert_type="toxicophore", severity="medium", name="Aromatic nitro"):
    return ChemistryAlert(
        alert_id="alert-1",
        alert_type=alert_type,  # type: ignore[arg-type]
        alert_name=name,
        severity=severity,  # type: ignore[arg-type]
        matched_smarts="[N+](=O)[O-]",
        description="Computational risk flag.",
        source="test",
        metadata={},
    )


def _by_endpoint(predictions):
    return {prediction.endpoint: prediction for prediction in predictions}


def test_rule_based_admet_emits_all_required_endpoints_and_metadata():
    predictions = predict_rule_based_admet(_profile(), [], "existing")

    assert [prediction.endpoint for prediction in predictions] == ENDPOINTS
    for prediction in predictions:
        assert prediction.prediction_method == "rule_based"
        assert prediction.probability is None
        assert prediction.model_name == "v0.4_rule_based_admet_triage"
        assert "rules_used" in prediction.metadata
        assert "descriptors_used" in prediction.metadata
        assert "alerts_used" in prediction.metadata
        assert "evidence_used" in prediction.metadata
        assert "limitations" in prediction.metadata


def test_high_logp_and_high_mw_increase_risk():
    profile = _profile(
        molecular_weight=650.0,
        logp=6.2,
        tpsa=170.0,
        hbd=6,
        hba=11,
        aromatic_rings=4,
        lipinski_violations=3,
    )

    predictions = _by_endpoint(predict_rule_based_admet(profile, [], "generated"))

    assert predictions["solubility_risk"].risk_level == "high"
    assert predictions["permeability_risk"].risk_level == "high"
    assert predictions["solubility_risk"].metadata["descriptors_used"]
    assert "logp_above_5" in predictions["solubility_risk"].metadata["rules_used"]


def test_toxicophore_alert_increases_toxicity_risk():
    predictions = _by_endpoint(predict_rule_based_admet(_profile(), [_alert()], "existing"))

    assert predictions["ames_mutagenicity_risk"].risk_level in {"medium", "high"}
    assert predictions["general_toxicity_risk"].risk_level in {"medium", "high"}
    assert predictions["ames_mutagenicity_risk"].metadata["alerts_used"] == ["alert-1"]


def test_safety_warning_evidence_increases_risk():
    warning = EvidenceItem(
        source="ChEMBL",
        source_record_id="warn-1",
        title="Drug warning",
        evidence_type="safety_warning",
        summary="Hepatic injury warning with cardiac QT concern.",
        confidence=0.8,
        metadata={"warning_type": "liver and QT warning"},
    )

    predictions = _by_endpoint(
        predict_rule_based_admet(_profile(), [], "existing", warning_evidence=[warning])
    )

    assert predictions["dili_risk"].risk_level in {"medium", "high"}
    assert predictions["herg_liability_risk"].risk_level in {"medium", "high"}
    assert predictions["general_toxicity_risk"].risk_level in {"medium", "high"}
    assert predictions["dili_risk"].metadata["evidence_used"][0]["source_record_id"] == "warn-1"


def test_confidence_remains_conservative():
    profile = _profile(molecular_weight=700.0, logp=7.0, tpsa=190.0, hbd=8, hba=14)

    predictions = predict_rule_based_admet(profile, [_alert(severity="high")], "generated")

    assert all(prediction.confidence <= 0.6 for prediction in predictions)
    assert all(prediction.probability is None for prediction in predictions)
