from __future__ import annotations

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.oracle_scoring import OracleScoringAgent
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
        metadata={"target_relevance_score": 0.8},
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
        target_relevance_score=0.8,
        seed_selection_reason="Retrieved molecule-target evidence.",
    )


def _generated() -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id="gen-1",
        smiles="CCOc1ccccn1",
        canonical_smiles="CCOc1ccccn1",
        generation_method="fragment_grower",
        parent_seed_ids=["CHEMBL_A"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 200.0, "logp": 2.0, "tpsa": 30.0},
        fingerprints={},
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.4,
            max_similarity_to_seed=0.62,
            novelty_class="novel_analog",
        ),
        warnings=["in_silico_hypothesis_only"],
    )


def test_oracle_scoring_agent_updates_generation_run_with_inspectable_oracles() -> None:
    run = GenerationRun(
        objectives=[_objective()],
        seeds=[_seed()],
        generated=[_generated()],
        retained=[_generated()],
        rejected=[],
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": run},
    )

    result = OracleScoringAgent().run(context)

    updated = result.config["generation_run"].retained[0]
    assert updated.generation_score is not None
    assert updated.metadata["oracle_scoring"]["experiment_worthiness_score"] == (
        updated.generation_score
    )
    assert len(updated.metadata["oracle_scoring"]["oracles"]) == 12
    assert "not predicted binding" in updated.metadata["oracle_scoring"]["explanation"].lower()
    assert result.traces[-1].agent_name == "OracleScoringAgent"
    assert result.traces[-1].metadata["scored_count"] == 1
