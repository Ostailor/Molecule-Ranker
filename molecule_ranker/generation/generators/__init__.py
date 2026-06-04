from __future__ import annotations

from typing import Any

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

    class _MissingSelfiesMutationGenerator:
        name = "selfies_mutation"
        version = "1.1"

        def generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError(
                "SelfiesMutationGenerator requires the optional 'selfies' dependency."
            )

    SelfiesMutationGenerator: Any = _MissingSelfiesMutationGenerator

__all__ = [
    "FragmentGrower",
    "MatchedPairTransformer",
    "MolecularGenerator",
    "ReactionlessLibraryEnumerator",
    "ScaffoldHopper",
    "SelfiesMutationGenerator",
    "build_generated_molecule",
]
