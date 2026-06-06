from __future__ import annotations

from molecule_ranker.generation.filters import DiversityFilter, NoveltyFilter, ValidationFilter
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationConfig,
    SeedMolecule,
)
from molecule_ranker.schemas import MoleculeCandidate


def _validation() -> ChemicalValidationResult:
    return ChemicalValidationResult(
        valid_rdkit_mol=True,
        sanitization_ok=True,
        canonicalization_ok=True,
        allowed_elements_ok=True,
        descriptor_bounds_ok=True,
        pains_or_alerts=[],
        rejection_reasons=[],
    )


def _generated(
    generated_id: str,
    smiles: str,
    *,
    score: float = 0.5,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles=smiles,
        canonical_smiles=smiles,
        generation_method="unit_test",
        parent_seed_ids=["CHEMBL_SEED"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={},
        fingerprints={},
        validation=_validation(),
        generation_score=score,
        warnings=["in_silico_hypothesis_only"],
        metadata={},
    )


def _existing(name: str, smiles: str) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        chemical_metadata={"canonical_smiles": smiles},
        known_targets=["MAOB"],
        evidence=[],
    )


def _seed(name: str, smiles: str) -> SeedMolecule:
    return SeedMolecule(
        name=name,
        canonical_smiles=smiles,
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=["MAOB"],
        source_candidate_name=name,
        evidence_count=2,
        best_evidence_confidence=0.8,
        target_relevance_score=0.8,
        seed_selection_reason="test seed",
        metadata={"matched_targets": ["MAOB"]},
    )


def test_validation_filter_rejects_invalid_molecules():
    retained, rejected = ValidationFilter().filter(
        [_generated("bad", "C1(CC")],
        config=GenerationConfig(),
    )

    assert retained == []
    assert rejected[0].validation.valid_rdkit_mol is False
    assert "rdkit_parse_failed" in rejected[0].validation.rejection_reasons


def test_validation_filter_rejects_descriptor_bounds_and_alerts_by_config():
    generated = _generated("nitro", "O=[N+]([O-])c1ccccc1")

    retained, rejected = ValidationFilter().filter(
        [generated],
        config=GenerationConfig(
            descriptor_bounds_warning_only=False,
            basic_alerts_warning_only=False,
        ),
    )

    assert retained == []
    assert rejected[0].validation.pains_or_alerts == ["nitro_group"]
    assert "basic_alerts_present" in rejected[0].validation.rejection_reasons


def test_validation_filter_rejects_radical_generated_molecules():
    retained, rejected = ValidationFilter().filter(
        [_generated("radical", "CC[CH]C")],
        config=GenerationConfig(),
    )

    assert retained == []
    assert rejected[0].validation is not None
    assert "radical_atom_present" in rejected[0].validation.rejection_reasons
    assert "structural_sanity_alerts_present" in rejected[0].warnings


def test_validation_filter_rejects_heteroatom_halogen_bonds():
    retained, rejected = ValidationFilter().filter(
        [_generated("n_f_bond", "FNc1ccccc1")],
        config=GenerationConfig(),
    )

    assert retained == []
    assert rejected[0].validation is not None
    assert "heteroatom_halogen_bond" in rejected[0].validation.rejection_reasons
    assert "heteroatom_halogen_bond" in rejected[0].validation.pains_or_alerts


def test_novelty_filter_rejects_duplicates_by_inchikey_or_smiles():
    generated = _generated("duplicate", "CCOc1ccccc1")

    retained, rejected = NoveltyFilter().filter(
        [generated],
        existing_candidates=[_existing("Existing", "CCOc1ccccc1")],
        seeds=[],
        config=GenerationConfig(),
    )

    assert retained == []
    assert rejected[0].novelty is not None
    assert rejected[0].novelty.duplicate_of_existing is True
    assert rejected[0].novelty.novelty_class == "duplicate"


def test_novelty_filter_rejects_duplicates_of_other_generated_molecules():
    first = _generated("first", "CCOc1ccccc1", score=0.9)
    second = _generated("second", "O(CC)c1ccccc1", score=0.4)

    retained, rejected = NoveltyFilter().filter(
        [first, second],
        existing_candidates=[],
        seeds=[],
        config=GenerationConfig(),
    )

    assert [candidate.generated_id for candidate in retained] == ["first"]
    assert rejected[0].generated_id == "second"
    assert rejected[0].novelty is not None
    assert rejected[0].novelty.duplicate_of_generated is True


def test_novelty_filter_rejects_near_duplicates_by_default():
    generated = _generated("near", "CCCCCCOc1ccccc1")

    retained, rejected = NoveltyFilter().filter(
        [generated],
        existing_candidates=[_existing("Existing", "CCCCCOc1ccccc1")],
        seeds=[],
        config=GenerationConfig(),
    )

    assert retained == []
    assert rejected[0].novelty is not None
    assert rejected[0].novelty.novelty_class == "near_duplicate"


def test_novelty_filter_retains_close_analogs():
    generated = _generated("close", "CCCCOc1ccccc1")

    retained, rejected = NoveltyFilter().filter(
        [generated],
        existing_candidates=[_existing("Existing", "CCCOc1ccccc1")],
        seeds=[_seed("Seed", "CCCOc1ccccc1")],
        config=GenerationConfig(),
    )

    assert rejected == []
    assert retained[0].novelty is not None
    assert retained[0].novelty.novelty_class == "close_analog"
    assert retained[0].novelty.nearest_existing_name == "Existing"


def test_novelty_filter_flags_distant_molecules_and_can_reject_them():
    generated = _generated("distant", "CCCCCCCC")

    retained, rejected = NoveltyFilter().filter(
        [generated],
        existing_candidates=[_existing("Existing", "CCOc1ccccc1")],
        seeds=[_seed("Seed", "CCOc1ccccc1")],
        config=GenerationConfig(reject_distant_generated_molecules=False),
    )

    assert rejected == []
    assert retained[0].novelty is not None
    assert retained[0].novelty.novelty_class == "distant"
    assert "distant_from_seed_context" in retained[0].warnings

    retained_rejecting, rejected_rejecting = NoveltyFilter().filter(
        [generated],
        existing_candidates=[_existing("Existing", "CCOc1ccccc1")],
        seeds=[_seed("Seed", "CCOc1ccccc1")],
        config=GenerationConfig(reject_distant_generated_molecules=True),
    )
    assert retained_rejecting == []
    assert rejected_rejecting[0].novelty is not None
    assert rejected_rejecting[0].novelty.novelty_class == "distant"


def test_diversity_filter_reduces_redundant_candidates():
    candidates = [
        _generated("top", "CCOc1ccccc1", score=0.9),
        _generated("redundant", "CCOc1ccccc1C", score=0.8),
        _generated("distinct", "CCCCCCCC", score=0.7),
    ]

    retained, rejected = DiversityFilter().filter(
        candidates,
        config=GenerationConfig(
            diversity_similarity_threshold=0.4,
            max_generated_per_diversity_cluster=1,
        ),
    )

    assert [candidate.generated_id for candidate in retained] == ["top", "distinct"]
    assert [candidate.generated_id for candidate in rejected] == ["redundant"]
    assert retained[0].diversity_cluster == "cluster-1"
    assert retained[1].diversity_cluster == "cluster-2"
