from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.design.active_design import (
    ActiveDesignResult,
    ActiveDesignStrategy,
    ActiveLearningDesignPlanner,
)
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    NoveltyAssessment,
)


def _objective() -> GenerationObjective:
    return GenerationObjective(
        objective_id="objective-1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        seed_molecule_names=["Seed A"],
        seed_molecule_ids=["CHEMBL_A"],
        objective_type="target_conditioned_analog_generation",
        metadata={"target_relevance_score": 0.82},
    )


def _score(**overrides: float) -> GeneratedMoleculeScoreBreakdown:
    payload = {
        "target_conditioning_score": 0.8,
        "seed_evidence_score": 0.82,
        "novelty_score": 0.72,
        "diversity_score": 0.7,
        "chemical_validity_score": 1.0,
        "property_profile_score": 0.76,
        "literature_context_score": 0.55,
        "developability_score": 0.76,
        "objective_alignment_score": 0.82,
        "generator_ensemble_score": 0.7,
        "uncertainty_score": 0.35,
        "medchem_critique_score": 0.75,
        "experiment_readiness_score": 0.72,
        "active_learning_priority_score": 0.62,
        "final_generation_score": 0.74,
        "confidence": 0.68,
        "explanation": "Computational triage score only.",
    }
    payload.update(overrides)
    return GeneratedMoleculeScoreBreakdown(**payload)


def _generated(
    generated_id: str,
    smiles: str,
    *,
    scaffold_id: str,
    oracle_score: float = 0.74,
    novelty: str = "novel_analog",
    diversity: float = 0.7,
    uncertainty_value: float = 0.48,
    model_predictions: list[dict[str, object]] | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles=smiles,
        canonical_smiles=smiles,
        generation_method="fragment_grower",
        parent_seed_ids=["CHEMBL_A"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 190.0, "logp": 2.1},
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
            novelty_class=novelty,  # type: ignore[arg-type]
        ),
        diversity_cluster=scaffold_id,
        generation_score=oracle_score,
        score_breakdown=_score(
            diversity_score=diversity,
            active_learning_priority_score=uncertainty_value,
            final_generation_score=oracle_score,
        ),
        metadata={
            "scaffold_id": scaffold_id,
            **({"model_predictions": model_predictions} if model_predictions else {}),
            "oracle_scoring": {
                "experiment_worthiness_score": oracle_score,
                "component_scores": {
                    "diversity_score": diversity,
                    "uncertainty_value": uncertainty_value,
                    "experimental_gap_value": 0.7,
                },
                "risk_flags": [],
            },
            "uncertainty": {
                "active_learning_value": uncertainty_value,
                "uncertainty_class": "interesting_uncertainty"
                if uncertainty_value >= 0.65
                else "low_uncertainty",
            },
        },
    )


def _endpoint(
    *,
    name: str = "target_engagement",
    category: str = "target_engagement",
) -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id=f"endpoint-{name}",
        name=name,
        endpoint_category=category,  # type: ignore[arg-type]
        directionality="binary",
    )


def _result(
    result_id: str,
    *,
    smiles: str,
    outcome_label: str,
    activity_direction: str,
    category: str = "target_engagement",
    target_symbol: str = "MAOB",
) -> AssayResult:
    endpoint = _endpoint(category=category)
    return AssayResult(
        result_id=result_id,
        candidate_id=None,
        candidate_name=result_id,
        candidate_origin="generated",
        canonical_smiles=smiles,
        disease_name="Parkinson disease",
        target_symbol=target_symbol,
        assay_context=AssayContext(
            assay_context_id=f"context-{result_id}",
            assay_name="high level imported result",
            assay_type="safety" if category == "safety" else "biochemical",
            target_symbol=target_symbol,
            disease_name="Parkinson disease",
            endpoint=endpoint,
        ),
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=0.8,
        qc_status="passed",
        source="csv_import",
        imported_at=datetime.now(UTC),
    )


def _plan(
    *,
    strategy: ActiveDesignStrategy = "balanced",
    results: list[AssayResult] | None = None,
) -> tuple[GeneratedMolecule, GeneratedMolecule, ActiveDesignResult]:
    lead = _generated("gen-1", "CCOc1ccccn1", scaffold_id="scaffold-a", oracle_score=0.78)
    diverse = _generated(
        "gen-2",
        "N#Cc1ccccc1O",
        scaffold_id="scaffold-b",
        oracle_score=0.66,
        novelty="distant",
        diversity=0.9,
        uncertainty_value=0.72,
    )
    result = ActiveLearningDesignPlanner().plan_next_round(
        objectives=[_objective()],
        generated_candidates=[lead, diverse],
        experimental_results=results or [],
        strategy=strategy,
        batch_size=2,
    )
    return lead, diverse, result


def test_feedback_shifts_next_design_plan() -> None:
    lead, _, result = _plan(
        results=[
            _result(
                "positive-gen-1",
                smiles="CCOc1ccccn1",
                outcome_label="positive",
                activity_direction="active",
            )
        ]
    )

    assert result.suggested_focus == "exploit_scaffold"
    assert result.next_design_plan.generator_strategy["next_focus"] == "exploit_scaffold"
    assert (
        lead.metadata["scaffold_id"]
        in result.next_design_plan.seed_strategy["preferred_scaffolds"]
    )
    assert result.metadata["experimental_feedback_used"] is True


def test_negative_result_reduces_exploit_score() -> None:
    no_feedback_signal = ActiveLearningDesignPlanner().score_candidate(
        _generated("gen-1", "CCOc1ccccn1", scaffold_id="scaffold-a"),
        experimental_results=[],
    )
    negative_signal = ActiveLearningDesignPlanner().score_candidate(
        _generated("gen-1", "CCOc1ccccn1", scaffold_id="scaffold-a"),
        experimental_results=[
            _result(
                "negative-gen-1",
                smiles="CCOc1ccccn1",
                outcome_label="negative",
                activity_direction="inactive",
            )
        ],
    )

    assert negative_signal.exploit_score < no_feedback_signal.exploit_score
    assert negative_signal.exact_feedback_result_ids == ["negative-gen-1"]
    assert negative_signal.metadata["surrogate_estimates_are_not_evidence"] is True


def test_safety_result_shifts_risk_reduction() -> None:
    _, _, result = _plan(
        strategy="balanced",
        results=[
            _result(
                "safety-gen-1",
                smiles="CCOc1ccccn1",
                outcome_label="negative",
                activity_direction="toxic",
                category="safety",
            )
        ],
    )

    assert result.selected_strategy == "risk_reduction"
    assert result.suggested_focus == "reduce_toxicity_risk"
    assert result.next_design_plan.oracle_strategy["emphasize"] == ["toxicity_risk"]


def test_no_feedback_uses_oracle_only_strategy() -> None:
    _, _, result = _plan(strategy="balanced", results=[])

    assert result.metadata["experimental_feedback_used"] is False
    assert result.metadata["selection_basis"] == "oracle_only"
    assert result.next_design_plan.metadata["surrogate_estimates_are_not_evidence"] is True
    followup_text = " ".join(
        str(item.get("action", "")) for item in result.next_design_plan.required_followups
    ).lower()
    assert "protocol" not in followup_text
    assert "reagent" not in followup_text


def test_active_design_rationale_mentions_model_uncertainty() -> None:
    prediction = {
        "prediction_id": "pred-1",
        "model_id": "model-1",
        "model_version": "1",
        "endpoint_id": "endpoint-maob",
        "predicted_probability": 0.68,
        "prediction_label": "positive",
        "uncertainty": 0.74,
        "confidence": 0.72,
        "applicability_domain": "near_domain",
        "calibration_status": "calibrated",
        "warnings": [],
        "not_evidence": True,
        "not_assay_result": True,
    }
    signal = ActiveLearningDesignPlanner().score_candidate(
        _generated(
            "gen-model",
            "CCOc1ccccn1",
            scaffold_id="scaffold-model",
            model_predictions=[prediction],
        ),
        experimental_results=[],
    )

    influence = signal.metadata["model_influence"]
    assert influence["prediction_count"] == 1
    assert "model uncertainty" in influence["rationale"].lower()
    assert influence["not_evidence"] is True
    assert signal.metadata["surrogate_estimates_are_not_evidence"] is True
