from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from molecule_ranker.developability.schemas import (
    ADMETPrediction,
    ADMETRiskLevel,
    ChemistryAlert,
    PhysChemProfile,
)

MODEL_NAME = "v0.4_rule_based_admet_triage"
MODEL_VERSION = "0.4"
LIMITATIONS = [
    "Rule-based ADMET baseline is computational triage only.",
    "No numeric clinical probabilities are emitted without a real predictive model.",
    "Risk levels are coarse hypotheses and require expert review.",
    "ADMET rules do not prove clinical safety or toxicity.",
]
ENDPOINTS = [
    "solubility_risk",
    "permeability_risk",
    "bbb_penetration_risk",
    "pgp_risk",
    "cyp_interaction_risk",
    "herg_liability_risk",
    "ames_mutagenicity_risk",
    "dili_risk",
    "general_toxicity_risk",
]


def predict_rule_based_admet(
    profile: PhysChemProfile,
    alerts: list[ChemistryAlert],
    origin: Literal["existing", "generated"] | str,
    warning_evidence: Sequence[Any] | None = None,
) -> list[ADMETPrediction]:
    evidence = list(warning_evidence or [])
    context = _Context(
        profile=profile,
        alerts=alerts,
        origin=origin,
        warning_evidence=evidence,
    )
    return [
        _prediction("solubility_risk", *_solubility(context), context),
        _prediction("permeability_risk", *_permeability(context), context),
        _prediction("bbb_penetration_risk", *_bbb(context), context),
        _prediction("pgp_risk", *_pgp(context), context),
        _prediction("cyp_interaction_risk", *_cyp(context), context),
        _prediction("herg_liability_risk", *_herg(context), context),
        _prediction("ames_mutagenicity_risk", *_ames(context), context),
        _prediction("dili_risk", *_dili(context), context),
        _prediction("general_toxicity_risk", *_general_toxicity(context), context),
    ]


def _solubility(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.logp, 5.0):
        rules.append("logp_above_5")
        descriptors.append("logp")
    if _gt(profile.molecular_weight, 500.0):
        rules.append("molecular_weight_above_500")
        descriptors.append("molecular_weight")
    if _gt(profile.aromatic_rings, 3):
        rules.append("aromatic_rings_above_3")
        descriptors.append("aromatic_rings")
    if not rules:
        return (
            "low",
            ["descriptor_ranges_within_solubility_triage_bounds"],
            descriptors,
            "in_domain",
        )
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "in_domain"


def _permeability(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.tpsa, 140.0):
        rules.append("tpsa_above_140")
        descriptors.append("tpsa")
    if _gt(profile.hbd, 5):
        rules.append("hbd_above_5")
        descriptors.append("hbd")
    if _gt(profile.hba, 10):
        rules.append("hba_above_10")
        descriptors.append("hba")
    if _gt(profile.molecular_weight, 500.0):
        rules.append("molecular_weight_above_500")
        descriptors.append("molecular_weight")
    if abs(profile.formal_charge or 0) > 1:
        rules.append("absolute_formal_charge_above_1")
        descriptors.append("formal_charge")
    if not rules:
        return (
            "low",
            ["descriptor_ranges_within_permeability_triage_bounds"],
            descriptors,
            "in_domain",
        )
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "in_domain"


def _bbb(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.tpsa, 90.0):
        rules.append("tpsa_above_90")
        descriptors.append("tpsa")
    if _gt(profile.hbd, 2):
        rules.append("hbd_above_2")
        descriptors.append("hbd")
    if _gt(profile.molecular_weight, 450.0):
        rules.append("molecular_weight_above_450")
        descriptors.append("molecular_weight")
    if (profile.formal_charge or 0) != 0:
        rules.append("nonzero_formal_charge")
        descriptors.append("formal_charge")
    if not rules:
        return "medium", ["low_polarity_neutral_descriptor_proxy"], descriptors, "unknown"
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "in_domain"


def _pgp(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.molecular_weight, 400.0):
        rules.append("molecular_weight_above_400")
        descriptors.append("molecular_weight")
    if _gt(profile.tpsa, 75.0):
        rules.append("tpsa_above_75")
        descriptors.append("tpsa")
    if _gt(profile.hba, 6):
        rules.append("hba_above_6")
        descriptors.append("hba")
    if not rules:
        return "unknown", ["no_clear_pgp_descriptor_proxy"], descriptors, "unknown"
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "unknown"


def _cyp(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.logp, 4.0):
        rules.append("logp_above_4")
        descriptors.append("logp")
    if _gt(profile.aromatic_rings, 2):
        rules.append("aromatic_rings_above_2")
        descriptors.append("aromatic_rings")
    if _alert_count(context.alerts, {"structural_liability", "assay_interference"}) > 0:
        rules.append("structural_liability_or_assay_interference_alert")
    if not rules:
        return "unknown", ["no_clear_cyp_descriptor_proxy"], descriptors, "unknown"
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "unknown"


def _herg(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.logp, 3.5):
        rules.append("logp_above_3_5")
        descriptors.append("logp")
    if _gt(profile.aromatic_rings, 2):
        rules.append("aromatic_rings_above_2")
        descriptors.append("aromatic_rings")
    if _gt(profile.molecular_weight, 450.0):
        rules.append("molecular_weight_above_450")
        descriptors.append("molecular_weight")
    if _has_warning_text(context.warning_evidence, {"herg", "qt", "cardiac"}):
        rules.append("existing_cardiac_warning_evidence")
    if not rules:
        return "unknown", ["no_clear_herg_descriptor_or_warning_proxy"], descriptors, "unknown"
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "unknown"


def _ames(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    toxicophore_count = _alert_count(context.alerts, {"toxicophore"})
    rules = []
    if toxicophore_count:
        rules.append("toxicophore_alert_present")
    if _alert_name_contains(context.alerts, {"nitro", "aniline"}):
        rules.append("mutagenicity_like_alert_name")
    if not rules:
        return "unknown", ["no_ames_specific_alert_proxy"], [], "unknown"
    return _risk_from_count(len(rules)), rules, [], "unknown"


def _dili(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _gt(profile.logp, 3.0) and _gt(profile.molecular_weight, 400.0):
        rules.append("rule_of_two_logp_above_3_and_mw_above_400")
        descriptors.extend(["logp", "molecular_weight"])
    if _alert_count(context.alerts, {"reactive_functionality", "toxicophore"}) > 0:
        rules.append("reactive_or_toxicophore_alert_present")
    if _has_warning_text(context.warning_evidence, {"liver", "hepatic", "dili", "hepatotoxic"}):
        rules.append("existing_liver_warning_evidence")
    if not rules:
        return (
            "unknown",
            ["no_clear_dili_descriptor_alert_or_warning_proxy"],
            descriptors,
            "unknown",
        )
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "unknown"


def _general_toxicity(context: _Context) -> tuple[ADMETRiskLevel, list[str], list[str], str]:
    profile = context.profile
    rules: list[str] = []
    descriptors: list[str] = []
    if _alert_count(
        context.alerts,
        {"toxicophore", "reactive_functionality", "unstable_group", "structural_liability"},
    ):
        rules.append("chemistry_alerts_present")
    if _has_high_or_critical_alert(context.alerts):
        rules.append("high_or_critical_alert_present")
    if _gt(profile.lipinski_violations, 1):
        rules.append("multiple_lipinski_violations")
        descriptors.append("lipinski_violations")
    if context.warning_evidence:
        rules.append("existing_safety_warning_evidence")
    if not rules:
        return "unknown", ["no_general_toxicity_proxy_detected"], descriptors, "unknown"
    return _risk_from_count(len(rules)), rules, sorted(set(descriptors)), "unknown"


def _prediction(
    endpoint: str,
    risk_level: ADMETRiskLevel,
    rules_used: list[str],
    descriptors_used: list[str],
    applicability_domain: str,
    context: _Context,
) -> ADMETPrediction:
    alerts_used = [
        alert.alert_id
        for alert in context.alerts
        if _alert_relevant_to_endpoint(endpoint, alert, rules_used)
    ]
    return ADMETPrediction(
        endpoint=endpoint,
        value=risk_level,
        probability=None,
        risk_level=risk_level,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        prediction_method="rule_based",
        applicability_domain=applicability_domain,  # type: ignore[arg-type]
        confidence=_confidence(risk_level, rules_used),
        metadata={
            "origin": context.origin,
            "rules_used": rules_used,
            "descriptors_used": descriptors_used,
            "alerts_used": alerts_used,
            "evidence_used": _evidence_summary(context.warning_evidence),
            "limitations": LIMITATIONS,
        },
    )


def _risk_from_count(count: int) -> ADMETRiskLevel:
    if count >= 3:
        return "high"
    if count >= 1:
        return "medium"
    return "low"


def _confidence(risk_level: ADMETRiskLevel, rules_used: list[str]) -> float:
    if risk_level == "unknown":
        return 0.2
    return min(0.6, 0.35 + 0.08 * len(rules_used))


def _alert_count(alerts: list[ChemistryAlert], alert_types: set[str]) -> int:
    return sum(1 for alert in alerts if alert.alert_type in alert_types)


def _has_high_or_critical_alert(alerts: list[ChemistryAlert]) -> bool:
    return any(alert.severity in {"high", "critical"} for alert in alerts)


def _alert_name_contains(alerts: list[ChemistryAlert], terms: set[str]) -> bool:
    return any(
        any(term in f"{alert.alert_name} {alert.description}".lower() for term in terms)
        for alert in alerts
    )


def _has_warning_text(evidence: Sequence[Any], terms: set[str]) -> bool:
    return any(any(term in _evidence_text(item) for term in terms) for item in evidence)


def _evidence_text(item: Any) -> str:
    if isinstance(item, str):
        return item.lower()
    if isinstance(item, Mapping):
        values = [str(value) for value in item.values() if value is not None]
        return " ".join(values).lower()
    values = [
        str(getattr(item, field, "") or "")
        for field in ("title", "summary", "evidence_type", "source", "source_record_id")
    ]
    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, Mapping):
        values.extend(str(value) for value in metadata.values() if value is not None)
    return " ".join(values).lower()


def _evidence_summary(evidence: Sequence[Any]) -> list[dict[str, str | None]]:
    summary: list[dict[str, str | None]] = []
    for item in evidence:
        if isinstance(item, str):
            summary.append({"source": "text_warning", "source_record_id": None, "type": item})
        elif isinstance(item, Mapping):
            summary.append(
                {
                    "source": str(item.get("source") or "mapping_warning"),
                    "source_record_id": (
                        str(item.get("source_record_id")) if item.get("source_record_id") else None
                    ),
                    "type": str(item.get("evidence_type") or item.get("warning_type") or ""),
                }
            )
        else:
            summary.append(
                {
                    "source": str(getattr(item, "source", "") or ""),
                    "source_record_id": (
                        str(getattr(item, "source_record_id", "") or "") or None
                    ),
                    "type": str(getattr(item, "evidence_type", "") or ""),
                }
            )
    return summary


def _alert_relevant_to_endpoint(
    endpoint: str,
    alert: ChemistryAlert,
    rules_used: list[str],
) -> bool:
    if not rules_used:
        return False
    if endpoint in {"ames_mutagenicity_risk", "general_toxicity_risk"}:
        return alert.alert_type in {
            "toxicophore",
            "reactive_functionality",
            "unstable_group",
            "structural_liability",
        }
    if endpoint == "dili_risk":
        return alert.alert_type in {"toxicophore", "reactive_functionality"}
    if endpoint == "cyp_interaction_risk":
        return alert.alert_type in {"structural_liability", "assay_interference"}
    return False


def _gt(value: float | int | None, threshold: float | int) -> bool:
    return value is not None and value > threshold


class _Context:
    def __init__(
        self,
        *,
        profile: PhysChemProfile,
        alerts: list[ChemistryAlert],
        origin: str,
        warning_evidence: list[Any],
    ) -> None:
        self.profile = profile
        self.alerts = alerts
        self.origin = origin
        self.warning_evidence = warning_evidence


__all__ = [
    "ADMETPrediction",
    "ENDPOINTS",
    "LIMITATIONS",
    "predict_rule_based_admet",
]
