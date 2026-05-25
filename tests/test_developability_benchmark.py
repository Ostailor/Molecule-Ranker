from __future__ import annotations

import json

from molecule_ranker.developability import benchmark as benchmark_module
from molecule_ranker.developability.benchmark import benchmark_developability_file


def _assessment(
    molecule_id: str,
    *,
    origin: str,
    risk_level: str,
    score: float,
    alerts: list[dict],
    admet_predictions: list[dict],
    complexity: str,
    recommendation: str = "retain",
) -> dict:
    return {
        "molecule_id": molecule_id,
        "molecule_name": molecule_id,
        "origin": origin,
        "canonical_smiles": "CCO",
        "physchem": {
            "canonical_smiles": "CCO",
            "molecular_weight": 46.1,
            "logp": -0.1,
            "tpsa": 20.2,
            "hbd": 1,
            "hba": 1,
            "rotatable_bonds": 0,
        },
        "alerts": alerts,
        "admet_predictions": admet_predictions,
        "synthesizability": {
            "estimated_complexity": complexity,
            "risk_level": "medium",
            "method": "descriptor_based_fallback",
            "confidence": 0.35,
        },
        "docking": [],
        "overall_developability_score": score,
        "risk_summary": "computational triage",
        "risk_level": risk_level,
        "confidence": 0.4,
        "recommendation": recommendation,
        "warnings": [],
        "metadata": {},
    }


def test_developability_benchmark_computes_metrics(tmp_path):
    artifact = {
        "success": True,
        "enabled": True,
        "assessed_existing_count": 1,
        "assessed_generated_count": 1,
        "retained_count": 1,
        "deprioritized_count": 0,
        "rejected_count": 0,
        "assessments": [
            _assessment(
                "EXISTING-1",
                origin="existing",
                risk_level="critical",
                score=0.2,
                alerts=[{"severity": "critical", "alert_type": "toxicophore"}],
                admet_predictions=[
                    {"endpoint": "herg_liability_risk", "risk_level": "high"}
                ],
                complexity="high",
                recommendation="expert_review_required",
            ),
            _assessment(
                "GEN-1",
                origin="generated",
                risk_level="low",
                score=0.8,
                alerts=[],
                admet_predictions=[{"endpoint": "solubility_risk", "risk_level": "low"}],
                complexity="low",
            ),
        ],
        "warnings": [],
    }
    path = tmp_path / "developability.json"
    path.write_text(json.dumps(artifact))

    result = benchmark_developability_file(path)

    assert result.assessment_count == 2
    assert result.descriptor_coverage == 1.0
    assert result.alert_rate == 0.5
    assert result.critical_alert_rate == 0.5
    assert result.high_risk_admet_rate == 0.5
    assert result.synthesized_complexity_distribution == {"high": 1, "low": 1}
    assert result.generated_retention_rate_after_developability == 1.0
    assert result.risk_level_distribution == {"critical": 1, "low": 1}
    assert result.endpoint_coverage == {"herg_liability_risk": 1, "solubility_risk": 1}
    assert result.developability_score_distribution.mean == 0.5


def test_developability_benchmark_handles_empty_assessments(tmp_path):
    path = tmp_path / "developability.json"
    path.write_text(json.dumps({"success": True, "enabled": True, "assessments": []}))

    result = benchmark_developability_file(path)

    assert result.assessment_count == 0
    assert result.descriptor_coverage == 0.0
    assert result.alert_rate == 0.0
    assert result.endpoint_coverage == {}
    assert result.developability_score_distribution.mean == 0.0


def test_developability_benchmark_tdc_is_optional(tmp_path, monkeypatch):
    path = tmp_path / "developability.json"
    path.write_text(json.dumps({"success": True, "enabled": True, "assessments": []}))
    monkeypatch.setattr(benchmark_module, "_tdc_available", lambda: False)

    disabled = benchmark_developability_file(path)
    enabled = benchmark_developability_file(path, enable_tdc_benchmark=True)

    assert disabled.tdc_benchmark_enabled is False
    assert disabled.warnings == []
    assert enabled.tdc_benchmark_enabled is True
    assert enabled.tdc_benchmark_available is False
    assert "optional tdc package is not installed" in enabled.warnings[0]
