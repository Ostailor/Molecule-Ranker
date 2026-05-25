from __future__ import annotations

from typing import Protocol

from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)


class MolecularGenerator(Protocol):
    name: str

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> list[GeneratedMolecule]:
        """Generate molecule hypotheses from evidence-backed seed molecules."""
        ...
