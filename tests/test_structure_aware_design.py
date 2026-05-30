from __future__ import annotations

from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    NoveltyAssessment,
)
from molecule_ranker.schemas import DevelopabilityAssessment
from molecule_ranker.structure.schemas import StructureAwareAssessment
from molecule_ranker.structure.structure_aware_design import StructureAwareGenerationLoop


def _validation() -> ChemicalValidationResult:
    return ChemicalValidationResult(
        valid_rdkit_mol=True,
        sanitization_ok=True,
        canonicalization_ok=True,
        allowed_elements_ok=True,
        descriptor_bounds_ok=True,
    )


def _novelty(novelty_class: str = "novel_analog") -> NoveltyAssessment:
    return NoveltyAssessment(
        duplicate_of_existing=False,
        duplicate_of_generated=False,
        max_similarity_to_existing=0.35,
        max_similarity_to_seed=0.62,
        novelty_class=novelty_class,  # type: ignore[arg-type]
    )


def _breakdown(
    *,
    novelty: float = 0.75,
    diversity: float = 0.75,
    uncertainty: float = 0.35,
    developability: float = 0.75,
) -> GeneratedMoleculeScoreBreakdown:
    return GeneratedMoleculeScoreBreakdown(
        target_conditioning_score=0.7,
        seed_evidence_score=0.7,
        novelty_score=novelty,
        diversity_score=diversity,
        chemical_validity_score=0.9,
        property_profile_score=0.75,
        literature_context_score=0.55,
        developability_score=developability,
        objective_alignment_score=0.72,
        generator_ensemble_score=0.7,
        uncertainty_score=uncertainty,
        medchem_critique_score=0.75,
        experiment_readiness_score=0.72,
        active_learning_priority_score=0.65,
        final_generation_score=0.72,
        confidence=0.7,
        explanation="Generated molecule score for test.",
    )


def _developability(score: float = 0.75, risk: str = "low") -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="Generated",
        origin="generated",
        structure_available=True,
        canonical_smiles="CCOc1ccccn1",
        developability_score=score,
        triage_recommendation="high_risk_flags"
        if risk == "critical"
        else "favorable_hypothesis",
        metadata={"risk_level": risk},
    )


def _candidate(
    molecule_id: str,
    *,
    cluster: str,
    novelty: float = 0.75,
    diversity: float = 0.75,
    uncertainty: float = 0.35,
    developability: float = 0.75,
    risk: str = "low",
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=molecule_id,
        smiles="CCOc1ccccn1",
        canonical_smiles=f"CCOc1ccccn1.{molecule_id}",
        generation_method="fragment_grower",
        parent_seed_ids=["seed-1"],
        conditioned_targets=["LRRK2"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 250.0, "logp": 2.4},
        fingerprints={},
        validation=_validation(),
        novelty=_novelty(),
        diversity_cluster=cluster,
        score_breakdown=_breakdown(
            novelty=novelty,
            diversity=diversity,
            uncertainty=uncertainty,
            developability=developability,
        ),
        developability_assessment=_developability(developability, risk),
        warnings=["in_silico_hypothesis_only"],
        metadata={"scaffold_id": cluster, "uncertainty": {"overall_uncertainty": uncertainty}},
    )


def _assessment(
    molecule_id: str,
    *,
    consensus: float = 0.7,
    pose: float = 0.7,
    interaction: float = 0.65,
    domain: str = "suitable_experimental_structure",
    recommendation: str = "retain_for_review",
    warnings: list[str] | None = None,
    docking_score: float = 0.55,
    pose_qc_score: float = 0.75,
) -> StructureAwareAssessment:
    return StructureAwareAssessment(
        assessment_id=f"assessment-{molecule_id}",
        molecule_id=molecule_id,
        molecule_name=molecule_id,
        target_symbol="LRRK2",
        structure_id="RCSB_PDB:6LIG",
        docking_pose_ids=[f"pose-{molecule_id}"],
        structure_score=0.75,
        pose_confidence=pose,
        interaction_score=interaction,
        consensus_score=consensus,
        applicability_domain=domain,  # type: ignore[arg-type]
        recommendation=recommendation,  # type: ignore[arg-type]
        warnings=warnings or ["Structure scores are not activity evidence."],
        explanation=(
            "Conservative structure-aware prioritization; not proof of binding "
            "and not activity evidence."
        ),
        metadata={
            "component_scores": {
                "docking_score": docking_score,
                "pose_qc_score": pose_qc_score,
                "interaction_profile_score": interaction,
            },
            "docking_scores_not_proof_of_binding": True,
            "structure_scores_not_activity_evidence": True,
        },
    )


def test_poor_pose_not_selected() -> None:
    good = _candidate("good", cluster="cluster-a")
    poor_pose = _candidate("poor-pose", cluster="cluster-b", diversity=0.95)

    result = StructureAwareGenerationLoop().plan_next_round(
        generated_candidates=[poor_pose, good],
        assessments=[
            _assessment("poor-pose", consensus=0.8, pose=0.05, recommendation="reject"),
            _assessment("good", consensus=0.66, pose=0.68),
        ],
        batch_size=1,
    )

    assert [candidate.molecule_id for candidate in result.selected_candidates] == ["good"]
    assert "poor_structure_pose_qc" in result.candidate_by_id("poor-pose").risk_flags
    assert "structure-aware prioritization" in result.report_markdown
    assert "binding optimization" not in result.report_markdown.lower()


def test_diversity_preserved() -> None:
    candidates = [
        _candidate("a1", cluster="cluster-a"),
        _candidate("a2", cluster="cluster-a"),
        _candidate("b1", cluster="cluster-b", novelty=0.7),
    ]

    result = StructureAwareGenerationLoop().plan_next_round(
        generated_candidates=candidates,
        assessments=[
            _assessment("a1", consensus=0.9),
            _assessment("a2", consensus=0.88),
            _assessment("b1", consensus=0.7),
        ],
        batch_size=2,
    )

    selected_clusters = {candidate.diversity_cluster for candidate in result.selected_candidates}
    assert selected_clusters == {"cluster-a", "cluster-b"}
    assert result.metadata["diversity_preserved"] is True


def test_docking_score_alone_not_sufficient() -> None:
    docking_only = _candidate("docking-only", cluster="cluster-a", novelty=0.9)
    balanced = _candidate("balanced", cluster="cluster-b", novelty=0.72)

    result = StructureAwareGenerationLoop().plan_next_round(
        generated_candidates=[docking_only, balanced],
        assessments=[
            _assessment(
                "docking-only",
                consensus=0.92,
                pose=0.2,
                interaction=0.0,
                docking_score=1.0,
                pose_qc_score=0.0,
                warnings=["Docking score alone is not sufficient."],
            ),
            _assessment("balanced", consensus=0.62, pose=0.65, interaction=0.65),
        ],
        batch_size=1,
    )

    assert [candidate.molecule_id for candidate in result.selected_candidates] == ["balanced"]
    assert "docking_score_alone_not_sufficient" in result.candidate_by_id(
        "docking-only"
    ).risk_flags


def test_structure_aware_loop_report_generated() -> None:
    result = StructureAwareGenerationLoop().plan_next_round(
        generated_candidates=[
            _candidate("generated-1", cluster="cluster-a"),
            _candidate("generated-2", cluster="cluster-b", risk="critical"),
        ],
        assessments=[
            _assessment("generated-1"),
            _assessment("generated-2", consensus=0.8),
        ],
        batch_size=1,
    )

    assert result.report["title"] == "Structure-aware generation loop"
    assert result.report["human_review_required"] is True
    assert result.report["selected_molecule_ids"] == ["generated-1"]
    assert "structure-aware prioritization" in result.report_markdown
    assert "Human review remains required." in result.report_markdown
    assert "critical_developability_risk" in result.candidate_by_id(
        "generated-2"
    ).risk_flags
