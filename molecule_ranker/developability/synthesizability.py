from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any, cast

from rdkit import Chem
from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors

from molecule_ranker.developability.schemas import (
    ADMETRiskLevel,
    ComplexityLevel,
    SynthesizabilityAssessment,
)

FALLBACK_METHOD = "descriptor_based_fallback"
RDKIT_SA_METHOD = "rdkit_contrib_sa_score_normalized"
LIMITATION = (
    "Coarse computational triage only; does not establish practical laboratory feasibility."
)
_ALERT_SMARTS: dict[str, str] = {
    "reactive_acid_halide": "C(=O)[Cl,Br,I]",
    "isocyanate": "N=C=O",
    "azide": "[$([N-]=[N+]=N),$([N]=[N+]=[N-])]",
    "epoxide": "C1OC1",
}


def compute_sa_score(smiles: str) -> float | None:
    mol = _mol_from_smiles_or_raise(smiles)
    raw_score = _rdkit_sa_score(mol)
    if raw_score is None:
        return None
    return round(1.0 - _normalize_raw_sa_score(raw_score), 3)


def compute_complexity_flags(smiles: str) -> list[str]:
    mol = _mol_from_smiles_or_raise(smiles)
    return _complexity_flags(mol)


def assess_synthesizability(
    smiles: str,
    config: Mapping[str, Any] | Any | None = None,
) -> SynthesizabilityAssessment:
    mol = _mol_from_smiles_or_raise(smiles)
    force_fallback = _config_bool(config, "force_descriptor_fallback", False) or _config_bool(
        config, "force_fallback", False
    )
    use_rdkit_sa = _config_bool(config, "use_rdkit_sa_score", True)
    flags = _complexity_flags(mol)
    raw_sa_score = None if force_fallback or not use_rdkit_sa else _rdkit_sa_score(mol)

    if raw_sa_score is None:
        accessibility_score = _fallback_accessibility_score(mol, flags)
        method = FALLBACK_METHOD
        confidence = 0.35
        warnings = [
            "Fallback descriptor complexity heuristic used.",
            "Assessment is coarse computational triage and requires expert review.",
        ]
        metadata = {
            "calculation_source": "rdkit_descriptors",
            "complexity_flags": flags,
            "limitations": [LIMITATION],
            "score_interpretation": "higher_score_means_lower_computed_complexity",
        }
    else:
        accessibility_score = 1.0 - _normalize_raw_sa_score(raw_sa_score)
        method = RDKIT_SA_METHOD
        confidence = 0.55
        warnings = [
            "RDKit contributed SA score is a heuristic and requires expert review.",
            "Assessment is coarse computational triage only.",
        ]
        metadata = {
            "calculation_source": "rdkit_contrib_sa_score",
            "raw_sa_score": round(float(raw_sa_score), 3),
            "raw_sa_score_interpretation": "lower_raw_score_means_lower_computed_complexity",
            "complexity_flags": flags,
            "limitations": [LIMITATION],
            "score_interpretation": "higher_score_means_lower_computed_complexity",
        }

    complexity = _estimated_complexity(accessibility_score, flags)
    risk = _risk_level(complexity)
    return SynthesizabilityAssessment(
        sa_score=round(max(0.0, min(accessibility_score, 1.0)), 3),
        retrosynthesis_available=False,
        route_count=None,
        estimated_complexity=complexity,
        starting_material_availability="unknown",
        risk_level=risk,
        method=method,
        confidence=confidence,
        warnings=warnings,
        metadata=metadata,
    )


def _complexity_flags(mol: Chem.Mol) -> list[str]:
    flags: list[str] = []
    descriptors = _descriptor_snapshot(mol)
    if descriptors["macrocycle_count"] > 0:
        flags.append("macrocycle_present")
    if descriptors["stereocenter_count"] >= 4:
        flags.append("many_stereocenters")
    if descriptors["bridgehead_atoms"] > 0:
        flags.append("bridgehead_atoms_present")
    if descriptors["spiro_atoms"] > 0:
        flags.append("spiro_atoms_present")
    if descriptors["heteroatom_ratio"] > 0.45:
        flags.append("high_heteroatom_burden")
    if descriptors["molecular_weight"] > 600:
        flags.append("high_molecular_weight")
    if descriptors["rotatable_bonds"] > 12:
        flags.append("many_rotatable_bonds")
    for name, smarts in _ALERT_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and mol.HasSubstructMatch(pattern):
            flags.append(f"alert_motif_{name}")
    return sorted(set(flags))


def _fallback_accessibility_score(mol: Chem.Mol, flags: list[str]) -> float:
    descriptors = _descriptor_snapshot(mol)
    penalty = (
        0.08 * descriptors["macrocycle_count"]
        + 0.035 * descriptors["stereocenter_count"]
        + 0.06 * descriptors["bridgehead_atoms"]
        + 0.06 * descriptors["spiro_atoms"]
        + 0.20 * max(descriptors["heteroatom_ratio"] - 0.35, 0.0)
        + 0.0015 * max(descriptors["molecular_weight"] - 350.0, 0.0)
        + 0.018 * max(descriptors["rotatable_bonds"] - 7, 0)
        + 0.05 * sum(1 for flag in flags if flag.startswith("alert_motif_"))
    )
    return round(max(0.0, min(1.0, 0.92 - penalty)), 3)


def _descriptor_snapshot(mol: Chem.Mol) -> dict[str, float | int]:
    descriptors = cast(Any, Descriptors)
    lipinski = cast(Any, Lipinski)
    mol_descriptors = cast(Any, rdMolDescriptors)
    ring_info = mol.GetRingInfo()
    atom_rings = ring_info.AtomRings()
    heavy_atoms = int(lipinski.HeavyAtomCount(mol))
    heteroatoms = sum(
        1 for atom in mol.GetAtoms() if atom.GetAtomicNum() not in {1, 6}
    )
    return {
        "macrocycle_count": sum(1 for ring in atom_rings if len(ring) >= 8),
        "stereocenter_count": len(Chem.FindMolChiralCenters(mol, includeUnassigned=True)),
        "bridgehead_atoms": int(mol_descriptors.CalcNumBridgeheadAtoms(mol)),
        "spiro_atoms": int(mol_descriptors.CalcNumSpiroAtoms(mol)),
        "heteroatom_ratio": heteroatoms / heavy_atoms if heavy_atoms else 0.0,
        "molecular_weight": float(descriptors.MolWt(mol)),
        "rotatable_bonds": int(lipinski.NumRotatableBonds(mol)),
    }


def _estimated_complexity(score: float, flags: list[str]) -> ComplexityLevel:
    if score < 0.35 or any(
        flag in flags for flag in {"macrocycle_present", "many_stereocenters"}
    ):
        return "high"
    if score < 0.65 or flags:
        return "medium"
    return "low"


def _risk_level(complexity: ComplexityLevel) -> ADMETRiskLevel:
    if complexity == "high":
        return "high"
    if complexity == "medium":
        return "medium"
    if complexity == "low":
        return "low"
    return "unknown"


def _rdkit_sa_score(mol: Chem.Mol) -> float | None:
    try:
        sascorer = import_module("rdkit.Contrib.SA_Score.sascorer")
    except Exception:
        return None
    try:
        return float(sascorer.calculateScore(mol))
    except Exception:
        return None


def _normalize_raw_sa_score(raw_score: float) -> float:
    return max(0.0, min((raw_score - 1.0) / 9.0, 1.0))


def _mol_from_smiles_or_raise(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles!r}")
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:
        raise ValueError(f"Invalid SMILES: {smiles!r}") from exc
    return mol


def _config_bool(config: Mapping[str, Any] | Any | None, key: str, default: bool) -> bool:
    if config is None:
        return default
    if isinstance(config, Mapping):
        return bool(config.get(key, default))
    return bool(getattr(config, key, default))


__all__ = [
    "FALLBACK_METHOD",
    "RDKIT_SA_METHOD",
    "SynthesizabilityAssessment",
    "assess_synthesizability",
    "compute_complexity_flags",
    "compute_sa_score",
]
