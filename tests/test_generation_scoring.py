from __future__ import annotations

from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationObjective,
    NoveltyAssessment,
    NoveltyClass,
    SeedMolecule,
)
from molecule_ranker.generation.scoring import GeneratedMoleculeScorer
from molecule_ranker.schemas import DevelopabilityAssessment


def _objective(literature_score: float = 0.6) -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        target_identifiers={"ensembl": "ENSG00000069535"},
        mechanism_hint="Retrieved MAOB mechanism context.",
        seed_molecule_names=["Rasagiline"],
        seed_molecule_ids=["CHEMBL887"],
        objective_type="target_conditioned_analog_generation",
        constraints={
            "molecular_weight": {"min": 150.0, "max": 350.0},
            "logp": {"min": -1.0, "max": 5.0},
            "tpsa": {"min": 0.0, "max": 140.0},
        },
        metadata={
            "target_relevance_score": 0.84,
            "literature_context_score": literature_score,
        },
    )


def _seed(
    name: str = "Rasagiline",
    seed_id: str = "CHEMBL887",
    seed_score: float = 0.82,
    literature_score: float = 0.6,
) -> SeedMolecule:
    return SeedMolecule(
        name=name,
        canonical_smiles="C#CCN(C)Cc1ccccc1",
        identifiers={"chembl": seed_id},
        known_targets=["MAOB"],
        source_candidate_name=name,
        evidence_count=3,
        best_evidence_confidence=0.9,
        target_relevance_score=0.84,
        seed_selection_reason="Evidence-backed seed.",
        metadata={
            "seed_score": seed_score,
            "literature_support_score": literature_score,
            "matched_targets": ["MAOB"],
        },
    )


def _validation(**overrides) -> ChemicalValidationResult:
    payload = {
        "valid_rdkit_mol": True,
        "sanitization_ok": True,
        "canonicalization_ok": True,
        "allowed_elements_ok": True,
        "descriptor_bounds_ok": True,
        "pains_or_alerts": [],
        "rejection_reasons": [],
    }
    payload.update(overrides)
    return ChemicalValidationResult(**payload)


def _novelty(
    novelty_class: NoveltyClass = "novel_analog",
    seed_similarity: float = 0.62,
):
    return NoveltyAssessment(
        duplicate_of_existing=novelty_class == "duplicate",
        duplicate_of_generated=False,
        max_similarity_to_existing=0.44,
        nearest_existing_name="Existing",
        max_similarity_to_seed=seed_similarity,
        nearest_seed_name="Rasagiline",
        novelty_class=novelty_class,
    )


def _generated(
    generated_id: str = "gen-1",
    *,
    novelty_class: NoveltyClass = "novel_analog",
    validation: ChemicalValidationResult | None = None,
    descriptors: dict[str, float] | None = None,
    diversity_cluster: str | None = "cluster-1",
    developability_assessment: DevelopabilityAssessment | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles="C#CCN(C)Cc1ccccn1",
        canonical_smiles="C#CCN(C)Cc1ccccn1",
        generation_method="selfies_mutation",
        parent_seed_ids=["CHEMBL887"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors=descriptors
        or {"molecular_weight": 200.0, "logp": 2.1, "tpsa": 30.0},
        fingerprints={},
        validation=validation or _validation(),
        novelty=_novelty(novelty_class),
        diversity_cluster=diversity_cluster,
        developability_assessment=developability_assessment,
        warnings=["in_silico_hypothesis_only"],
        metadata={},
    )


def _developability(
    *,
    score: float,
    risk_level: str = "low",
    recommendation: str = "favorable_hypothesis",
) -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="generated",
        origin="generated",
        structure_available=True,
        canonical_smiles="CCO",
        developability_score=score,
        triage_recommendation=recommendation,  # type: ignore[arg-type]
        metadata={"risk_level": risk_level},
    )


def test_generated_molecule_scores_are_bounded():
    scored = GeneratedMoleculeScorer().score(
        [_generated()],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    breakdown = scored[0].score_breakdown
    assert breakdown is not None
    for key, value in breakdown.model_dump(exclude={"explanation"}).items():
        assert 0.0 <= value <= 1.0, key
    assert scored[0].generation_score == breakdown.final_generation_score


def test_low_developability_reduces_generated_ranking():
    low = _generated(
        generated_id="low-dev",
        developability_assessment=_developability(
            score=0.1,
            risk_level="high",
            recommendation="high_risk_flags",
        ),
    )
    high = _generated(
        generated_id="high-dev",
        developability_assessment=_developability(score=0.9),
    )

    scored = GeneratedMoleculeScorer().score(
        [low, high],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    assert [candidate.generated_id for candidate in scored] == ["high-dev", "low-dev"]
    assert scored[0].score_breakdown is not None
    assert scored[0].score_breakdown.developability_score == 0.9
    assert scored[1].generation_score is not None
    assert scored[1].generation_score <= 0.45


def test_critical_developability_rejects_generated_molecule():
    critical = _generated(
        developability_assessment=_developability(
            score=0.1,
            risk_level="critical",
            recommendation="high_risk_flags",
        )
    )

    scored = GeneratedMoleculeScorer().score(
        [critical],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    assert "developability_critical_risk" in scored[0].validation.rejection_reasons
    assert scored[0].generation_score is not None
    assert 0.0 <= scored[0].generation_score <= 1.0


def test_duplicate_molecules_score_low():
    scored = GeneratedMoleculeScorer().score(
        [_generated(novelty_class="duplicate")],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    assert scored[0].score_breakdown is not None
    assert scored[0].score_breakdown.novelty_score == 0.0
    assert scored[0].generation_score is not None
    assert scored[0].generation_score < 0.6


def test_generated_confidence_is_capped():
    scored = GeneratedMoleculeScorer().score(
        [_generated()],
        objectives=[_objective(literature_score=1.0)],
        seeds=[_seed(seed_score=1.0, literature_score=1.0)],
        retained_generated=[],
    )

    assert scored[0].score_breakdown is not None
    assert scored[0].score_breakdown.confidence <= 0.45


def test_literature_context_does_not_create_direct_generated_molecule_evidence():
    generated = _generated()

    scored = GeneratedMoleculeScorer().score(
        [generated],
        objectives=[_objective(literature_score=0.9)],
        seeds=[_seed(literature_score=0.8)],
        retained_generated=[],
    )

    assert scored[0].score_breakdown is not None
    assert scored[0].score_breakdown.literature_context_score > 0
    assert scored[0].metadata["direct_generated_molecule_literature_evidence"] is False
    assert "no direct experimental evidence" in scored[0].score_breakdown.explanation.lower()
    assert generated.metadata == {}


def test_explanations_are_clear_and_cautious():
    scored = GeneratedMoleculeScorer().score(
        [_generated(novelty_class="close_analog")],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    explanation = scored[0].score_breakdown.explanation if scored[0].score_breakdown else ""
    assert "in-silico research hypothesis" in explanation
    assert "not predicted binding affinity" in explanation
    assert "not ADMET" in explanation
    assert "no direct experimental evidence" in explanation


def test_v1_1_score_breakdown_includes_uncertainty_readiness_and_medchem_critique():
    generated = _generated(
        descriptors={
            "molecular_weight": 205.0,
            "logp": 2.0,
            "tpsa": 35.0,
            "heavy_atom_count": 14.0,
        }
    )

    scored = GeneratedMoleculeScorer().score(
        [generated],
        objectives=[_objective()],
        seeds=[_seed()],
        retained_generated=[],
    )

    breakdown = scored[0].score_breakdown
    assert breakdown is not None
    assert breakdown.objective_alignment_score > 0
    assert breakdown.medchem_critique_score > 0
    assert breakdown.uncertainty_score > 0
    assert breakdown.experiment_readiness_score > 0
    assert breakdown.active_learning_priority_score > 0
    assert scored[0].metadata["scoring_policy"] == "v1.1_generated_hypothesis_only"
    assert scored[0].metadata["experiment_readiness"]["label"] in {
        "review_ready",
        "needs_review",
        "deprioritized",
    }
    assert scored[0].metadata["medicinal_chemistry_critique"]["scope"] == (
        "non-protocol computational critique"
    )
    assert scored[0].metadata["uncertainty"]["claim_boundary"] == (
        "uncertainty describes computational triage only"
    )
