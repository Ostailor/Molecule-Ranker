from __future__ import annotations

from typing import Any, cast

from rdkit import Chem
from rdkit.Chem import QED, Crippen, Descriptors, Lipinski, rdMolDescriptors

from molecule_ranker.developability.schemas import PhysChemProfile

RULE_ASSUMPTIONS: dict[str, Any] = {
    "lipinski": {
        "molecular_weight": "<=500",
        "logp": "<=5",
        "hbd": "<=5",
        "hba": "<=10",
    },
    "veber": {
        "rotatable_bonds": "<=10",
        "tpsa": "<=140",
    },
    "ghose": {
        "molecular_weight": "160-480",
        "logp": "-0.4 to 5.6",
        "heavy_atom_count": "20-70",
        "note": "Molar refractivity is not stored in PhysChemProfile and is not counted.",
    },
    "egan": {
        "logp": "<=5.88",
        "tpsa": "<=131.6",
    },
    "muegge": {
        "molecular_weight": "200-600",
        "logp": "-2 to 5",
        "tpsa": "<=150",
        "hbd": "<=5",
        "hba": "<=10",
        "rotatable_bonds": "<=15",
        "heavy_atom_count": ">=5",
    },
    "interpretation": (
        "Rule-of-five-style filters are coarse triage rules, not hard proof "
        "that a compound can or cannot become a drug."
    ),
    "qed": "QED is a drug-likeness heuristic, not an efficacy or safety prediction.",
}


def compute_physchem_profile(smiles: str) -> PhysChemProfile:
    mol = _mol_from_smiles_or_raise(smiles)
    descriptors = cast(Any, Descriptors)
    crippen = cast(Any, Crippen)
    lipinski = cast(Any, Lipinski)
    rd_descriptors = cast(Any, rdMolDescriptors)

    canonical_smiles = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    inchi_key = _inchi_key(mol)
    profile = PhysChemProfile(
        canonical_smiles=canonical_smiles,
        inchi_key=inchi_key,
        molecular_weight=round(float(descriptors.MolWt(mol)), 3),
        logp=round(float(crippen.MolLogP(mol)), 3),
        tpsa=round(float(descriptors.TPSA(mol)), 3),
        hbd=int(lipinski.NumHDonors(mol)),
        hba=int(lipinski.NumHAcceptors(mol)),
        rotatable_bonds=int(lipinski.NumRotatableBonds(mol)),
        aromatic_rings=int(lipinski.NumAromaticRings(mol)),
        heavy_atom_count=int(lipinski.HeavyAtomCount(mol)),
        formal_charge=int(sum(atom.GetFormalCharge() for atom in mol.GetAtoms())),
        fraction_csp3=round(float(rd_descriptors.CalcFractionCSP3(mol)), 3),
        qed=compute_qed(canonical_smiles),
        lipinski_violations=0,
        veber_violations=0,
        ghose_violations=0,
        egan_violations=0,
        muegge_violations=0,
        metadata={
            "descriptor_source": "rdkit",
            "input_smiles": smiles,
            "assumptions": RULE_ASSUMPTIONS,
        },
    )
    return profile.model_copy(
        update={
            "lipinski_violations": lipinski_violations(profile),
            "veber_violations": veber_violations(profile),
            "ghose_violations": ghose_violations(profile),
            "egan_violations": egan_violations(profile),
            "muegge_violations": muegge_violations(profile),
        }
    )


def lipinski_violations(profile: PhysChemProfile) -> int:
    return sum(
        [
            _gt(profile.molecular_weight, 500.0),
            _gt(profile.logp, 5.0),
            _gt(profile.hbd, 5),
            _gt(profile.hba, 10),
        ]
    )


def veber_violations(profile: PhysChemProfile) -> int:
    return sum(
        [
            _gt(profile.rotatable_bonds, 10),
            _gt(profile.tpsa, 140.0),
        ]
    )


def ghose_violations(profile: PhysChemProfile) -> int:
    return sum(
        [
            _outside(profile.molecular_weight, 160.0, 480.0),
            _outside(profile.logp, -0.4, 5.6),
            _outside(profile.heavy_atom_count, 20, 70),
        ]
    )


def egan_violations(profile: PhysChemProfile) -> int:
    return sum(
        [
            _gt(profile.logp, 5.88),
            _gt(profile.tpsa, 131.6),
        ]
    )


def muegge_violations(profile: PhysChemProfile) -> int:
    return sum(
        [
            _outside(profile.molecular_weight, 200.0, 600.0),
            _outside(profile.logp, -2.0, 5.0),
            _gt(profile.tpsa, 150.0),
            _gt(profile.hbd, 5),
            _gt(profile.hba, 10),
            _gt(profile.rotatable_bonds, 15),
            _lt(profile.heavy_atom_count, 5),
        ]
    )


def compute_qed(smiles: str) -> float | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        Chem.SanitizeMol(mol)
        return round(float(QED.qed(mol)), 3)
    except Exception:
        return None


def descriptor_risk_flags(profile: PhysChemProfile) -> list[str]:
    flags: list[str] = []
    if profile.lipinski_violations:
        flags.append(f"lipinski_violations:{profile.lipinski_violations}")
    if profile.veber_violations:
        flags.append(f"veber_violations:{profile.veber_violations}")
    if profile.ghose_violations:
        flags.append(f"ghose_violations:{profile.ghose_violations}")
    if profile.egan_violations:
        flags.append(f"egan_violations:{profile.egan_violations}")
    if profile.muegge_violations:
        flags.append(f"muegge_violations:{profile.muegge_violations}")
    if _gt(profile.molecular_weight, 700.0):
        flags.append("molecular_weight_extreme_high")
    if _gt(profile.logp, 6.0):
        flags.append("logp_high")
    if _gt(profile.tpsa, 180.0):
        flags.append("tpsa_extreme_high")
    if _gt(profile.rotatable_bonds, 15):
        flags.append("rotatable_bonds_high")
    if profile.qed is not None and profile.qed < 0.2:
        flags.append("qed_low_drug_likeness_heuristic")
    return flags


def _mol_from_smiles_or_raise(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles!r}")
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:
        raise ValueError(f"Invalid SMILES: {smiles!r}") from exc
    return mol


def _inchi_key(mol: Chem.Mol) -> str | None:
    try:
        return Chem.MolToInchiKey(mol) or None
    except Exception:
        return None


def _gt(value: float | int | None, threshold: float | int) -> bool:
    return value is not None and value > threshold


def _lt(value: float | int | None, threshold: float | int) -> bool:
    return value is not None and value < threshold


def _outside(value: float | int | None, lower: float | int, upper: float | int) -> bool:
    return value is not None and (value < lower or value > upper)


__all__ = [
    "compute_physchem_profile",
    "compute_qed",
    "descriptor_risk_flags",
    "egan_violations",
    "ghose_violations",
    "lipinski_violations",
    "muegge_violations",
    "PhysChemProfile",
    "veber_violations",
]
