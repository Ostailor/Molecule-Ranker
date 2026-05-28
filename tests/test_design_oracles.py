from __future__ import annotations

from molecule_ranker.design.oracles import MultiObjectiveOracleStack
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationObjective,
    NoveltyAssessment,
    NoveltyClass,
    SeedMolecule,
)
from molecule_ranker.schemas import DevelopabilityAssessment


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        target_identifiers={"ensembl": "ENSG00000069535"},
        mechanism_hint="retrieved mechanism context",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
        metadata={"target_relevance_score": 0.84, "literature_context_score": 0.55},
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
        metadata={"seed_score": 0.82, "literature_support_score": 0.55, "scaffold_id": "s1"},
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


def _novelty(novelty_class: NoveltyClass = "novel_analog", seed_similarity: float = 0.62):
    return NoveltyAssessment(
        duplicate_of_existing=novelty_class == "duplicate",
        duplicate_of_generated=False,
        max_similarity_to_existing=0.35,
        nearest_existing_name="Existing",
        max_similarity_to_seed=seed_similarity,
        nearest_seed_name="Seed A",
        novelty_class=novelty_class,
    )


def _developability(score: float, risk_level: str = "low") -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="Generated",
        origin="generated",
        structure_available=True,
        canonical_smiles="CCOc1ccccn1",
        developability_score=score,
        triage_recommendation="high_risk_flags"
        if risk_level == "critical"
        else "favorable_hypothesis",
        metadata={"risk_level": risk_level},
    )


def _generated(
    generated_id: str = "gen-1",
    *,
    novelty_class: NoveltyClass = "novel_analog",
    validation: ChemicalValidationResult | None = None,
    diversity_cluster: str | None = "cluster-a",
    developability: DevelopabilityAssessment | None = None,
    metadata: dict | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles="CCOc1ccccn1",
        canonical_smiles="CCOc1ccccn1",
        generation_method="fragment_grower",
        parent_seed_ids=["CHEMBL_A"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={
            "molecular_weight": 200.0,
            "logp": 2.1,
            "tpsa": 30.0,
            "rotatable_bonds": 4.0,
        },
        fingerprints={},
        validation=validation or _validation(),
        novelty=_novelty(novelty_class),
        diversity_cluster=diversity_cluster,
        developability_assessment=developability,
        warnings=["in_silico_hypothesis_only"],
        metadata=metadata or {"scaffold_id": "new-scaffold"},
    )


def test_each_oracle_returns_bounded_score() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    assert len(result.oracles) == 12
    for oracle in result.oracles:
        assert 0.0 <= oracle.score <= 1.0
        assert 0.0 <= oracle.confidence <= 1.0
        assert oracle.oracle_name
        assert oracle.explanation
    assert 0.0 <= result.experiment_worthiness_score <= 1.0


def test_critical_risk_lowers_composite() -> None:
    stack = MultiObjectiveOracleStack()
    low_risk = stack.score(
        candidate=_generated(developability=_developability(0.8)),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )
    critical = stack.score(
        candidate=_generated(developability=_developability(0.8, "critical")),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    assert critical.experiment_worthiness_score < low_risk.experiment_worthiness_score
    assert critical.experiment_worthiness_score <= 0.3
    assert "critical_developability_risk" in critical.risk_flags


def test_diversity_improves_selection() -> None:
    retained = [_generated("retained", diversity_cluster="cluster-a")]
    duplicate_cluster = MultiObjectiveOracleStack().score(
        candidate=_generated("same-cluster", diversity_cluster="cluster-a"),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=retained,
    )
    diverse = MultiObjectiveOracleStack().score(
        candidate=_generated("new-cluster", diversity_cluster="cluster-b"),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=retained,
    )

    assert diverse.component_scores["diversity_score"] > duplicate_cluster.component_scores[
        "diversity_score"
    ]
    assert diverse.experiment_worthiness_score > duplicate_cluster.experiment_worthiness_score


def test_docking_disabled_by_default() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    docking = result.oracle_by_name("docking_oracle")
    assert docking.metadata["enabled"] is False
    assert "docking_disabled" in docking.risk_flags


def test_surrogate_absent_handled() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    surrogate = result.oracle_by_name("surrogate_activity_oracle")
    assert surrogate.metadata["available"] is False
    assert surrogate.score == 0.5
    assert "surrogate_absent" in surrogate.risk_flags


def test_score_explanation_cautious() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    explanation = result.explanation.lower()
    assert "experiment worthiness" in explanation
    assert "not predicted efficacy" in explanation
    assert "not predicted binding" in explanation
