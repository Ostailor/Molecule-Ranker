from __future__ import annotations

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.novel_molecule import NovelMoleculeAgent
from molecule_ranker.generation.errors import GenerationError
from molecule_ranker.generation.schemas import GenerationRun
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, Target


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )


def _seed_candidate(
    name: str,
    smiles: str | None = "CCOc1ccccc1",
    known_targets: list[str] | None = None,
) -> MoleculeCandidate:
    chemical_metadata = {"inchikey": f"{name.upper()}-KEY"}
    if smiles is not None:
        chemical_metadata["canonical_smiles"] = smiles
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=known_targets if known_targets is not None else ["MAOB"],
        development_status="max_phase_4",
        mechanism_of_action="MAOB inhibitor",
        chemical_metadata=chemical_metadata,
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id=f"mechanism-{name}",
                title="Seed target mechanism",
                evidence_type="mechanism",
                summary="Retrieved mechanism evidence.",
                confidence=0.9,
            )
        ],
    )


def _target() -> Target:
    return Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        identifiers={"ensembl": "ENSG00000069535"},
        disease_relevance_score=0.84,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Retrieved disease-target association.",
                confidence=0.84,
            )
        ],
    )


def test_novel_molecule_generation_is_opt_in_and_keeps_existing_candidates_separate():
    candidate = _seed_candidate("seed")
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[candidate],
        config={"enable_novel_generation": False},
    )

    result = NovelMoleculeAgent().run(context)

    assert result.candidates == [candidate]
    assert result.generated_candidates == []
    assert "generation_run" not in result.config
    assert result.config["generated_molecules"] == []
    trace = result.traces[-1]
    assert trace.agent_name == "NovelMoleculeAgent"
    assert trace.metadata["implemented"] is True
    assert trace.metadata["generation_enabled"] is False


def test_enabled_generation_produces_generation_run_from_real_seed_candidates():
    seed_a = _seed_candidate("seed-a", "CCN(CC)CCOc1ccccc1")
    seed_b = _seed_candidate("seed-b", "CC(C)NCC(O)c1ccc(O)c(O)c1")
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[seed_a, seed_b],
        config={
            "enable_novel_generation": True,
            "generated_candidate_limit": 6,
            "generation_attempt_budget": 80,
            "generation_random_seed": 7,
            "descriptor_bounds_warning_only": True,
            "near_duplicate_similarity_threshold": 0.98,
            "duplicate_similarity_threshold": 0.995,
        },
    )

    result = NovelMoleculeAgent().run(context)

    assert result.candidates == [seed_a, seed_b]
    generation_run = result.config["generation_run"]
    generated_molecules = result.config["generated_molecules"]
    assert isinstance(generation_run, GenerationRun)
    assert generation_run.objectives
    assert len(generation_run.seeds) == 2
    assert generation_run.generated
    assert generated_molecules == generation_run.retained
    assert 1 <= len(generated_molecules) <= 6
    assert 1 <= len(result.generated_candidates) <= 6
    generated_smiles = [candidate.canonical_smiles for candidate in result.generated_candidates]
    seed_smiles = {
        seed_a.chemical_metadata["canonical_smiles"],
        seed_b.chemical_metadata["canonical_smiles"],
    }
    assert len(generated_smiles) == len(set(generated_smiles))
    assert not set(generated_smiles) & seed_smiles
    assert result.generated_candidates == sorted(
        result.generated_candidates,
        key=lambda candidate: candidate.generation_score,
        reverse=True,
    )
    for generated in result.generated_candidates:
        assert generated.source == "SELFIES_MUTATION_CROSSOVER"
        assert generated.target_symbol == "MAOB"
        assert generated.seed_molecule_names
        assert generated.generation_score is not None
        assert 0.0 <= generated.generation_score <= 1.0
        assert generated.max_seed_similarity <= 1.0
        assert generated.descriptors["molecular_weight"] > 0
        assert generated.descriptors["heavy_atom_count"] > 0
        assert generated.trace["origin"] == "generated"
        assert generated.trace["score_explanation"]
        assert "in_silico_hypothesis_only" in generated.warnings
        assert generated.evidence == []

    trace = result.traces[-1]
    assert trace.metadata["generation_enabled"] is True
    assert trace.metadata["generated_count"] == len(result.generated_candidates)
    assert trace.metadata["seed_count"] == 2
    assert trace.metadata["generation_run"]["retained_count"] == len(generated_molecules)
    assert trace.metadata["filters"]["near_duplicate_similarity_threshold"] == 0.98


def test_generation_without_seeds_warns_and_continues_by_default():
    candidate = _seed_candidate("off-target", "CCN(CC)CCOc1ccccc1", known_targets=["DRD2"])
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[candidate],
        config={"enable_novel_generation": True},
    )

    result = NovelMoleculeAgent().run(context)

    assert result.candidates == [candidate]
    assert result.generated_candidates == []
    assert result.config["generated_molecules"] == []
    assert isinstance(result.config["generation_run"], GenerationRun)
    assert result.config["generation_run"].warnings
    assert "no seed molecules" in result.config["generation_run"].warnings[0].lower()
    assert result.traces[-1].metadata["warnings"]


def test_strict_generation_fails_when_no_seeds_are_available():
    candidate = _seed_candidate("unstructured", None)
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[candidate],
        config={
            "enable_novel_generation": True,
            "strict_generation": True,
        },
    )

    with pytest.raises(GenerationError):
        NovelMoleculeAgent().run(context)


def test_generated_molecules_are_not_added_to_main_ranking_by_default():
    seed_a = _seed_candidate("seed-a", "CCN(CC)CCOc1ccccc1")
    seed_b = _seed_candidate("seed-b", "CC(C)NCC(O)c1ccc(O)c(O)c1")
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[seed_a, seed_b],
        config={
            "enable_novel_generation": True,
            "generated_candidate_limit": 4,
            "generation_attempt_budget": 40,
            "generation_random_seed": 11,
            "descriptor_bounds_warning_only": True,
        },
    )

    result = NovelMoleculeAgent().run(context)

    assert result.candidates == [seed_a, seed_b]
    assert result.config["generated_molecules"]
    assert all(candidate.name in {"seed-a", "seed-b"} for candidate in result.candidates)


def test_include_generated_in_main_ranking_preserves_generated_labels_without_evidence():
    seed_a = _seed_candidate("seed-a", "CCN(CC)CCOc1ccccc1")
    seed_b = _seed_candidate("seed-b", "CC(C)NCC(O)c1ccc(O)c(O)c1")
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target()],
        candidates=[seed_a, seed_b],
        config={
            "enable_novel_generation": True,
            "include_generated_in_main_ranking": True,
            "generated_candidate_limit": 4,
            "generation_attempt_budget": 40,
            "generation_random_seed": 11,
            "descriptor_bounds_warning_only": True,
        },
    )

    result = NovelMoleculeAgent().run(context)

    generated_main = [
        candidate
        for candidate in result.candidates
        if candidate.chemical_metadata.get("origin") == "generated"
    ]
    assert generated_main
    assert len(generated_main) == len(result.config["generated_molecules"])
    for candidate in generated_main:
        assert candidate.origin == "generated"
        assert candidate.molecule_type == "generated"
        assert candidate.direct_evidence_available is False
        assert candidate.generation_metadata["generated_id"] == candidate.name
        assert candidate.evidence == []
        assert candidate.score is not None
        assert candidate.chemical_metadata["generation_score_explanation"]
        assert candidate.chemical_metadata["direct_experimental_evidence"] is False
    assert result.traces[-1].metadata["main_ranking_generated_count"] == len(generated_main)
