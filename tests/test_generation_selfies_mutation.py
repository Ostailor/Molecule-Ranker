from __future__ import annotations

from molecule_ranker.generation.generators import SelfiesMutationGenerator
from molecule_ranker.generation.schemas import GenerationConfig, GenerationObjective, SeedMolecule


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-maob-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        target_identifiers={"ensembl": "ENSG00000069535"},
        mechanism_hint="MAO-B inhibition",
        seed_molecule_names=["Rasagiline", "Safinamide"],
        seed_molecule_ids=["CHEMBL887", "CHEMBL123"],
        objective_type="target_conditioned_analog_generation",
        constraints={},
        metadata={"source": "unit-test"},
    )


def _seeds() -> list[SeedMolecule]:
    return [
        SeedMolecule(
            name="Rasagiline",
            canonical_smiles="C#CCN(C)Cc1ccccc1",
            identifiers={"chembl": "CHEMBL887"},
            known_targets=["MAOB"],
            source_candidate_name="Rasagiline",
            evidence_count=3,
            best_evidence_confidence=0.91,
            target_relevance_score=0.84,
            seed_selection_reason="Retrieved ChEMBL seed with target evidence.",
            metadata={"rank": 1},
        ),
        SeedMolecule(
            name="Safinamide",
            canonical_smiles="NCC(O)c1ccc(OCc2ccccc2)cc1",
            identifiers={"chembl": "CHEMBL123"},
            known_targets=["MAOB"],
            source_candidate_name="Safinamide",
            evidence_count=2,
            best_evidence_confidence=0.82,
            target_relevance_score=0.84,
            seed_selection_reason="Retrieved ChEMBL seed with target evidence.",
            metadata={"rank": 2},
        ),
    ]


def _config(seed: int | None = 7) -> GenerationConfig:
    return GenerationConfig(
        generation_random_seed=seed,
        generated_per_objective=6,
        max_generation_rounds=8,
        max_mutations_per_child=2,
        enable_crossover=True,
        max_generated_before_filtering=40,
    )


def test_selfies_generator_produces_candidates_from_seed_inputs():
    generated = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config())

    assert 1 <= len(generated) <= 6
    assert all(candidate.generation_method == "selfies_mutation" for candidate in generated)
    assert all(candidate.objective_id == "objective-maob-1" for candidate in generated)
    assert all(candidate.conditioned_targets == ["MAOB"] for candidate in generated)
    assert all(candidate.origin == "generated" for candidate in generated)
    assert all(candidate.validation.valid_rdkit_mol for candidate in generated)
    assert all(candidate.canonical_smiles for candidate in generated)
    assert all(candidate.selfies for candidate in generated)
    assert all(candidate.metadata["generator"] == "selfies_mutation" for candidate in generated)
    assert {candidate.canonical_smiles for candidate in generated}.isdisjoint(
        {seed.canonical_smiles for seed in _seeds()}
    )


def test_same_random_seed_gives_reproducible_output():
    first = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config(seed=11))
    second = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config(seed=11))

    assert [candidate.canonical_smiles for candidate in first] == [
        candidate.canonical_smiles for candidate in second
    ]
    assert [candidate.metadata["mutation_operations"] for candidate in first] == [
        candidate.metadata["mutation_operations"] for candidate in second
    ]


def test_different_random_seed_can_produce_different_output():
    first = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config(seed=1))
    second = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config(seed=2))

    assert [candidate.canonical_smiles for candidate in first] != [
        candidate.canonical_smiles for candidate in second
    ]


def test_generated_molecules_preserve_parent_seed_metadata():
    generated = SelfiesMutationGenerator().generate(_objective(), _seeds(), _config(seed=13))

    assert generated
    for candidate in generated:
        assert candidate.parent_seed_ids
        assert set(candidate.parent_seed_ids) <= {"CHEMBL887", "CHEMBL123"}
        assert candidate.metadata["parent_seed_names"]
        assert candidate.metadata["mutation_operations"]
        assert candidate.metadata["source_seed_smiles"]
        if candidate.metadata["operation"] == "crossover":
            assert len(candidate.parent_seed_ids) == 2


def test_no_generated_molecules_when_no_seeds_are_available():
    generated = SelfiesMutationGenerator().generate(_objective(), [], _config())

    assert generated == []
