from __future__ import annotations

import hashlib
from typing import Any, Protocol

from rdkit import Chem

from molecule_ranker.generation.chemistry import (
    BASIC_PROPERTY_BOUNDS,
    allowed_elements_check,
    basic_property_bounds_check,
    canonicalize_smiles,
    descriptors_from_mol,
    detect_basic_alerts,
    inchi_key_from_mol,
    mol_from_smiles,
)
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)


class MolecularGenerator(Protocol):
    name: str
    version: str

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> list[GeneratedMolecule]:
        """Generate molecule hypotheses from evidence-backed seed molecules."""
        ...


def seed_id(seed: SeedMolecule) -> str:
    for key in ("chembl", "pubchem_cid", "cid", "inchikey", "name"):
        value = seed.identifiers.get(key)
        if value:
            return str(value)
    return seed.name


def build_generated_molecule(
    *,
    generator_name: str,
    generator_version: str,
    objective: GenerationObjective,
    seed: SeedMolecule,
    smiles: str,
    generation_round: int,
    output_index: int,
    transformation_metadata: dict[str, Any],
    warnings: list[str] | None = None,
) -> GeneratedMolecule:
    canonical_smiles = canonicalize_smiles(smiles)
    if canonical_smiles is None:
        raise ValueError(f"Generator {generator_name} produced invalid SMILES.")
    mol = mol_from_smiles(canonical_smiles)
    if mol is None:
        raise ValueError(f"Generator {generator_name} produced unsanitizable SMILES.")

    descriptors = descriptors_from_mol(mol)
    allowed_elements = set(["C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"])
    allowed_elements_ok = allowed_elements_check(mol, allowed_elements)
    rejection_reasons = basic_property_bounds_check(descriptors, BASIC_PROPERTY_BOUNDS)
    if not allowed_elements_ok:
        rejection_reasons.append("contains disallowed element")
    alerts = detect_basic_alerts(mol)
    parent_seed_id = seed_id(seed)
    generated_id = _generated_id(
        objective_id=objective.objective_id,
        generator_name=generator_name,
        canonical_smiles=canonical_smiles,
        output_index=output_index,
    )
    merged_warnings: list[str] = sorted(
        {
            "in_silico_hypothesis_only",
            "no_synthesis_route_provided",
            *(str(warning) for warning in (warnings or [])),
            *(["coarse_property_bounds_warning"] if rejection_reasons else []),
            *(["basic_alerts_present"] if alerts else []),
        }
    )
    provenance = {
        "generator_name": generator_name,
        "generator_version": generator_version,
        "generated_id": generated_id,
        "parent_seed_ids": [parent_seed_id],
        "transformation_metadata": transformation_metadata,
        "warnings": merged_warnings,
    }
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles=smiles,
        canonical_smiles=canonical_smiles,
        inchi_key=inchi_key_from_mol(mol),
        generation_method=generator_name,
        parent_seed_ids=[parent_seed_id],
        conditioned_targets=[objective.target_symbol],
        objective_id=objective.objective_id,
        generation_round=generation_round,
        descriptors=descriptors,
        fingerprints={
            "morgan": {
                "radius": 2,
                "n_bits": 2048,
                "representation": "rdkit_explicit_bit_vector_not_serialized",
            }
        },
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=allowed_elements_ok,
            descriptor_bounds_ok=not rejection_reasons,
            pains_or_alerts=alerts,
            rejection_reasons=rejection_reasons,
            metadata={
                "bounds": BASIC_PROPERTY_BOUNDS,
                "allowed_elements": sorted(allowed_elements),
            },
        ),
        warnings=merged_warnings,
        metadata={
            "generator": generator_name,
            "generator_name": generator_name,
            "generator_version": generator_version,
            "parent_seed_names": [seed.name],
            "source_seed_smiles": [seed.canonical_smiles],
            "transformation_metadata": transformation_metadata,
            "generator_provenance": [provenance],
            "hypothesis_only": True,
            "no_imported_evidence": True,
            "no_synthesis_planning": True,
        },
    )


def attach_atom_to_first_available_atom(smiles: str, atom_symbol: str) -> str | None:
    mol = mol_from_smiles(smiles)
    if mol is None:
        return None
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 1:
            continue
        if atom.GetFormalCharge() != 0:
            continue
        if atom.GetTotalNumHs() <= 0:
            continue
        rw_mol = Chem.RWMol(mol)
        new_atom_idx = rw_mol.AddAtom(Chem.Atom(atom_symbol))
        rw_mol.AddBond(atom.GetIdx(), new_atom_idx, Chem.BondType.SINGLE)
        candidate = rw_mol.GetMol()
        try:
            Chem.SanitizeMol(candidate)
        except Exception:
            continue
        canonical = Chem.MolToSmiles(candidate, canonical=True, isomericSmiles=True)
        if canonical:
            return canonical
    return None


def _generated_id(
    *,
    objective_id: str,
    generator_name: str,
    canonical_smiles: str,
    output_index: int,
) -> str:
    digest = hashlib.sha1(canonical_smiles.encode("utf-8")).hexdigest()[:10]
    return f"{objective_id}:{generator_name}:{output_index}:{digest}"
