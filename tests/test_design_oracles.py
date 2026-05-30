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
from molecule_ranker.schemas import DevelopabilityAssessment, EvidenceItem


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


def _structure_context(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "applicability_domain": "suitable_experimental_structure",
        "structure_selection_confidence": 0.82,
        "receptor_preparation_confidence": 0.72,
        "pose_qc_status": "pass",
        "pose_confidence": 0.52,
        "interaction_score": 0.58,
        "interaction_counts": {"hydrogen_bond_like": 1, "hydrophobic_contact": 1},
        "consensus_score": 0.68,
        "not_experimental_evidence": True,
    }
    payload.update(overrides)
    return payload


def test_structure_oracle_improves_ranking_only_modestly() -> None:
    stack = MultiObjectiveOracleStack()
    without_structure = stack.score(
        candidate=_generated("no-structure"),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_structure_oracle=True,
    )
    with_structure = stack.score(
        candidate=_generated(
            "with-structure",
            metadata={"structure_oracle": _structure_context(consensus_score=0.95)},
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_structure_oracle=True,
    )

    assert (
        with_structure.experiment_worthiness_score
        > without_structure.experiment_worthiness_score
    )
    assert (
        with_structure.experiment_worthiness_score
        - without_structure.experiment_worthiness_score
        <= 0.08
    )
    assert with_structure.oracle_by_name("consensus_structure_oracle").confidence <= 0.45
    assert with_structure.metadata["structure_oracle_enabled"] is True


def test_unavailable_structure_oracle_is_unknown_not_harsh_penalty() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(
            metadata={
                "structure_oracle": {
                    "applicability_domain": "unavailable",
                    "not_experimental_evidence": True,
                }
            }
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_structure_oracle=True,
    )

    consensus = result.oracle_by_name("consensus_structure_oracle")
    assert consensus.score == 0.5
    assert "structure_context_unknown" in consensus.risk_flags
    assert result.experiment_worthiness_score >= 0.45


def _prediction(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "prediction_id": "pred-1",
        "model_id": "model-1",
        "model_version": "1",
        "endpoint_id": "endpoint-maob",
        "predicted_probability": 0.82,
        "prediction_label": "positive",
        "uncertainty": 0.22,
        "confidence": 0.78,
        "applicability_domain": "in_domain",
        "calibration_status": "calibrated",
        "warnings": [],
        "not_evidence": True,
        "not_assay_result": True,
    }
    payload.update(overrides)
    return payload


def test_surrogate_absent_handled() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )

    surrogate = result.oracle_by_name("calibrated_surrogate_oracle")
    assert surrogate.metadata["available"] is False
    assert surrogate.score == 0.5
    assert "surrogate_absent" in surrogate.risk_flags


def test_calibrated_positive_prediction_increases_oracle_score_modestly() -> None:
    stack = MultiObjectiveOracleStack()
    objective = _objective()
    baseline = stack.score(
        candidate=_generated(),
        objective=objective,
        seeds=[_seed()],
        retained_generated=[],
    )
    with_prediction = stack.score(
        candidate=_generated(
            metadata={"scaffold_id": "new-scaffold", "model_predictions": [_prediction()]}
        ),
        objective=objective,
        seeds=[_seed()],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_endpoint_id="endpoint-maob",
        surrogate_oracle_weight=0.08,
    )

    surrogate = with_prediction.oracle_by_name("calibrated_surrogate_oracle")
    delta = with_prediction.experiment_worthiness_score - baseline.experiment_worthiness_score
    assert 0.0 < delta <= 0.04
    assert surrogate.score == 0.82
    assert surrogate.metadata["not_experimental_evidence"] is True
    assert surrogate.metadata["not_assay_result"] is True


def test_uncalibrated_prediction_ignored_or_warned() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(
            metadata={
                "scaffold_id": "new-scaffold",
                "model_predictions": [_prediction(calibration_status="uncalibrated")],
            }
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_endpoint_id="endpoint-maob",
        require_calibrated_predictions=True,
    )

    surrogate = result.oracle_by_name("calibrated_surrogate_oracle")
    assert surrogate.score == 0.5
    assert "surrogate_uncalibrated" in surrogate.risk_flags
    assert surrogate.metadata["available"] is False


def test_endpoint_mismatched_prediction_ignored() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(
            metadata={
                "scaffold_id": "new-scaffold",
                "model_predictions": [_prediction(endpoint_id="endpoint-other")],
            }
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_endpoint_id="endpoint-maob",
    )

    surrogate = result.oracle_by_name("calibrated_surrogate_oracle")
    assert surrogate.score == 0.5
    assert "surrogate_endpoint_mismatch" in surrogate.risk_flags


def test_out_of_domain_prediction_penalized() -> None:
    baseline = MultiObjectiveOracleStack().score(
        candidate=_generated(),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
    )
    out_of_domain = MultiObjectiveOracleStack().score(
        candidate=_generated(
            metadata={
                "scaffold_id": "new-scaffold",
                "model_predictions": [
                    _prediction(applicability_domain="out_of_domain", confidence=0.92)
                ],
            }
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_endpoint_id="endpoint-maob",
        out_of_domain_penalty=0.08,
    )

    assert out_of_domain.experiment_worthiness_score < baseline.experiment_worthiness_score
    assert "surrogate_out_of_domain" in out_of_domain.risk_flags


def test_model_prediction_does_not_become_evidence_item() -> None:
    result = MultiObjectiveOracleStack().score(
        candidate=_generated(
            metadata={"scaffold_id": "new-scaffold", "model_predictions": [_prediction()]}
        ),
        objective=_objective(),
        seeds=[_seed()],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_endpoint_id="endpoint-maob",
    )

    dumped = result.model_dump(mode="json")
    assert all(not isinstance(value, EvidenceItem) for value in dumped.values())
    assert "EvidenceItem" not in str(dumped)
    assert "activity evidence" not in str(dumped).lower()


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
