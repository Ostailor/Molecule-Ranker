from __future__ import annotations

from typing import Any

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.experiment_readiness import ExperimentReadinessAgent
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    GenerationRun,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.schemas import DevelopabilityAssessment


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
        metadata={"target_relevance_score": 0.82, "review_priority_context": 0.78},
    )


def _seed() -> SeedMolecule:
    return SeedMolecule(
        name="Seed A",
        canonical_smiles="CCOc1ccccc1",
        identifiers={"chembl": "CHEMBL_A"},
        known_targets=["MAOB"],
        source_candidate_name="Seed A",
        evidence_count=3,
        best_evidence_confidence=0.88,
        target_relevance_score=0.8,
        seed_selection_reason="Retrieved molecule-target evidence.",
    )


def _developability(score: float = 0.78, risk_level: str = "low") -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="gen-1",
        origin="generated",
        structure_available=True,
        canonical_smiles="CCOc1ccccn1",
        developability_score=score,
        triage_recommendation="high_risk_flags"
        if risk_level == "critical"
        else "favorable_hypothesis",
        metadata={"risk_level": risk_level},
    )


def _score_breakdown(**overrides: float) -> GeneratedMoleculeScoreBreakdown:
    payload = {
        "target_conditioning_score": 0.8,
        "seed_evidence_score": 0.84,
        "novelty_score": 0.72,
        "diversity_score": 0.8,
        "chemical_validity_score": 1.0,
        "property_profile_score": 0.78,
        "literature_context_score": 0.62,
        "developability_score": 0.78,
        "objective_alignment_score": 0.82,
        "generator_ensemble_score": 0.7,
        "uncertainty_score": 0.28,
        "medchem_critique_score": 0.82,
        "experiment_readiness_score": 0.74,
        "active_learning_priority_score": 0.55,
        "final_generation_score": 0.76,
        "confidence": 0.7,
        "explanation": "Generated hypothesis scored for computational triage only.",
    }
    payload.update(overrides)
    return GeneratedMoleculeScoreBreakdown(**payload)


def _generated(
    *,
    generated_id: str = "gen-1",
    validation: ChemicalValidationResult | None = None,
    novelty_class: str = "novel_analog",
    generation_score: float = 0.75,
    developability: DevelopabilityAssessment | None = None,
    metadata: dict[str, Any] | None = None,
    score_breakdown: GeneratedMoleculeScoreBreakdown | None = None,
    diversity_cluster: str | None = "cluster-a",
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
        descriptors={"molecular_weight": 200.0, "logp": 2.0, "tpsa": 30.0},
        fingerprints={},
        validation=validation
        or ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.38,
            max_similarity_to_seed=0.62,
            novelty_class=novelty_class,  # type: ignore[arg-type]
        ),
        diversity_cluster=diversity_cluster,
        generation_score=generation_score,
        score_breakdown=score_breakdown or _score_breakdown(),
        developability_assessment=developability or _developability(),
        warnings=["in_silico_hypothesis_only"],
        metadata=metadata or {},
    )


def _run(candidate: GeneratedMolecule) -> GenerationRun:
    return GenerationRun(
        objectives=[_objective()],
        seeds=[_seed()],
        generated=[candidate],
        retained=[candidate],
        rejected=[],
    )


def _context(candidate: GeneratedMolecule) -> PipelineContext:
    return PipelineContext(
        disease_input="Parkinson disease",
        config={"generation_run": _run(candidate)},
    )


def test_high_risk_molecule_rejected() -> None:
    candidate = _generated(
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
            rejection_reasons=["critical alert"],
        ),
        developability=_developability(0.2, "critical"),
        metadata={
            "medicinal_chemistry_critique": {
                "recommended_action": "reject",
                "concerns": ["Critical computational risk flag."],
            },
            "uncertainty": {"uncertainty_class": "uncontrolled_risk"},
        },
    )

    result = ExperimentReadinessAgent().run(_context(candidate))

    readiness = result.config["experiment_ready_candidates"][0]
    assert readiness.readiness_bucket == "reject"
    assert readiness.readiness_score <= 0.2
    assert readiness.blocking_risks
    updated = result.config["generation_run"].retained[0]
    assert updated.metadata["experiment_readiness_v1_1"]["readiness_bucket"] == "reject"


def test_poor_pose_decreases_experiment_readiness() -> None:
    base = _generated(metadata={})
    poor_pose = _generated(
        metadata={
            "structure_oracle": {
                "applicability_domain": "suitable_experimental_structure",
                "pose_qc_status": "reject",
                "consensus_score": 0.2,
                "not_experimental_evidence": True,
            }
        }
    )
    agent = ExperimentReadinessAgent()

    base_score = agent.score_candidate(
        candidate=base,
        objective=_objective(),
        parent_seeds=[_seed()],
        cluster_counts={"cluster-a": 2},
        config={"enable_structure_oracle": True},
    )
    poor_score = agent.score_candidate(
        candidate=poor_pose,
        objective=_objective(),
        parent_seeds=[_seed()],
        cluster_counts={"cluster-a": 2},
        config={"enable_structure_oracle": True},
    )

    assert poor_score.readiness_score < base_score.readiness_score
    assert "poor_structure_pose_qc" in poor_score.blocking_risks
    assert any("structure" in warning for warning in poor_score.warnings)


def test_diverse_plausible_molecule_retained_for_expert_review() -> None:
    candidate = _generated(
        metadata={
            "oracle_scoring": {
                "component_scores": {
                    "target_context_score": 0.82,
                    "novelty_score": 0.74,
                    "diversity_score": 0.84,
                    "experimental_gap_value": 0.76,
                    "seed_evidence_score": 0.84,
                },
                "risk_flags": [],
            },
            "uncertainty": {
                "uncertainty_class": "low_uncertainty",
                "active_learning_value": 0.36,
                "overall_uncertainty": 0.24,
                "experimental_gap_uncertainty": 0.7,
                "applicability_domain": "near_domain",
            },
            "medicinal_chemistry_critique": {
                "recommended_action": "retain_for_review",
                "confidence": 0.82,
                "concerns": [],
            },
            "assay_feasibility_context": {"score": 0.7},
        }
    )

    result = ExperimentReadinessAgent().run(_context(candidate))

    readiness = result.config["experiment_ready_candidates"][0]
    assert readiness.readiness_bucket == "ready_for_expert_review"
    assert 0.0 <= readiness.readiness_score <= 1.0
    assert "expert medchem review" in readiness.suggested_high_level_followup
    assert "human_review_required" in readiness.warnings


def test_uncertainty_candidate_uses_active_learning_bucket() -> None:
    candidate = _generated(
        generation_score=0.61,
        score_breakdown=_score_breakdown(
            objective_alignment_score=0.66,
            uncertainty_score=0.72,
            final_generation_score=0.61,
        ),
        metadata={
            "uncertainty": {
                "uncertainty_class": "interesting_uncertainty",
                "active_learning_value": 0.86,
                "overall_uncertainty": 0.67,
                "experimental_gap_uncertainty": 0.82,
                "applicability_domain": "near_domain",
            },
            "medicinal_chemistry_critique": {
                "recommended_action": "needs_expert_review",
                "concerns": ["Review uncertainty drivers."],
            },
        },
    )

    result = ExperimentReadinessAgent().run(_context(candidate))

    readiness = result.config["experiment_ready_candidates"][0]
    assert readiness.readiness_bucket == "active_learning_candidate"
    assert readiness.metadata["score_components"]["uncertainty_value"] > 0.6
    assert "additional computational uncertainty review" in (
        readiness.suggested_high_level_followup
    )


def test_no_lab_protocols_in_followup() -> None:
    result = ExperimentReadinessAgent().run(_context(_generated()))
    readiness = result.config["experiment_ready_candidates"][0]

    forbidden = ("protocol", "reagent", "reaction", "dosing", "animal", "patient")
    followup_text = " ".join(readiness.suggested_high_level_followup).lower()
    assert all(term not in followup_text for term in forbidden)
