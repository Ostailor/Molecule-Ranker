from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)


def _objective(**overrides: Any) -> GenerationObjective:
    payload: dict[str, Any] = {
        "objective_id": "objective-1",
        "disease_name": "Parkinson disease",
        "target_symbol": "MAOB",
        "target_name": "Monoamine oxidase B",
        "target_identifiers": {"ensembl": "ENSG00000069535"},
        "mechanism_hint": "MAO-B inhibition",
        "seed_molecule_names": ["Rasagiline"],
        "seed_molecule_ids": ["CHEMBL887"],
        "objective_type": "target_conditioned_analog_generation",
        "constraints": {"max_molecular_weight": 500},
        "metadata": {"source": "unit-test"},
    }
    payload.update(overrides)
    return GenerationObjective(**payload)


def _seed(**overrides: Any) -> SeedMolecule:
    payload: dict[str, Any] = {
        "name": "Rasagiline",
        "canonical_smiles": "C#CCN(C)CCc1ccccc1",
        "identifiers": {"chembl": "CHEMBL887"},
        "known_targets": ["MAOB"],
        "source_candidate_name": "Rasagiline",
        "evidence_count": 3,
        "best_evidence_confidence": 0.91,
        "target_relevance_score": 0.84,
        "seed_selection_reason": "Highest-confidence evidence-backed MAOB seed.",
        "metadata": {"rank": 1},
    }
    payload.update(overrides)
    return SeedMolecule(**payload)


def _validation(**overrides: Any) -> ChemicalValidationResult:
    payload: dict[str, Any] = {
        "valid_rdkit_mol": True,
        "sanitization_ok": True,
        "canonicalization_ok": True,
        "allowed_elements_ok": True,
        "descriptor_bounds_ok": True,
        "pains_or_alerts": [],
        "rejection_reasons": [],
        "metadata": {"rdkit_version": "test"},
    }
    payload.update(overrides)
    return ChemicalValidationResult(**payload)


def _novelty(**overrides: Any) -> NoveltyAssessment:
    payload: dict[str, Any] = {
        "duplicate_of_existing": False,
        "duplicate_of_generated": False,
        "max_similarity_to_existing": 0.44,
        "nearest_existing_name": "Rasagiline",
        "max_similarity_to_seed": 0.62,
        "nearest_seed_name": "Rasagiline",
        "novelty_class": "novel_analog",
        "metadata": {"fingerprint": "morgan"},
    }
    payload.update(overrides)
    return NoveltyAssessment(**payload)


def _score(**overrides: Any) -> GeneratedMoleculeScoreBreakdown:
    payload: dict[str, Any] = {
        "target_conditioning_score": 0.8,
        "seed_evidence_score": 0.9,
        "novelty_score": 0.7,
        "diversity_score": 0.6,
        "chemical_validity_score": 1.0,
        "property_profile_score": 0.75,
        "literature_context_score": 0.2,
        "final_generation_score": 0.72,
        "confidence": 0.64,
        "explanation": "Generated hypothesis scored from target conditioning and seed context.",
    }
    payload.update(overrides)
    return GeneratedMoleculeScoreBreakdown(**payload)


def _generated(**overrides: Any) -> GeneratedMolecule:
    payload: dict[str, Any] = {
        "generated_id": "gen-1",
        "smiles": "C#CCN(C)CCc1ccccn1",
        "canonical_smiles": "C#CCN(C)CCc1ccccn1",
        "selfies": "[C][#C][C][N][Branch1][C][C][C]",
        "inchi_key": "TEST-INCHI-KEY",
        "generation_method": "selfies_mutation_crossover",
        "parent_seed_ids": ["CHEMBL887"],
        "conditioned_targets": ["MAOB"],
        "objective_id": "objective-1",
        "generation_round": 1,
        "descriptors": {"molecular_weight": 210.3, "clogp": 2.1},
        "fingerprints": {"morgan": {"bits": [1, 7, 23]}},
        "validation": _validation(),
        "novelty": _novelty(),
        "diversity_cluster": "cluster-1",
        "generation_score": 0.72,
        "score_breakdown": _score(),
        "warnings": ["in_silico_hypothesis_only"],
        "metadata": {"generator_version": "test"},
    }
    payload.update(overrides)
    return GeneratedMolecule(**payload)


def test_generation_run_schema_serializes_nested_models():
    run = GenerationRun(
        objectives=[_objective()],
        seeds=[_seed()],
        generated=[_generated()],
        retained=[_generated(generated_id="gen-retained")],
        rejected=[
            _generated(
                generated_id="gen-rejected",
                validation=_validation(valid_rdkit_mol=False),
            )
        ],
        warnings=["Generated molecules are in-silico research hypotheses only."],
        metadata={"run_id": "generation-run-1"},
    )

    payload = run.model_dump(mode="json")

    assert payload["objectives"][0]["objective_type"] == "target_conditioned_analog_generation"
    assert payload["seeds"][0]["best_evidence_confidence"] == 0.91
    assert payload["generated"][0]["origin"] == "generated"
    assert payload["generated"][0]["validation"]["valid_rdkit_mol"] is True
    assert payload["generated"][0]["novelty"]["novelty_class"] == "novel_analog"
    assert payload["retained"][0]["score_breakdown"]["final_generation_score"] == 0.72
    assert '"GenerationRun"' not in run.model_dump_json()


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _objective(objective_type="unsupported"), "objective_type"),
        (lambda: _seed(best_evidence_confidence=1.01), "best_evidence_confidence"),
        (lambda: _seed(target_relevance_score=-0.01), "target_relevance_score"),
        (lambda: _novelty(max_similarity_to_existing=1.2), "max_similarity_to_existing"),
        (lambda: _novelty(max_similarity_to_seed=-0.2), "max_similarity_to_seed"),
        (lambda: _novelty(novelty_class="validated_hit"), "novelty_class"),
        (lambda: _score(target_conditioning_score=1.1), "target_conditioning_score"),
        (lambda: _score(final_generation_score=-0.01), "final_generation_score"),
        (lambda: _generated(generation_score=1.1), "generation_score"),
    ],
)
def test_generation_schema_scores_and_enums_are_validated(factory, field_name):
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)


def test_generated_molecule_rejects_experimental_validation_claims():
    with pytest.raises(ValidationError) as error:
        _generated(metadata={"experimentally_validated": True})

    assert "experimentally_validated" in str(error.value)
