from __future__ import annotations

import random
from typing import Any

from rdkit import Chem

from molecule_ranker.generation.generators.base import (
    MolecularGenerator,
    build_generated_molecule,
)
from molecule_ranker.generation.generators.fragment_grower import FragmentGrower
from molecule_ranker.generation.generators.matched_pair_transformer import (
    MatchedPairTransformer,
)
from molecule_ranker.generation.generators.reactionless_library_enum import (
    ReactionlessLibraryEnumerator,
)
from molecule_ranker.generation.generators.scaffold_hopper import ScaffoldHopper

try:
    from molecule_ranker.generation.generators.selfies_mutation import SelfiesMutationGenerator
except ModuleNotFoundError as exc:
    if exc.name != "selfies":
        raise

    from molecule_ranker.generation.chemistry import canonicalize_smiles, mol_from_smiles
    from molecule_ranker.generation.schemas import (
        GeneratedMolecule,
        GenerationConfig,
        GenerationObjective,
        SeedMolecule,
    )

    class _FallbackSelfiesMutationGenerator:
        name = "selfies_mutation"
        version = "1.1"

        def generate(
            self,
            objective: GenerationObjective,
            seeds: list[SeedMolecule],
            config: GenerationConfig,
        ) -> list[GeneratedMolecule]:
            if not seeds or config.generated_per_objective == 0:
                return []

            rng = random.Random(config.generation_random_seed)
            seed_smiles = {
                canonical
                for seed in seeds
                if (canonical := canonicalize_smiles(seed.canonical_smiles)) is not None
            }
            generated_by_smiles: dict[str, GeneratedMolecule] = {}
            allowed_atoms = [
                atom
                for atom in ["C", "N", "O", "F", "Cl"]
                if atom in set(config.allowed_generation_elements)
            ] or ["C"]

            for attempt in range(1, config.max_generated_before_filtering + 1):
                if len(generated_by_smiles) >= config.generated_per_objective:
                    break
                seed = rng.choice(seeds)
                child_smiles = _attach_atom_at_random_position(
                    seed.canonical_smiles,
                    rng.choice(allowed_atoms),
                    rng,
                )
                canonical = canonicalize_smiles(child_smiles) if child_smiles else None
                if (
                    canonical is None
                    or canonical in seed_smiles
                    or canonical in generated_by_smiles
                ):
                    continue

                mutation_operations = [
                    {
                        "operation": "fallback_atom_attachment",
                        "atom": child_smiles.replace(seed.canonical_smiles, "")
                        if child_smiles
                        else "unknown",
                        "selfies_dependency_available": False,
                    }
                ]
                try:
                    generated = build_generated_molecule(
                        generator_name=self.name,
                        generator_version=self.version,
                        objective=objective,
                        seed=seed,
                        smiles=canonical,
                        generation_round=1,
                        output_index=attempt,
                        transformation_metadata={
                            "transformation": "rdkit_safe_mutation_fallback",
                            "mutation_operations": mutation_operations,
                            "selfies_dependency_available": False,
                            "documentation": (
                                "RDKit fallback used because the optional SELFIES "
                                "package is unavailable; output is an in-silico "
                                "hypothesis only."
                            ),
                        },
                        warnings=["selfies_dependency_unavailable_fallback"],
                    )
                except ValueError:
                    continue

                generated_by_smiles[canonical] = generated.model_copy(
                    update={
                        "selfies": _pseudo_selfies_from_smiles(canonical),
                        "metadata": {
                            **generated.metadata,
                            "operation": "fallback_atom_attachment",
                            "mutation_operations": mutation_operations,
                            "selfies_dependency_available": False,
                        },
                    }
                )

            return list(generated_by_smiles.values())[: config.generated_per_objective]

    def _attach_atom_at_random_position(
        smiles: str,
        atom_symbol: str,
        rng: random.Random,
    ) -> str | None:
        mol = mol_from_smiles(smiles)
        if mol is None:
            return None
        candidates = [
            atom
            for atom in mol.GetAtoms()
            if atom.GetAtomicNum() > 1
            and atom.GetFormalCharge() == 0
            and atom.GetTotalNumHs() > 0
        ]
        rng.shuffle(candidates)
        for atom in candidates:
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

    def _pseudo_selfies_from_smiles(smiles: str) -> str:
        mol = mol_from_smiles(smiles)
        if mol is None:
            return f"[fallback:{smiles}]"
        return "".join(f"[{atom.GetSymbol()}]" for atom in mol.GetAtoms())

    SelfiesMutationGenerator: Any = _FallbackSelfiesMutationGenerator

__all__ = [
    "FragmentGrower",
    "MatchedPairTransformer",
    "MolecularGenerator",
    "ReactionlessLibraryEnumerator",
    "ScaffoldHopper",
    "SelfiesMutationGenerator",
    "build_generated_molecule",
]
