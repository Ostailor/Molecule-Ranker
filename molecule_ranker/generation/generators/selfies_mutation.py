from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, cast

import selfies as sf

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

DEFAULT_ALLOWED_ELEMENTS = {"C", "H", "N", "O", "F", "P", "S", "Cl", "Br", "I"}
DEFAULT_MUTATION_TOKENS = ["[C]", "[N]", "[O]", "[F]", "[Cl]", "[=C]", "[=N]"]


@dataclass(frozen=True)
class EncodedSeed:
    seed: SeedMolecule
    seed_id: str
    canonical_smiles: str
    selfies_tokens: list[str]


class SelfiesMutationGenerator:
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

        encoded_seeds = self._encoded_seeds(seeds)
        if not encoded_seeds:
            return []

        rng = random.Random(config.generation_random_seed)
        seed_smiles = {seed.canonical_smiles for seed in encoded_seeds}
        token_vocabulary = self._token_vocabulary(encoded_seeds)
        generated_by_smiles: dict[str, GeneratedMolecule] = {}
        attempts = 0

        for generation_round in range(1, config.max_generation_rounds + 1):
            if len(generated_by_smiles) >= config.generated_per_objective:
                break
            while attempts < config.max_generated_before_filtering:
                if len(generated_by_smiles) >= config.generated_per_objective:
                    break
                attempts += 1
                child = self._propose_child(
                    encoded_seeds=encoded_seeds,
                    config=config,
                    rng=rng,
                    token_vocabulary=token_vocabulary,
                )
                generated = self._generated_molecule_from_child(
                    child=child,
                    objective=objective,
                    config=config,
                    generation_round=generation_round,
                    attempt=attempts,
                    seed_smiles=seed_smiles,
                )
                if generated is None:
                    continue
                generated_by_smiles.setdefault(generated.canonical_smiles, generated)
            if attempts >= config.max_generated_before_filtering:
                break

        return list(generated_by_smiles.values())[: config.generated_per_objective]

    def _encoded_seeds(self, seeds: list[SeedMolecule]) -> list[EncodedSeed]:
        encoded: list[EncodedSeed] = []
        for seed in seeds:
            canonical = canonicalize_smiles(seed.canonical_smiles)
            if canonical is None:
                continue
            try:
                selfies_tokens = list(sf.split_selfies(sf.encoder(canonical)))
            except sf.EncoderError:
                continue
            if not selfies_tokens:
                continue
            encoded.append(
                EncodedSeed(
                    seed=seed,
                    seed_id=self._seed_id(seed),
                    canonical_smiles=canonical,
                    selfies_tokens=selfies_tokens,
                )
            )
        return encoded

    def _propose_child(
        self,
        *,
        encoded_seeds: list[EncodedSeed],
        config: GenerationConfig,
        rng: random.Random,
        token_vocabulary: list[str],
    ) -> dict[str, Any]:
        can_crossover = config.enable_crossover and len(encoded_seeds) >= 2
        operation_choices = ["substitution", "insertion", "deletion"]
        if can_crossover:
            operation_choices.append("crossover")
        operation = rng.choice(operation_choices)

        if operation == "crossover":
            parent_a, parent_b = rng.sample(encoded_seeds, 2)
            cut_a = rng.randrange(1, len(parent_a.selfies_tokens) + 1)
            cut_b = rng.randrange(0, len(parent_b.selfies_tokens))
            tokens = [
                *parent_a.selfies_tokens[:cut_a],
                *parent_b.selfies_tokens[cut_b:],
            ]
            parents = [parent_a, parent_b]
            mutation_operations = [
                {
                    "operation": "crossover",
                    "cut_a": cut_a,
                    "cut_b": cut_b,
                    "parent_seed_ids": [parent_a.seed_id, parent_b.seed_id],
                }
            ]
        else:
            parent = rng.choice(encoded_seeds)
            tokens = list(parent.selfies_tokens)
            parents = [parent]
            mutation_operations = []

        mutation_count = rng.randint(1, config.max_mutations_per_child)
        for _ in range(mutation_count):
            mutation_operation = operation if operation != "crossover" else rng.choice(
                ["substitution", "insertion", "deletion"]
            )
            tokens, mutation_metadata = self._mutate_tokens(
                tokens=tokens,
                operation=mutation_operation,
                rng=rng,
                token_vocabulary=token_vocabulary,
            )
            mutation_operations.append(mutation_metadata)

        return {
            "tokens": tokens,
            "operation": operation,
            "parents": parents,
            "mutation_operations": mutation_operations,
        }

    def _mutate_tokens(
        self,
        *,
        tokens: list[str],
        operation: str,
        rng: random.Random,
        token_vocabulary: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        mutated = list(tokens) or [rng.choice(token_vocabulary)]
        if operation == "substitution":
            position = rng.randrange(len(mutated))
            old_token = mutated[position]
            new_token = rng.choice(token_vocabulary)
            mutated[position] = new_token
            return mutated, {
                "operation": operation,
                "position": position,
                "old_token": old_token,
                "new_token": new_token,
            }
        if operation == "insertion":
            position = rng.randrange(len(mutated) + 1)
            new_token = rng.choice(token_vocabulary)
            mutated.insert(position, new_token)
            return mutated, {
                "operation": operation,
                "position": position,
                "new_token": new_token,
            }

        if len(mutated) <= 3:
            position = rng.randrange(len(mutated) + 1)
            new_token = rng.choice(token_vocabulary)
            mutated.insert(position, new_token)
            return mutated, {
                "operation": "insertion",
                "requested_operation": operation,
                "position": position,
                "new_token": new_token,
            }
        position = rng.randrange(len(mutated))
        old_token = mutated.pop(position)
        return mutated, {
            "operation": operation,
            "position": position,
            "old_token": old_token,
        }

    def _generated_molecule_from_child(
        self,
        *,
        child: dict[str, Any],
        objective: GenerationObjective,
        config: GenerationConfig,
        generation_round: int,
        attempt: int,
        seed_smiles: set[str],
    ) -> GeneratedMolecule | None:
        tokens = [str(token) for token in child["tokens"]]
        if not tokens:
            return None
        selfies = "".join(tokens)
        try:
            decoded_smiles = cast(str, sf.decoder(selfies))
        except sf.DecoderError:
            return None

        canonical_smiles = canonicalize_smiles(decoded_smiles)
        if canonical_smiles is None or canonical_smiles in seed_smiles:
            return None
        mol = mol_from_smiles(canonical_smiles)
        if mol is None:
            return None

        descriptors = descriptors_from_mol(mol)
        allowed_elements = set(config.allowed_generation_elements)
        allowed_elements_ok = allowed_elements_check(mol, allowed_elements)
        rejection_reasons = basic_property_bounds_check(descriptors, BASIC_PROPERTY_BOUNDS)
        if not allowed_elements_ok:
            rejection_reasons.append("contains disallowed element")
        alerts = detect_basic_alerts(mol)
        parents = cast(list[EncodedSeed], child["parents"])
        parent_seed_ids = [parent.seed_id for parent in parents]
        warnings = ["in_silico_hypothesis_only"]
        if rejection_reasons:
            warnings.append("coarse_property_bounds_warning")
        if alerts:
            warnings.append("basic_alerts_present")

        return GeneratedMolecule(
            generated_id=f"{objective.objective_id}:selfies:{attempt}",
            smiles=decoded_smiles,
            canonical_smiles=canonical_smiles,
            selfies=selfies,
            inchi_key=inchi_key_from_mol(mol),
            generation_method=self.name,
            parent_seed_ids=parent_seed_ids,
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
            warnings=warnings,
            metadata={
                "generator": self.name,
                "generator_name": self.name,
                "generator_version": self.version,
                "operation": child["operation"],
                "mutation_operations": child["mutation_operations"],
                "transformation_metadata": {
                    "transformation": "selfies_mutation_or_crossover",
                    "documentation": (
                        "SELFIES token mutation/crossover used to propose an in-silico "
                        "hypothesis; this is not evidence of activity or synthesis."
                    ),
                    "mutation_operations": child["mutation_operations"],
                },
                "parent_seed_names": [parent.seed.name for parent in parents],
                "source_seed_smiles": [parent.canonical_smiles for parent in parents],
                "attempt": attempt,
                "hypothesis_only": True,
                "no_imported_evidence": True,
                "no_synthesis_planning": True,
                "generator_provenance": [
                    {
                        "generator_name": self.name,
                        "generator_version": self.version,
                        "parent_seed_ids": parent_seed_ids,
                        "transformation_metadata": {
                            "transformation": "selfies_mutation_or_crossover",
                            "mutation_operations": child["mutation_operations"],
                        },
                        "warnings": warnings,
                    }
                ],
            },
        )

    def _token_vocabulary(self, encoded_seeds: list[EncodedSeed]) -> list[str]:
        observed = {
            token
            for seed in encoded_seeds
            for token in seed.selfies_tokens
            if not token.startswith("[Ring") and not token.startswith("[Branch")
        }
        return sorted(observed | set(DEFAULT_MUTATION_TOKENS))

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey", "name"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
