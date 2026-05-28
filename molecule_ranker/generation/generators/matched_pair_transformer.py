from __future__ import annotations

from molecule_ranker.generation.chemistry import canonicalize_smiles
from molecule_ranker.generation.generators.base import build_generated_molecule
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)

SAFE_LOCAL_TRANSFORMATIONS = [
    {
        "name": "terminal_fluoro_to_chloro",
        "from": "F",
        "to": "Cl",
        "documentation": "Documented local halogen variation; no disease-specific fact encoded.",
    },
    {
        "name": "ethoxy_to_propoxy",
        "from": "CCO",
        "to": "CCCO",
        "documentation": "Documented small alkoxy length variation; no activity claim implied.",
    },
    {
        "name": "methyl_to_fluoro",
        "from": "C",
        "to": "F",
        "documentation": "Documented atom-level local variation used only for enumeration.",
    },
]


class MatchedPairTransformer:
    name = "matched_pair_transformer"
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

        transformations = self._transformations_from_seeds(seeds)
        for seed in seeds:
            canonical_seed = canonicalize_smiles(seed.canonical_smiles)
            if canonical_seed is None:
                continue
            for transform in transformations:
                source = str(transform["from"])
                replacement = str(transform["to"])
                if source not in canonical_seed:
                    continue
                candidate = canonical_seed.replace(source, replacement, 1)
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
                                "transformation": "matched_pair_transform",
                                "rule_name": transform["name"],
                                "from": source,
                                "to": replacement,
                                "documentation": transform["documentation"],
                                "source": transform["source"],
                            },
                            warnings=["matched_pair_rule_hypothesis_only"],
                        )
                    )
                except ValueError:
                    continue
                if len(generated) >= config.generated_per_objective:
                    return generated
        return generated

    def _transformations_from_seeds(self, seeds: list[SeedMolecule]) -> list[dict[str, str]]:
        mined = [
            {**transform, "source": "retrieved_candidate_metadata"}
            for seed in seeds
            for transform in seed.metadata.get("matched_pair_transformations", [])
            if {"name", "from", "to", "documentation"} <= set(transform)
        ]
        safe_local = [
            {**transform, "source": "safe_local_transformation"}
            for transform in SAFE_LOCAL_TRANSFORMATIONS
        ]
        return [*mined, *safe_local]
