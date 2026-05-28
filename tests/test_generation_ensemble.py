from __future__ import annotations

from molecule_ranker.generation.ensemble import GeneratorEnsemble
from molecule_ranker.generation.generators.base import build_generated_molecule
from molecule_ranker.generation.schemas import GenerationConfig, GenerationObjective, SeedMolecule


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-maob-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        target_identifiers={"ensembl": "ENSG00000069535"},
        mechanism_hint="retrieved mechanism context",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
    )


def _seed() -> SeedMolecule:
    return SeedMolecule(
        name="Seed A",
        canonical_smiles="CCOc1ccccc1",
        identifiers={"chembl": "CHEMBL_A"},
        known_targets=["MAOB"],
        source_candidate_name="Seed A",
        evidence_count=2,
        best_evidence_confidence=0.9,
        target_relevance_score=0.84,
        seed_selection_reason="Retrieved molecule-target evidence.",
    )


class MockGenerator:
    version = "test"

    def __init__(self, name: str, smiles: list[str]) -> None:
        self.name = name
        self.smiles = smiles
        self.observed_budget = 0

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ):
        self.observed_budget = config.generated_per_objective
        return [
            build_generated_molecule(
                generator_name=self.name,
                generator_version=self.version,
                objective=objective,
                seed=seeds[0],
                smiles=smiles,
                generation_round=1,
                output_index=index,
                transformation_metadata={
                    "transformation": "mocked",
                    "documentation": f"{self.name} test transform",
                },
                warnings=["mock_generator_output"],
            )
            for index, smiles in enumerate(self.smiles, start=1)
        ]


class FailingGenerator:
    name = "failing_generator"
    version = "test"

    def generate(
        self,
        objective: GenerationObjective,
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ):
        raise RuntimeError("planned generator failure")


def test_ensemble_runs_mocked_generators() -> None:
    generator_a = MockGenerator("mock_a", ["CCOc1ccccc1F"])
    generator_b = MockGenerator("mock_b", ["CCOc1ccccc1Cl"])
    ensemble = GeneratorEnsemble(generators=[generator_a, generator_b])

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(generated_per_objective=4),
    )

    assert [run["generator_name"] for run in result.generator_runs] == ["mock_a", "mock_b"]
    assert {molecule.generation_method for molecule in result.generated} == {
        "mock_a",
        "mock_b",
    }
    assert generator_a.observed_budget == 2
    assert generator_b.observed_budget == 2


def test_generator_provenance_preserved() -> None:
    ensemble = GeneratorEnsemble(generators=[MockGenerator("mock_a", ["CCOc1ccccc1F"])])

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(generated_per_objective=2),
    )

    molecule = result.generated[0]
    assert molecule.metadata["generator_name"] == "mock_a"
    assert molecule.metadata["generator_version"] == "test"
    assert molecule.metadata["transformation_metadata"]["documentation"] == (
        "mock_a test transform"
    )
    assert molecule.metadata["generator_provenance"][0]["generator_name"] == "mock_a"
    assert molecule.parent_seed_ids == ["CHEMBL_A"]


def test_duplicate_outputs_merged() -> None:
    ensemble = GeneratorEnsemble(
        generators=[
            MockGenerator("mock_a", ["CCOc1ccccc1F"]),
            MockGenerator("mock_b", ["CCOc1ccccc1F"]),
        ]
    )

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(generated_per_objective=4),
    )

    assert len(result.generated) == 1
    molecule = result.generated[0]
    assert sorted(
        item["generator_name"] for item in molecule.metadata["generator_provenance"]
    ) == ["mock_a", "mock_b"]
    assert "duplicate_generator_output_merged" in molecule.warnings


def test_generator_failure_isolated() -> None:
    ensemble = GeneratorEnsemble(
        generators=[
            FailingGenerator(),
            MockGenerator("mock_ok", ["CCOc1ccccc1F"]),
        ]
    )

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(generated_per_objective=4),
    )

    assert len(result.generated) == 1
    assert result.failures[0]["generator_name"] == "failing_generator"
    assert result.generator_runs[0]["status"] == "failed"
    assert result.generator_runs[1]["status"] == "succeeded"


def test_generator_can_be_disabled() -> None:
    ensemble = GeneratorEnsemble(
        generators=[
            MockGenerator("mock_a", ["CCOc1ccccc1F"]),
            MockGenerator("mock_b", ["CCOc1ccccc1Cl"]),
        ]
    )

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(
            generated_per_objective=4,
            disabled_generators=["mock_a"],
        ),
    )

    assert [run["generator_name"] for run in result.generator_runs] == ["mock_b"]
    assert {molecule.generation_method for molecule in result.generated} == {"mock_b"}


def test_no_generated_molecule_has_fake_evidence() -> None:
    ensemble = GeneratorEnsemble(generators=[MockGenerator("mock_a", ["CCOc1ccccc1F"])])

    result = ensemble.run(
        objectives=[_objective()],
        seeds=[_seed()],
        config=GenerationConfig(generated_per_objective=2),
    )

    assert result.generated
    for molecule in result.generated:
        assert not hasattr(molecule, "evidence")
        assert molecule.origin == "generated"
        assert molecule.metadata["hypothesis_only"] is True
        assert molecule.metadata["no_imported_evidence"] is True
