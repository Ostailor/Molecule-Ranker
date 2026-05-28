from __future__ import annotations

from molecule_ranker.generation.generators.base import (
    attach_atom_to_first_available_atom,
    build_generated_molecule,
)
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)

SUBSTITUENT_VARIATIONS = [
    {"label": "methyl_variation", "atom_symbol": "C"},
    {"label": "fluoro_variation", "atom_symbol": "F"},
    {"label": "chloro_variation", "atom_symbol": "Cl"},
]


class ReactionlessLibraryEnumerator:
    name = "reactionless_library_enum"
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
            for substituent in SUBSTITUENT_VARIATIONS:
                candidate = attach_atom_to_first_available_atom(
                    seed.canonical_smiles,
                    str(substituent["atom_symbol"]),
                )
                if candidate is None:
                    continue
                try:
                    generated.append(
                        build_generated_molecule(
                            generator_name=self.name,
                            generator_version=self.version,
                            objective=objective,
                            seed=seed,
                            smiles=candidate,
                            generation_round=1,
                            output_index=len(generated) + 1,
                            transformation_metadata={
                                "transformation": "reactionless_substituent_variation",
                                "substituent_label": substituent["label"],
                                "documentation": (
                                    "Simple substituent variation for in-silico analog "
                                    "enumeration only; explicitly not a synthesis planner."
                                ),
                                "not_synthesis_planner": True,
                                "reaction_route_provided": False,
                            },
                            warnings=[
                                "reactionless_enumeration_only",
                                "no_reaction_route_or_synthesis_claim",
                            ],
                        )
                    )
                except ValueError:
                    continue
                if len(generated) >= config.generated_per_objective:
                    return generated
        return generated
