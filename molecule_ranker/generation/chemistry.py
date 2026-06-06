from __future__ import annotations

from typing import Any, cast

from rdkit import Chem, DataStructs
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdFingerprintGenerator

BASIC_PROPERTY_BOUNDS: dict[str, tuple[float, float]] = {
    "molecular_weight": (150.0, 650.0),
    "logp": (-2.0, 6.0),
    "tpsa": (0.0, 180.0),
    "hbd": (0.0, 8.0),
    "hba": (0.0, 12.0),
    "rotatable_bonds": (0.0, 15.0),
    "heavy_atom_count": (8.0, 70.0),
}

_BASIC_ALERT_SMARTS: dict[str, str] = {
    "nitro_group": "[N+](=O)[O-]",
    "catechol": "c1([OH])c([OH])cccc1",
    "aldehyde": "[CX3H1](=O)[#6]",
    "acid_chloride": "C(=O)Cl",
    "epoxide": "C1OC1",
    "azide": "[$([N-]=[N+]=N),$([N]=[N+]=[N-])]",
    "isocyanate": "N=C=O",
}

_HALOGENS = {"F", "Cl", "Br", "I"}
_HETEROATOMS_WITH_UNSTABLE_HALOGEN_BONDS = {"N", "O", "S", "P"}


def mol_from_smiles(smiles: str) -> Chem.Mol | None:
    """Parse and sanitize a SMILES string with RDKit."""

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None
    return mol


def mol_from_inchi(inchi: str) -> Chem.Mol | None:
    """Parse and sanitize an InChI string with RDKit."""

    try:
        mol = Chem.MolFromInchi(inchi)
    except Exception:
        return None
    if mol is None:
        return None
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None
    return mol


def canonicalize_smiles(smiles: str) -> str | None:
    mol = mol_from_smiles(smiles)
    if mol is None:
        return None
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    return canonical or None


def canonicalize_inchi(inchi: str) -> str | None:
    mol = mol_from_inchi(inchi)
    if mol is None:
        return None
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    return canonical or None


def inchi_key_from_mol(mol: Chem.Mol) -> str | None:
    try:
        inchi_key = Chem.MolToInchiKey(mol)
    except Exception:
        return None
    return inchi_key or None


def descriptors_from_mol(mol: Chem.Mol) -> dict[str, float]:
    descriptors_module = cast(Any, Descriptors)
    crippen_module = cast(Any, Crippen)
    lipinski_module = cast(Any, Lipinski)
    return {
        "molecular_weight": round(float(descriptors_module.MolWt(mol)), 3),
        "logp": round(float(crippen_module.MolLogP(mol)), 3),
        "tpsa": round(float(descriptors_module.TPSA(mol)), 3),
        "hbd": float(lipinski_module.NumHDonors(mol)),
        "hba": float(lipinski_module.NumHAcceptors(mol)),
        "rotatable_bonds": float(lipinski_module.NumRotatableBonds(mol)),
        "aromatic_rings": float(lipinski_module.NumAromaticRings(mol)),
        "heavy_atom_count": float(lipinski_module.HeavyAtomCount(mol)),
        "formal_charge": float(sum(atom.GetFormalCharge() for atom in mol.GetAtoms())),
    }


def morgan_fingerprint(mol: Chem.Mol, radius: int = 2, n_bits: int = 2048):
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
    return generator.GetFingerprint(mol)


def tanimoto_similarity(mol_a: Chem.Mol, mol_b: Chem.Mol) -> float:
    fingerprint_a = morgan_fingerprint(mol_a)
    fingerprint_b = morgan_fingerprint(mol_b)
    return float(DataStructs.TanimotoSimilarity(fingerprint_a, fingerprint_b))


def allowed_elements_check(mol: Chem.Mol, allowed: set[str]) -> bool:
    return all(atom.GetSymbol() in allowed for atom in mol.GetAtoms())


def basic_property_bounds_check(
    descriptors: dict[str, float],
    bounds: dict[str, tuple[float, float]],
) -> list[str]:
    reasons: list[str] = []
    for name, (minimum, maximum) in bounds.items():
        value = descriptors.get(name)
        if value is None:
            reasons.append(f"{name} missing")
            continue
        if value < minimum:
            reasons.append(f"{name} below minimum {minimum}")
        if value > maximum:
            reasons.append(f"{name} above maximum {maximum}")
    return reasons


def detect_basic_alerts(mol: Chem.Mol) -> list[str]:
    alerts: list[str] = []
    for name, smarts in _BASIC_ALERT_SMARTS.items():
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and mol.HasSubstructMatch(pattern):
            alerts.append(name)
    return alerts


def detect_structural_sanity_alerts(mol: Chem.Mol) -> list[str]:
    """Detect generated structures that should not be retained for review."""

    alerts: list[str] = []
    if any(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms()):
        alerts.append("radical_atom_present")

    for bond in mol.GetBonds():
        symbols = {bond.GetBeginAtom().GetSymbol(), bond.GetEndAtom().GetSymbol()}
        if symbols & _HALOGENS and symbols & _HETEROATOMS_WITH_UNSTABLE_HALOGEN_BONDS:
            alerts.append("heteroatom_halogen_bond")
            break

    return alerts
