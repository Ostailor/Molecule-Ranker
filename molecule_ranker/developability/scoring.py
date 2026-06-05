from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

from molecule_ranker.developability.schemas import (
    ADMETPrediction as ScoredADMETPrediction,
)
from molecule_ranker.developability.schemas import (
    ChemistryAlert as ScoredChemistryAlert,
)
from molecule_ranker.developability.schemas import (
    DevelopabilityAssessment as ScoredDevelopabilityAssessment,
)
from molecule_ranker.developability.schemas import (
    DevelopabilityRecommendation,
    DevelopabilityRiskLevel,
    DockingAssessment,
    PhysChemProfile,
    SynthesizabilityAssessment,
)
from molecule_ranker.generation.chemistry import (
    canonicalize_inchi,
    canonicalize_smiles,
    descriptors_from_mol,
    detect_basic_alerts,
    mol_from_smiles,
)
from molecule_ranker.schemas import DevelopabilityAssessment, DevelopabilityFlag

DEVELOPABILITY_LIMITATIONS = [
    "Developability assessment is computational triage only.",
    "ADMET-style property checks are heuristic and do not prove clinical safety.",
    "Toxicity and medicinal chemistry alerts are risk flags requiring expert review.",
    "Synthetic accessibility scores are heuristic and do not prove practical synthesizability.",
    "No synthesis routes, reagents, procedures, temperatures, or protocols are provided.",
]

TOXICITY_ALERT_SMARTS: dict[str, tuple[str, str]] = {
    "aromatic_nitro": ("[N+](=O)[O-]", "Nitro groups can be toxicity-risk flags."),
    "aniline": ("[NX3;H2,H1;!$(NC=O)]c", "Aniline-like motif is a toxicity-risk flag."),
    "hydrazine": ("[NX3][NX3]", "Hydrazine-like motif is a toxicity-risk flag."),
    "alkyl_halide": ("[CX4][Cl,Br,I]", "Alkyl halide motif is a reactivity/toxicity-risk flag."),
    "isocyanate": ("N=C=O", "Isocyanate motif is a reactive toxicity-risk flag."),
}

MED_CHEM_ALERT_SMARTS: dict[str, tuple[str, str]] = {
    "catechol": ("c1([OH])c([OH])cccc1", "Catechol motif can create assay and liability flags."),
    "aldehyde": ("[CX3H1](=O)[#6]", "Aldehyde motif can be a reactive medicinal chemistry alert."),
    "epoxide": ("C1OC1", "Epoxide motif can be a reactive medicinal chemistry alert."),
    "azide": (
        "[$([N-]=[N+]=N),$([N]=[N+]=[N-])]",
        "Azide motif can be a medicinal chemistry alert.",
    ),
    "michael_acceptor": (
        "[C,c]=[C,c]-[C,S,N,P]=[O,S,N]",
        "Michael-acceptor-like motif can be a covalent reactivity flag.",
    ),
}

DEVELOPABILITY_SCORE_WEIGHTS = {
    "physchem_score": 0.25,
    "admet_score": 0.20,
    "toxicity_score": 0.20,
    "synthesizability_score": 0.15,
    "alert_score": 0.10,
    "structure_score": 0.10,
}

RISK_TO_SCORE = {
    "low": 0.85,
    "medium": 0.55,
    "high": 0.25,
    "unknown": 0.50,
}

ALERT_SEVERITY_PENALTY = {
    "low": 0.05,
    "medium": 0.15,
    "high": 0.30,
    "critical": 0.65,
}

TOXICITY_ENDPOINTS = {
    "herg",
    "herg_liability_risk",
    "ames",
    "ames_mutagenicity_risk",
    "dili",
    "dili_risk",
    "general_toxicity_risk",
    "ld50",
}

SEVERE_SAFETY_EVIDENCE_TERMS = (
    "black box",
    "boxed warning",
    "withdrawn",
    "life-threatening",
    "fatal",
    "death",
    "severe",
    "contraindication",
)

SAFETY_EVIDENCE_TERMS = (
    "safety",
    "warning",
    "toxicity",
    "toxic",
    "qt",
    "herg",
    "ames",
    "mutagen",
    "dili",
    "liver injury",
    "hepatic",
    "cardiac",
)


def physchem_score(profile: PhysChemProfile | None) -> float:
    """Score descriptor fit for coarse developability triage.

    This is a heuristic drug-likeness/property score. It is not an efficacy,
    safety, or clinical-success prediction.
    """

    if profile is None:
        return 0.50

    rule_penalty = (
        0.08 * profile.lipinski_violations
        + 0.06 * profile.veber_violations
        + 0.04
        * (
            profile.ghose_violations
            + profile.egan_violations
            + profile.muegge_violations
        )
    )
    descriptor_fit = 1.0 - rule_penalty

    if profile.molecular_weight is not None:
        descriptor_fit -= 0.06 if profile.molecular_weight > 600 else 0.0
        descriptor_fit -= 0.04 if profile.molecular_weight < 120 else 0.0
    if profile.logp is not None:
        descriptor_fit -= 0.08 if profile.logp > 5.5 else 0.0
        descriptor_fit -= 0.04 if profile.logp < -1.0 else 0.0
    if profile.tpsa is not None:
        descriptor_fit -= 0.06 if profile.tpsa > 140 else 0.0
    if profile.rotatable_bonds is not None:
        descriptor_fit -= 0.04 if profile.rotatable_bonds > 10 else 0.0
    if profile.formal_charge is not None:
        descriptor_fit -= 0.04 if abs(profile.formal_charge) > 1 else 0.0

    qed_component = profile.qed if profile.qed is not None else 0.50
    return _clamp01(0.70 * _clamp01(descriptor_fit) + 0.30 * qed_component)


def admet_score(predictions: Sequence[ScoredADMETPrediction] | None) -> float:
    """Score rule/model ADMET outputs by risk labels, not clinical probabilities."""

    if not predictions:
        return 0.50
    return _clamp01(
        sum(RISK_TO_SCORE.get(prediction.risk_level, 0.50) for prediction in predictions)
        / len(predictions)
    )


def toxicity_score(
    alerts: Sequence[ScoredChemistryAlert] | None,
    predictions: Sequence[ScoredADMETPrediction] | None,
    warning_evidence: Sequence[Any] | None = None,
) -> float:
    """Score toxicity risk flags conservatively.

    Alert and endpoint matches are computational risk flags requiring expert
    review; they are not proof of toxicity.
    """

    toxicity_predictions = [
        prediction
        for prediction in predictions or []
        if prediction.endpoint.lower() in TOXICITY_ENDPOINTS
    ]
    base_score = admet_score(toxicity_predictions) if toxicity_predictions else 0.50

    toxic_alerts = [
        alert
        for alert in alerts or []
        if alert.alert_type in {"toxicophore", "reactive_functionality", "structural_liability"}
        or "toxic" in alert.alert_name.lower()
    ]
    penalty = min(0.45, sum(ALERT_SEVERITY_PENALTY[alert.severity] for alert in toxic_alerts))
    if _has_safety_evidence(warning_evidence):
        penalty += 0.15
    if _has_severe_safety_evidence(warning_evidence):
        penalty += 0.25
    return _clamp01(base_score - penalty)


def synthesizability_score(assessment: SynthesizabilityAssessment | None) -> float:
    """Score coarse computational synthesizability triage.

    A higher score is only a developability hypothesis. It does not establish
    that a molecule is practically synthesizable.
    """

    if assessment is None:
        return 0.50

    risk_component = RISK_TO_SCORE.get(assessment.risk_level, 0.50)
    complexity_component = {
        "low": 0.85,
        "medium": 0.55,
        "high": 0.25,
        "unknown": 0.50,
    }.get(assessment.estimated_complexity, 0.50)
    sa_component = assessment.sa_score if assessment.sa_score is not None else complexity_component
    return _clamp01(0.50 * sa_component + 0.30 * complexity_component + 0.20 * risk_component)


def alert_score(alerts: Sequence[ScoredChemistryAlert] | None) -> float:
    """Score medicinal chemistry alert burden, with severe alerts penalized more."""

    if not alerts:
        return 1.0
    penalty = sum(ALERT_SEVERITY_PENALTY[alert.severity] for alert in alerts)
    return _clamp01(1.0 - penalty)


def structure_score(docking: Sequence[DockingAssessment] | None) -> tuple[float, bool, float]:
    """Score optional structure-aware evidence as a weak modifier.

    Docking and structure scores are treated cautiously; they do not prove
    binding or efficacy.
    """

    enabled = [assessment for assessment in docking or [] if assessment.enabled]
    if not enabled:
        return 0.50, False, 0.30

    scored = [assessment for assessment in enabled if assessment.docking_score is not None]
    if not scored:
        confidence = _mean([assessment.confidence for assessment in enabled], default=0.35)
        return 0.50, True, confidence

    weak_scores = [
        0.50 + 0.20 * (cast(float, assessment.docking_score) - 0.50) * assessment.confidence
        for assessment in scored
    ]
    confidence = _mean([assessment.confidence for assessment in scored], default=0.35)
    return _clamp01(_mean(weak_scores, default=0.50)), True, confidence


def score_developability(
    *,
    molecule_id: str,
    molecule_name: str,
    origin: str,
    canonical_smiles: str,
    physchem: PhysChemProfile | None,
    alerts: Sequence[ScoredChemistryAlert] | None = None,
    admet_predictions: Sequence[ScoredADMETPrediction] | None = None,
    synthesizability: SynthesizabilityAssessment | None = None,
    docking: Sequence[DockingAssessment] | None = None,
    warning_evidence: Sequence[Any] | None = None,
    direct_evidence_available: bool = False,
) -> ScoredDevelopabilityAssessment:
    """Build a V0.4 developability assessment from component triage outputs."""

    alert_list = list(alerts or [])
    admet_list = list(admet_predictions or [])
    docking_list = list(docking or [])

    component_scores = {
        "physchem_score": physchem_score(physchem),
        "admet_score": admet_score(admet_list),
        "toxicity_score": toxicity_score(alert_list, admet_list, warning_evidence),
        "synthesizability_score": synthesizability_score(synthesizability),
        "alert_score": alert_score(alert_list),
    }
    (
        component_scores["structure_score"],
        structure_available,
        structure_confidence,
    ) = structure_score(
        docking_list
    )
    overall_score = _clamp01(
        sum(
            DEVELOPABILITY_SCORE_WEIGHTS[name] * score
            for name, score in component_scores.items()
        )
    )

    critical_alert = any(alert.severity == "critical" for alert in alert_list)
    high_alert = any(alert.severity == "high" for alert in alert_list)
    severe_evidence = _has_severe_safety_evidence(warning_evidence)
    risk_level = _risk_level(
        score=overall_score,
        insufficient_structure=physchem is None,
        high_alert=high_alert,
        critical_alert=critical_alert,
        severe_evidence=severe_evidence,
    )
    recommendation = _recommendation(
        risk_level=risk_level,
        score=overall_score,
        high_alert=high_alert,
        critical_alert=critical_alert,
        severe_evidence=severe_evidence,
    )
    confidence = _assessment_confidence(
        origin=origin,
        physchem=physchem,
        admet_predictions=admet_list,
        synthesizability=synthesizability,
        structure_available=structure_available,
        structure_confidence=structure_confidence,
        direct_evidence_available=direct_evidence_available,
    )
    warnings = _assessment_warnings(
        structure_available=structure_available,
        origin=origin,
        direct_evidence_available=direct_evidence_available,
    )

    return ScoredDevelopabilityAssessment(
        molecule_id=molecule_id,
        molecule_name=molecule_name,
        origin="generated" if origin == "generated" else "existing",
        canonical_smiles=canonical_smiles,
        physchem=physchem,
        alerts=alert_list,
        admet_predictions=admet_list,
        synthesizability=synthesizability,
        docking=docking_list,
        overall_developability_score=round(overall_score, 3),
        risk_summary=_risk_summary(risk_level, recommendation),
        risk_level=risk_level,
        confidence=round(confidence, 3),
        recommendation=recommendation,
        warnings=warnings,
        metadata={
            "assessment_policy": "v0.4_developability_weighted_triage",
            "component_scores": {key: round(value, 3) for key, value in component_scores.items()},
            "weights": dict(DEVELOPABILITY_SCORE_WEIGHTS),
            "structure_available": structure_available,
            "safety_evidence_present": _has_safety_evidence(warning_evidence),
            "severe_safety_evidence_present": severe_evidence,
            "limitations": list(DEVELOPABILITY_LIMITATIONS),
        },
    )


def _risk_level(
    *,
    score: float,
    insufficient_structure: bool,
    high_alert: bool,
    critical_alert: bool,
    severe_evidence: bool,
) -> DevelopabilityRiskLevel:
    if insufficient_structure:
        return "unknown"
    if critical_alert or severe_evidence:
        return "critical"
    if high_alert:
        return "high"
    if score >= 0.75:
        return "low"
    if score >= 0.55:
        return "medium"
    return "high"


def _recommendation(
    *,
    risk_level: str,
    score: float,
    high_alert: bool,
    critical_alert: bool,
    severe_evidence: bool,
) -> DevelopabilityRecommendation:
    if risk_level == "unknown":
        return "expert_review_required"
    if risk_level == "critical":
        return "reject" if critical_alert or severe_evidence else "expert_review_required"
    if high_alert:
        return "expert_review_required"
    if risk_level == "high":
        return "deprioritize" if score >= 0.35 else "expert_review_required"
    if risk_level == "medium":
        return "deprioritize"
    return "retain"


def _assessment_confidence(
    *,
    origin: str,
    physchem: PhysChemProfile | None,
    admet_predictions: Sequence[ScoredADMETPrediction],
    synthesizability: SynthesizabilityAssessment | None,
    structure_available: bool,
    structure_confidence: float,
    direct_evidence_available: bool,
) -> float:
    components = [
        0.80 if physchem is not None else 0.20,
        _mean([prediction.confidence for prediction in admet_predictions], default=0.35),
        synthesizability.confidence if synthesizability is not None else 0.35,
        structure_confidence,
    ]
    confidence = _mean(components, default=0.35)
    if not structure_available:
        confidence *= 0.90
    if origin == "generated" and not direct_evidence_available:
        confidence = min(confidence * 0.75, 0.55)
    return _clamp01(confidence)


def _assessment_warnings(
    *,
    structure_available: bool,
    origin: str,
    direct_evidence_available: bool,
) -> list[str]:
    warnings = [
        "High developability score is not a safety claim.",
        "Low developability score does not prove practical impossibility.",
        "Developability assessment is computational triage and requires expert review.",
        "ADMET predictions do not prove clinical safety.",
    ]
    if not structure_available:
        warnings.append(
            "Structure-aware docking assessment was unavailable; structure score used a "
            "neutral conservative default."
        )
    else:
        warnings.append("Docking or structure scores are weak modifiers and do not prove binding.")
    if origin == "generated" and not direct_evidence_available:
        warnings.append(
            "Generated molecule has no direct experimental evidence in this assessment."
        )
    return warnings


def _risk_summary(risk_level: str, recommendation: str) -> str:
    return (
        f"{risk_level} developability risk by computational triage; "
        f"recommendation={recommendation}; requires expert review."
    )


def _has_safety_evidence(warning_evidence: Sequence[Any] | None) -> bool:
    return any(
        _evidence_contains(evidence, SAFETY_EVIDENCE_TERMS)
        for evidence in warning_evidence or []
    )


def _has_severe_safety_evidence(warning_evidence: Sequence[Any] | None) -> bool:
    return any(
        _evidence_contains(evidence, SEVERE_SAFETY_EVIDENCE_TERMS)
        for evidence in warning_evidence or []
    )


def _evidence_contains(evidence: Any, terms: Sequence[str]) -> bool:
    text = _evidence_text(evidence).lower()
    return any(term in text for term in terms)


def _evidence_text(evidence: Any) -> str:
    if isinstance(evidence, Mapping):
        values = evidence.values()
    else:
        values = [
            getattr(evidence, "source", ""),
            getattr(evidence, "source_record_id", ""),
            getattr(evidence, "title", ""),
            getattr(evidence, "evidence_type", ""),
            getattr(evidence, "summary", ""),
            getattr(evidence, "metadata", ""),
        ]
    return " ".join(str(value) for value in values if value is not None)


def _mean(values: Sequence[float], *, default: float) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class DevelopabilityAssessor:
    """Deterministic V0.4 heuristic developability triage."""

    def assess_mapping(
        self,
        *,
        name: str,
        origin: str,
        chemical_metadata: Mapping[str, Any] | None = None,
        identifiers: Mapping[str, Any] | None = None,
        descriptors: Mapping[str, Any] | None = None,
        warnings: list[str] | None = None,
        structure_filter_enabled: bool = False,
        min_developability_score: float = 0.25,
    ) -> DevelopabilityAssessment:
        canonical_smiles = self._canonical_structure(
            chemical_metadata=chemical_metadata or {},
            identifiers=identifiers or {},
            descriptors=descriptors or {},
        )
        if canonical_smiles is None:
            missing_structure = self._flag(
                category="structure_quality",
                severity="medium",
                label="missing_structure",
                description=(
                    "No parseable structure was available, so structure-aware "
                    "developability triage is incomplete."
                ),
            )
            assessment = DevelopabilityAssessment(
                molecule_name=name,
                origin="generated" if origin == "generated" else "existing",
                structure_available=False,
                structure_filter_pass=None,
                developability_score=0.0,
                triage_recommendation="insufficient_structure",
                structure_quality_flags=[missing_structure],
                limitations=list(DEVELOPABILITY_LIMITATIONS),
                metadata={"assessment_policy": "v0.4_heuristic_developability_triage"},
            )
            return assessment

        mol = mol_from_smiles(canonical_smiles)
        if mol is None:
            invalid_structure = self._flag(
                category="structure_quality",
                severity="high",
                label="invalid_structure",
                description=(
                    "A structure string was present but could not be parsed for "
                    "structure-aware developability triage."
                ),
            )
            return DevelopabilityAssessment(
                molecule_name=name,
                origin="generated" if origin == "generated" else "existing",
                structure_available=False,
                canonical_smiles=canonical_smiles,
                structure_quality_flags=[invalid_structure],
                structure_filter_pass=False if structure_filter_enabled else None,
                developability_score=0.0,
                triage_recommendation="insufficient_structure",
                limitations=list(DEVELOPABILITY_LIMITATIONS),
                metadata={"assessment_policy": "v0.4_heuristic_developability_triage"},
            )

        descriptor_payload = descriptors_from_mol(mol)
        descriptor_payload.update(self._numeric_descriptor_overrides(descriptors or {}))
        property_flags = self._admet_property_flags(descriptor_payload)
        toxicity_flags = self._substructure_flags(mol, TOXICITY_ALERT_SMARTS, "toxicity_risk")
        med_chem_flags = self._substructure_flags(
            mol,
            MED_CHEM_ALERT_SMARTS,
            "medicinal_chemistry_alert",
        )
        for alert in detect_basic_alerts(mol):
            if not any(flag.label == alert for flag in med_chem_flags):
                med_chem_flags.append(
                    self._flag(
                        category="medicinal_chemistry_alert",
                        severity="medium",
                        label=alert,
                        description=f"{alert} is a coarse medicinal chemistry risk flag.",
                    )
                )
        liability_flags = self._chemical_liability_flags(descriptor_payload, warnings or [])
        synthetic_accessibility_score = self._synthetic_accessibility_score(
            mol,
            descriptor_payload,
            med_chem_flags,
            toxicity_flags,
        )
        developability_score = self._developability_score(
            property_flags=property_flags,
            toxicity_flags=toxicity_flags,
            med_chem_flags=med_chem_flags,
            liability_flags=liability_flags,
            synthetic_accessibility_score=synthetic_accessibility_score,
        )
        high_flags = [
            flag
            for flag in [*property_flags, *toxicity_flags, *med_chem_flags, *liability_flags]
            if flag.severity == "high"
        ]
        medium_flags = [
            flag
            for flag in [*property_flags, *toxicity_flags, *med_chem_flags, *liability_flags]
            if flag.severity == "medium"
        ]
        if high_flags:
            recommendation = "high_risk_flags"
        elif medium_flags:
            recommendation = "review_flags"
        else:
            recommendation = "favorable_hypothesis"

        structure_filter_pass = None
        if structure_filter_enabled:
            structure_filter_pass = developability_score >= min_developability_score

        return DevelopabilityAssessment(
            molecule_name=name,
            origin="generated" if origin == "generated" else "existing",
            structure_available=True,
            canonical_smiles=canonical_smiles,
            descriptors=descriptor_payload,
            admet_properties=self._admet_properties(descriptor_payload, property_flags),
            admet_property_flags=property_flags,
            toxicity_risk_flags=toxicity_flags,
            medicinal_chemistry_alerts=med_chem_flags,
            synthetic_accessibility_score=synthetic_accessibility_score,
            chemical_liability_flags=liability_flags,
            structure_quality_flags=[],
            structure_filter_pass=structure_filter_pass,
            developability_score=developability_score,
            triage_recommendation=recommendation,
            limitations=list(DEVELOPABILITY_LIMITATIONS),
            metadata={
                "assessment_policy": "v0.4_heuristic_developability_triage",
                "rdkit_based": True,
                "high_flag_count": len(high_flags),
                "medium_flag_count": len(medium_flags),
            },
        )

    def _canonical_structure(
        self,
        *,
        chemical_metadata: Mapping[str, Any],
        identifiers: Mapping[str, Any],
        descriptors: Mapping[str, Any],
    ) -> str | None:
        for source in (chemical_metadata, identifiers, descriptors):
            for field in ("canonical_smiles", "isomeric_smiles", "smiles", "canonical_smile"):
                value = source.get(field)
                if value not in (None, ""):
                    canonical = canonicalize_smiles(str(value))
                    if canonical is not None:
                        return canonical
            for field in ("inchi", "standard_inchi"):
                value = source.get(field)
                if value not in (None, ""):
                    canonical = canonicalize_inchi(str(value))
                    if canonical is not None:
                        return canonical
        return None

    def _admet_property_flags(
        self,
        descriptors: Mapping[str, float],
    ) -> list[DevelopabilityFlag]:
        flags: list[DevelopabilityFlag] = []
        mw = descriptors.get("molecular_weight", 0.0)
        logp = descriptors.get("logp", 0.0)
        tpsa = descriptors.get("tpsa", 0.0)
        hbd = descriptors.get("hbd", 0.0)
        hba = descriptors.get("hba", 0.0)
        rotatable = descriptors.get("rotatable_bonds", 0.0)
        if mw > 500:
            flags.append(self._property_flag("molecular_weight_high", "medium", mw, ">500"))
        if mw < 150:
            flags.append(self._property_flag("molecular_weight_low", "low", mw, "<150"))
        if logp > 5:
            flags.append(self._property_flag("logp_high", "medium", logp, ">5"))
        if logp < -1:
            flags.append(self._property_flag("logp_low", "low", logp, "<-1"))
        if hbd > 5:
            flags.append(self._property_flag("hbd_high", "medium", hbd, ">5"))
        if hba > 10:
            flags.append(self._property_flag("hba_high", "medium", hba, ">10"))
        if tpsa > 140:
            flags.append(self._property_flag("tpsa_high", "medium", tpsa, ">140"))
        if rotatable > 10:
            flags.append(self._property_flag("rotatable_bonds_high", "medium", rotatable, ">10"))
        if self._lipinski_violations(descriptors) >= 2:
            flags.append(
                self._flag(
                    category="admet_property",
                    severity="high",
                    label="multiple_lipinski_flags",
                    description=(
                        "Multiple rule-of-five style property flags were detected; "
                        "this is a computational developability risk flag."
                    ),
                )
            )
        return flags

    def _admet_properties(
        self,
        descriptors: Mapping[str, float],
        property_flags: list[DevelopabilityFlag],
    ) -> dict[str, Any]:
        return {
            "lipinski_violations": self._lipinski_violations(descriptors),
            "veber_flags": self._veber_flags(descriptors),
            "property_flag_count": len(property_flags),
            "admet_note": (
                "Property checks are ADMET-style heuristics for computational triage; "
                "they do not establish clinical safety."
            ),
        }

    def _chemical_liability_flags(
        self,
        descriptors: Mapping[str, float],
        warnings: list[str],
    ) -> list[DevelopabilityFlag]:
        flags: list[DevelopabilityFlag] = []
        charge = abs(descriptors.get("formal_charge", 0.0))
        aromatic_rings = descriptors.get("aromatic_rings", 0.0)
        heavy_atoms = descriptors.get("heavy_atom_count", 0.0)
        if charge > 1:
            flags.append(
                self._flag(
                    category="chemical_liability",
                    severity="medium",
                    label="high_formal_charge",
                    description="High formal charge is a chemical liability risk flag.",
                    metadata={"formal_charge": descriptors.get("formal_charge")},
                )
            )
        if aromatic_rings > 4:
            flags.append(
                self._flag(
                    category="chemical_liability",
                    severity="medium",
                    label="many_aromatic_rings",
                    description="Many aromatic rings can be a developability liability flag.",
                    metadata={"aromatic_rings": aromatic_rings},
                )
            )
        if heavy_atoms > 60:
            flags.append(
                self._flag(
                    category="chemical_liability",
                    severity="medium",
                    label="large_heavy_atom_count",
                    description="Large heavy atom count is a chemical liability risk flag.",
                    metadata={"heavy_atom_count": heavy_atoms},
                )
            )
        if any(
            "black box" in warning.lower() or "boxed" in warning.lower() for warning in warnings
        ):
            flags.append(
                self._flag(
                    category="chemical_liability",
                    severity="high",
                    label="retrieved_serious_warning",
                    description=(
                        "Retrieved warning text indicates a serious risk flag; this "
                        "requires expert review."
                    ),
                )
            )
        return flags

    def _synthetic_accessibility_score(
        self,
        mol: Chem.Mol,
        descriptors: Mapping[str, float],
        med_chem_flags: list[DevelopabilityFlag],
        toxicity_flags: list[DevelopabilityFlag],
    ) -> float:
        heavy_atoms = descriptors.get("heavy_atom_count", 0.0)
        rotatable = descriptors.get("rotatable_bonds", 0.0)
        aromatic_rings = descriptors.get("aromatic_rings", 0.0)
        stereo_centers = len(Chem.FindMolChiralCenters(mol, includeUnassigned=True))
        descriptors_module = cast(Any, Descriptors)
        mol_descriptors = cast(Any, rdMolDescriptors)
        ring_count = float(descriptors_module.RingCount(mol))
        bridgeheads = float(mol_descriptors.CalcNumBridgeheadAtoms(mol))
        spiro = float(mol_descriptors.CalcNumSpiroAtoms(mol))
        penalty = (
            0.004 * max(heavy_atoms - 25, 0)
            + 0.018 * max(rotatable - 6, 0)
            + 0.025 * max(aromatic_rings - 3, 0)
            + 0.030 * stereo_centers
            + 0.025 * max(ring_count - 4, 0)
            + 0.040 * (bridgeheads + spiro)
            + 0.035 * len(med_chem_flags)
            + 0.025 * len(toxicity_flags)
        )
        return round(max(0.0, min(1.0, 0.92 - penalty)), 3)

    def _developability_score(
        self,
        *,
        property_flags: list[DevelopabilityFlag],
        toxicity_flags: list[DevelopabilityFlag],
        med_chem_flags: list[DevelopabilityFlag],
        liability_flags: list[DevelopabilityFlag],
        synthetic_accessibility_score: float,
    ) -> float:
        flags = [*property_flags, *toxicity_flags, *med_chem_flags, *liability_flags]
        severity_penalty = sum(
            {"info": 0.01, "low": 0.03, "medium": 0.08, "high": 0.16}[flag.severity]
            for flag in flags
        )
        score = 0.72 * synthetic_accessibility_score + 0.28 * max(0.0, 1.0 - severity_penalty)
        return round(max(0.0, min(1.0, score)), 3)

    def _substructure_flags(
        self,
        mol: Chem.Mol,
        patterns: Mapping[str, tuple[str, str]],
        category: str,
    ) -> list[DevelopabilityFlag]:
        flags: list[DevelopabilityFlag] = []
        for label, (smarts, description) in patterns.items():
            pattern = Chem.MolFromSmarts(smarts)
            if pattern is not None and mol.HasSubstructMatch(pattern):
                flags.append(
                    self._flag(
                        category=category,
                        severity="medium",
                        label=label,
                        description=description,
                    )
                )
        return flags

    def _property_flag(
        self,
        label: str,
        severity: str,
        value: float,
        threshold: str,
    ) -> DevelopabilityFlag:
        return self._flag(
            category="admet_property",
            severity=severity,
            label=label,
            description=(
                f"{label} ({value:.3g}) crossed heuristic threshold {threshold}; "
                "this is an ADMET-style risk flag, not a safety conclusion."
            ),
            metadata={"value": value, "threshold": threshold},
        )

    def _flag(
        self,
        *,
        category: str,
        severity: str,
        label: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> DevelopabilityFlag:
        return DevelopabilityFlag(
            category=category,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            label=label,
            description=description,
            metadata=metadata or {},
        )

    def _lipinski_violations(self, descriptors: Mapping[str, float]) -> int:
        return sum(
            [
                descriptors.get("molecular_weight", 0.0) > 500,
                descriptors.get("logp", 0.0) > 5,
                descriptors.get("hbd", 0.0) > 5,
                descriptors.get("hba", 0.0) > 10,
            ]
        )

    def _veber_flags(self, descriptors: Mapping[str, float]) -> int:
        return sum(
            [
                descriptors.get("rotatable_bonds", 0.0) > 10,
                descriptors.get("tpsa", 0.0) > 140,
            ]
        )

    def _numeric_descriptor_overrides(
        self,
        descriptors: Mapping[str, Any],
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for key, value in descriptors.items():
            if isinstance(value, (int, float)):
                values[str(key)] = float(value)
        return values
