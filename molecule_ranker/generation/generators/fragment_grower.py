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

GENERIC_FRAGMENT_VOCABULARY = [
    {"label": "methyl", "atom_symbol": "C"},
    {"label": "amino_atom", "atom_symbol": "N"},
    {"label": "fluoro", "atom_symbol": "F"},
]


class FragmentGrower:
    name = "fragment_grower"
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
            for fragment in self._allowed_fragments(seed):
                grown = attach_atom_to_first_available_atom(
                    seed.canonical_smiles,
                    str(fragment["atom_symbol"]),
                )
                if grown is None:
                    continue
                try:
                    generated.append(
                        build_generated_molecule(
                            generator_name=self.name,
                            generator_version=self.version,
                            objective=objective,
                            seed=seed,
                            smiles=grown,
                            generation_round=1,
                            output_index=len(generated) + 1,
                            transformation_metadata={
                                "transformation": "fragment_growth",
                                "documentation": (
                                    "Rule-based atom/fragment growth from seed structure "
                                    "and generic non-biomedical fragment vocabulary; no "
                                    "activity or synthesis claim is implied."
                                ),
                                "fragment_label": fragment["label"],
                                "fragment_source": fragment["source"],
                            },
                            warnings=["fragment_growth_hypothesis_only"],
                        )
                    )
                except ValueError:
                    continue
                if len(generated) >= config.generated_per_objective:
                    return generated
        return generated

    def _allowed_fragments(self, seed: SeedMolecule) -> list[dict[str, str]]:
        observed_fragments: list[dict[str, str]] = []
        for fragment in seed.metadata.get("allowed_fragments", []):
            if isinstance(fragment, dict):
                label = fragment.get("label") or fragment.get("name") or "seed_fragment"
                symbol = fragment.get("atom_symbol") or fragment.get("symbol")
            elif isinstance(fragment, (list, tuple)) and len(fragment) >= 2:
                label, symbol = fragment[0], fragment[1]
            else:
                continue
            if symbol in {"C", "N", "O", "F", "Cl"}:
                observed_fragments.append(
                    {
                        "label": str(label),
                        "atom_symbol": str(symbol),
                        "source": "retrieved_seed",
                    }
                )
        generic = [
            {**fragment, "source": "curated_generic_non_biomedical"}
            for fragment in GENERIC_FRAGMENT_VOCABULARY
        ]
        return [*observed_fragments, *generic]
