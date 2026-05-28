from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.generation.generators import (
    FragmentGrower,
    MatchedPairTransformer,
    MolecularGenerator,
    ReactionlessLibraryEnumerator,
    ScaffoldHopper,
    SelfiesMutationGenerator,
)
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)


class GeneratorEnsembleResult(BaseModel):
    generated: list[GeneratedMolecule] = Field(default_factory=list)
    generator_runs: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneratorEnsemble:
    name = "generator_ensemble"
    version = "1.1"

    def __init__(
        self,
        generators: Sequence[MolecularGenerator] | None = None,
        *,
        disabled_generators: set[str] | None = None,
    ) -> None:
        self.generators = list(generators) if generators is not None else self._default_generators()
        self.disabled_generators = disabled_generators or set()
        self.last_result = GeneratorEnsembleResult()

    def run(
        self,
        *,
        objectives: list[GenerationObjective],
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> GeneratorEnsembleResult:
        enabled_generators = self._enabled_generators(config)
        generator_runs: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        warnings: list[str] = []
        generated_by_key: dict[tuple[str, str], GeneratedMolecule] = {}

        if not enabled_generators:
            result = GeneratorEnsembleResult(
                warnings=["No molecular generators enabled."],
                metadata=self._metadata(enabled_generators=[]),
            )
            self.last_result = result
            return result

        for objective in objectives:
            objective_seeds = self._objective_seeds(objective, seeds)
            if not objective_seeds:
                warnings.append(f"No seeds available for objective {objective.objective_id}.")
                continue
            budgets = self._budget_for_generators(
                enabled_generators,
                total_budget=config.generated_per_objective,
                weights=config.generator_budget_weights,
            )
            for generator in enabled_generators:
                budget = budgets.get(generator.name, 0)
                if budget <= 0:
                    generator_runs.append(
                        self._run_record(generator, objective, "skipped", budget, 0)
                    )
                    continue
                generator_config = config.model_copy(update={"generated_per_objective": budget})
                try:
                    outputs = generator.generate(objective, objective_seeds, generator_config)
                except Exception as exc:
                    failure = {
                        "generator_name": generator.name,
                        "generator_version": getattr(generator, "version", "unknown"),
                        "objective_id": objective.objective_id,
                        "error": str(exc),
                    }
                    failures.append(failure)
                    warnings.append(f"Generator {generator.name} failed independently.")
                    generator_runs.append(
                        self._run_record(generator, objective, "failed", budget, 0, failure)
                    )
                    continue

                for molecule in outputs:
                    self._merge_generated(generated_by_key, molecule)
                generator_runs.append(
                    self._run_record(generator, objective, "succeeded", budget, len(outputs))
                )

        generated = sorted(
            generated_by_key.values(),
            key=lambda molecule: (
                molecule.objective_id,
                molecule.generation_method,
                molecule.generated_id,
            ),
        )
        result = GeneratorEnsembleResult(
            generated=generated,
            generator_runs=generator_runs,
            failures=failures,
            warnings=warnings,
            metadata=self._metadata(enabled_generators=enabled_generators),
        )
        self.last_result = result
        return result

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> list[GeneratedMolecule]:
        return self.run(objectives=[objective], seeds=seeds, config=config).generated

    def _enabled_generators(self, config: GenerationConfig) -> list[MolecularGenerator]:
        disabled = set(config.disabled_generators) | self.disabled_generators
        requested = set(config.enabled_generators or [])
        if config.generation_method not in {self.name, "ensemble", "generator_ensemble"}:
            requested.add(config.generation_method)
        enabled: list[MolecularGenerator] = []
        for generator in self.generators:
            if generator.name in disabled:
                continue
            if requested and generator.name not in requested:
                continue
            enabled.append(generator)
        return enabled

    def _budget_for_generators(
        self,
        generators: list[MolecularGenerator],
        *,
        total_budget: int,
        weights: dict[str, float],
    ) -> dict[str, int]:
        if total_budget <= 0:
            return {generator.name: 0 for generator in generators}
        positive_weights = {
            generator.name: max(float(weights.get(generator.name, 1.0)), 0.0)
            for generator in generators
        }
        weight_sum = sum(positive_weights.values()) or float(len(generators))
        budgets = {
            name: int(total_budget * weight / weight_sum)
            for name, weight in positive_weights.items()
        }
        for generator in generators:
            if positive_weights[generator.name] > 0 and budgets[generator.name] == 0:
                budgets[generator.name] = 1
        while sum(budgets.values()) > total_budget:
            largest = max(budgets, key=lambda name: budgets[name])
            budgets[largest] -= 1
        while sum(budgets.values()) < total_budget:
            largest_weight = max(positive_weights, key=lambda name: positive_weights[name])
            budgets[largest_weight] += 1
        return budgets

    def _objective_seeds(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
    ) -> list[SeedMolecule]:
        seeds_by_id = {self._seed_id(seed): seed for seed in seeds}
        objective_seeds = [
            seeds_by_id[seed_id]
            for seed_id in objective.seed_molecule_ids
            if seed_id in seeds_by_id
        ]
        if objective_seeds:
            return objective_seeds
        objective_seed_names = set(objective.seed_molecule_names)
        return [
            seed
            for seed in seeds
            if seed.name in objective_seed_names
            or seed.source_candidate_name in objective_seed_names
        ] or list(seeds)

    def _merge_generated(
        self,
        generated_by_key: dict[tuple[str, str], GeneratedMolecule],
        molecule: GeneratedMolecule,
    ) -> None:
        key = (molecule.objective_id, molecule.canonical_smiles)
        existing = generated_by_key.get(key)
        if existing is None:
            generated_by_key[key] = self._with_ensemble_metadata(molecule)
            return

        existing_provenance = list(existing.metadata.get("generator_provenance", []))
        new_provenance = list(molecule.metadata.get("generator_provenance", []))
        merged = existing.model_copy(
            update={
                "parent_seed_ids": sorted(
                    {*existing.parent_seed_ids, *molecule.parent_seed_ids}
                ),
                "warnings": sorted(
                    {
                        *existing.warnings,
                        *molecule.warnings,
                        "duplicate_generator_output_merged",
                    }
                ),
                "metadata": {
                    **existing.metadata,
                    "generator_provenance": [*existing_provenance, *new_provenance],
                    "duplicate_generators": sorted(
                        {
                            *existing.metadata.get("duplicate_generators", []),
                            molecule.generation_method,
                        }
                    ),
                    "ensemble_duplicate_merged": True,
                },
            }
        )
        generated_by_key[key] = merged

    def _with_ensemble_metadata(self, molecule: GeneratedMolecule) -> GeneratedMolecule:
        return molecule.model_copy(
            update={
                "metadata": {
                    **molecule.metadata,
                    "generator_ensemble": self.name,
                    "generator_ensemble_version": self.version,
                    "hypothesis_only": True,
                    "no_imported_evidence": True,
                }
            }
        )

    def _run_record(
        self,
        generator: MolecularGenerator,
        objective: GenerationObjective,
        status: str,
        budget: int,
        generated_count: int,
        failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "generator_name": generator.name,
            "generator_version": getattr(generator, "version", "unknown"),
            "objective_id": objective.objective_id,
            "status": status,
            "budget": budget,
            "generated_count": generated_count,
        }
        if failure is not None:
            record["failure"] = failure
        return record

    def _metadata(self, *, enabled_generators: list[MolecularGenerator]) -> dict[str, Any]:
        return {
            "ensemble_name": self.name,
            "ensemble_version": self.version,
            "enabled_generators": [generator.name for generator in enabled_generators],
            "disabled_generators": sorted(self.disabled_generators),
            "hypothesis_only": True,
            "no_synthesis_planning": True,
            "no_imported_evidence": True,
        }

    def _default_generators(self) -> list[MolecularGenerator]:
        return [
            SelfiesMutationGenerator(),
            FragmentGrower(),
            ScaffoldHopper(),
            MatchedPairTransformer(),
            ReactionlessLibraryEnumerator(),
        ]

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey", "name"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
