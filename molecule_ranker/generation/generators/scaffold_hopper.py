from __future__ import annotations

from molecule_ranker.generation.chemistry import canonicalize_smiles
from molecule_ranker.generation.generators.base import build_generated_molecule
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)

SCAFFOLD_REPLACEMENT_RULES = [
    {
        "name": "phenyl_to_pyridyl_context_swap",
        "from": "c1ccccc1",
        "to": "c1ccncc1",
        "documentation": (
            "Rule-based scaffold replacement that changes an aromatic ring while retaining "
            "the surrounding string context; it does not claim preserved activity."
        ),
    },
    {
        "name": "phenyl_to_difluorophenyl_context_swap",
        "from": "c1ccccc1",
        "to": "c1cc(F)cc(F)c1",
        "documentation": (
            "Rule-based aromatic scaffold substitution for diversity; it is not evidence "
            "of target engagement."
        ),
    },
]


class ScaffoldHopper:
    name = "scaffold_hopper"
    version = "1.1"

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> list[GeneratedMolecule]:
        generated: list[GeneratedMolecule] = []
        if config.generated_per_objective == 0:
            return generated

        for seed in seeds:
            canonical_seed = canonicalize_smiles(seed.canonical_smiles)
            if canonical_seed is None:
                continue
            for rule in SCAFFOLD_REPLACEMENT_RULES:
                if str(rule["from"]) not in canonical_seed:
                    continue
                candidate = canonical_seed.replace(str(rule["from"]), str(rule["to"]), 1)
                canonical_candidate = canonicalize_smiles(candidate)
                if canonical_candidate is None or canonical_candidate == canonical_seed:
                    continue
                try:
                    generated.append(
                        build_generated_molecule(
                            generator_name=self.name,
                            generator_version=self.version,
                            objective=objective,
                            seed=seed,
                            smiles=canonical_candidate,
                            generation_round=1,
                            output_index=len(generated) + 1,
                            transformation_metadata={
                                "transformation": "rule_based_scaffold_replacement",
                                "rule_name": rule["name"],
                                "documentation": rule["documentation"],
                                "activity_preservation_claim": False,
                            },
                            warnings=[
                                "rule_based_scaffold_hop",
                                "no_preserved_activity_claim",
                            ],
                        )
                    )
                except ValueError:
                    continue
                if len(generated) >= config.generated_per_objective:
                    return generated
        return generated
