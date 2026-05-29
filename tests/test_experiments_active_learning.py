from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.experiments.active_learning import (
    build_experimental_learning_dataset,
    suggest_next_experiments,
)
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.review.schemas import Reviewer, ReviewerDecision, ReviewItem
from molecule_ranker.schemas import DevelopabilityAssessment, MoleculeCandidate


def _endpoint(
    name: str = "binding_affinity",
    *,
    category: str = "potency",
    directionality: str = "lower_is_better",
) -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id=f"endpoint-{name}",
        name=name,
        endpoint_category=category,  # type: ignore[arg-type]
        unit="nM",
        directionality=directionality,  # type: ignore[arg-type]
        metadata={},
    )


def _context(
    *,
    endpoint: AssayEndpoint | None = None,
    disease_name: str = "Parkinson disease",
    target_symbol: str = "MAOB",
    assay_type: str = "biochemical",
) -> AssayContext:
    endpoint = endpoint or _endpoint()
    return AssayContext(
        assay_context_id=f"context-{endpoint.name}",
        assay_name=f"{endpoint.name} assay",
        assay_type=assay_type,  # type: ignore[arg-type]
        target_symbol=target_symbol,
        disease_name=disease_name,
        model_system="recombinant_protein",
        species="human",
        endpoint=endpoint,
    )


def _result(
    result_id: str,
    *,
    candidate_id: str | None = "CHEMBL887",
    candidate_name: str = "Rasagiline",
    candidate_origin: str = "existing",
    canonical_smiles: str | None = "C#CCN1CCC2=CC=CC=C21",
    endpoint: AssayEndpoint | None = None,
    disease_name: str = "Parkinson disease",
    target_symbol: str = "MAOB",
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    measured_value_numeric: float | None = 12.0,
    normalized_value: float | None = 12.0,
) -> AssayResult:
    assay_type = "safety" if endpoint and endpoint.endpoint_category == "safety" else "biochemical"
    context = _context(
        endpoint=endpoint,
        disease_name=disease_name,
        target_symbol=target_symbol,
        assay_type=assay_type,
    )
    return AssayResult(
        result_id=result_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin=candidate_origin,  # type: ignore[arg-type]
        canonical_smiles=canonical_smiles,
        disease_name=disease_name,
        target_symbol=target_symbol,
        assay_context=context,
        measured_value=measured_value_numeric,
        measured_value_numeric=measured_value_numeric,
        unit="nM",
        normalized_value=normalized_value,
        normalized_unit="nM" if normalized_value is not None else None,
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=0.8,
        qc_status=qc_status,  # type: ignore[arg-type]
        source="csv_import",
        imported_at=datetime.now(UTC),
    )


def _candidate() -> MoleculeCandidate:
    return _candidate_with(
        name="Rasagiline",
        candidate_id="CHEMBL887",
        smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.72,
        developability_score=0.68,
    )


def _candidate_with(
    *,
    name: str,
    candidate_id: str,
    smiles: str,
    score: float,
    developability_score: float = 0.7,
    warnings: list[str] | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": candidate_id},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": smiles},
        score=score,
        developability_assessment=DevelopabilityAssessment(
            molecule_name=name,
            origin="existing",
            structure_available=True,
            canonical_smiles=smiles,
            descriptors={"molecular_weight": 171.24},
            developability_score=developability_score,
            triage_recommendation="favorable_hypothesis",
            metadata={"risk_level": "high" if developability_score < 0.3 else "low"},
        ),
        warnings=warnings or [],
    )


def _generated_candidate() -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Generated-MAOB-001",
        molecule_type="small_molecule",
        origin="generated",
        identifiers={"generated": "gen-1"},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": "CCOc1ccccc1N"},
        score=0.5,
        generation_metadata={"generation_score": 0.58},
    )


def _review_item(**overrides: Any) -> ReviewItem:
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "disease_name": "Parkinson disease",
        "candidate_id": "CHEMBL887",
        "candidate_name": "Rasagiline",
        "candidate_origin": "existing",
        "target_symbols": ["MAOB"],
        "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
        "score": 0.72,
        "confidence": 0.7,
        "priority_bucket": "high_priority",
        "review_status": "accepted",
    }
    payload.update(overrides)
    return ReviewItem(**payload)


def _review_decision(review_item_id: str, decision: str) -> ReviewerDecision:
    return ReviewerDecision(
        review_item_id=review_item_id,
        reviewer=Reviewer(reviewer_id="expert-1", role="medicinal_chemist"),
        decision=decision,  # type: ignore[arg-type]
        rationale="Expert triage decision.",
        confidence=0.8,
    )


def test_builds_dataset_rows_with_descriptors_fingerprints_and_review_features():
    dataset = build_experimental_learning_dataset(
        [_result("result-1")],
        existing_candidates=[_candidate()],
        generated_molecules=[_generated_candidate()],
        review_items=[_review_item()],
        config={"fingerprint_n_bits": 128},
    )

    assert dataset.endpoint_name == "binding_affinity"
    assert dataset.included_result_ids == ["result-1"]
    assert dataset.excluded_result_ids == []
    assert len(dataset.rows) == 1
    row = dataset.rows[0]
    assert row["label"] == 1
    assert row["binary_label"] == 1
    assert row["continuous_label"] == 12.0
    assert row["existing_ranking_score"] == 0.72
    assert row["developability_score"] == 0.68
    assert row["review_priority_score"] == 1.0
    assert row["review_status"] == "accepted"
    assert row["desc_molecular_weight"] > 0
    assert row["morgan_fp_n_bits"] == 128
    assert isinstance(row["morgan_fp_on_bits"], list)
    assert dataset.feature_schema["fingerprint"]["morgan_fp_n_bits"] == 128


def test_excludes_failed_qc_inconclusive_and_context_mismatches_by_default():
    results = [
        _result("included"),
        _result("failed", outcome_label="failed_qc", qc_status="failed"),
        _result("inconclusive", outcome_label="inconclusive", activity_direction="ambiguous"),
        _result("other-endpoint", endpoint=_endpoint("cellular_activity")),
        _result("other-target", target_symbol="LRRK2"),
    ]

    dataset = build_experimental_learning_dataset(
        results,
        existing_candidates=[_candidate()],
        endpoint_name="binding_affinity",
        target_symbol="MAOB",
    )

    assert dataset.included_result_ids == ["included"]
    assert set(dataset.excluded_result_ids) == {
        "failed",
        "inconclusive",
        "other-endpoint",
        "other-target",
    }
    assert dataset.exclusion_reasons["failed"] == "failed_qc_excluded"
    assert dataset.exclusion_reasons["inconclusive"] == "inconclusive_excluded"
    assert dataset.exclusion_reasons["other-endpoint"] == "endpoint_mismatch"
    assert dataset.exclusion_reasons["other-target"] == "target_mismatch"


def test_maps_safety_and_continuous_labels_without_inventing_positives():
    safety_endpoint = _endpoint(
        "cytotoxicity",
        category="safety",
        directionality="higher_is_better",
    )
    results = [
        _result(
            "toxic",
            endpoint=safety_endpoint,
            outcome_label="negative",
            activity_direction="toxic",
            measured_value_numeric=0.72,
            normalized_value=0.72,
        ),
        _result(
            "not-tested",
            endpoint=safety_endpoint,
            outcome_label="not_tested",
            activity_direction="not_applicable",
            measured_value_numeric=None,
            normalized_value=None,
        ),
    ]

    dataset = build_experimental_learning_dataset(
        results,
        existing_candidates=[_candidate()],
        endpoint_name="cytotoxicity",
        config={"allow_context_pooling": False},
    )

    assert dataset.included_result_ids == ["toxic"]
    assert dataset.excluded_result_ids == ["not-tested"]
    row = dataset.rows[0]
    assert row["label_type"] == "safety_binary"
    assert row["safety_label"] == 1
    assert row["continuous_label"] == 0.72
    assert dataset.label_schema["safety_label"]["toxic"] == 1


def test_inconclusive_can_be_weakly_labeled_when_configured():
    dataset = build_experimental_learning_dataset(
        [_result("weak", outcome_label="inconclusive", activity_direction="ambiguous")],
        existing_candidates=[_candidate()],
        config={"inconclusive_label_policy": "weak"},
    )

    assert dataset.included_result_ids == ["weak"]
    assert dataset.rows[0]["label"] == 0.5
    assert dataset.rows[0]["label_type"] == "weak_inconclusive"


def test_uncertainty_strategy_prioritizes_uncertain_scores():
    uncertain = _candidate_with(
        name="Uncertain",
        candidate_id="CHEMBL-U",
        smiles="CCO",
        score=0.51,
    )
    confident = _candidate_with(
        name="Confident",
        candidate_id="CHEMBL-C",
        smiles="c1ccccc1",
        score=0.91,
    )

    batch = suggest_next_experiments(
        [confident, uncertain],
        [],
        [],
        [],
        {"strategy": "uncertainty", "top_k": 2},
    )

    assert batch.strategy == "uncertainty"
    assert batch.suggestions[0].candidate_name == "Uncertain"
    assert batch.suggestions[0].uncertainty_score is not None
    assert "expert triage" in batch.suggestions[0].rationale.lower()


def test_diversity_strategy_avoids_near_duplicates():
    alpha = _candidate_with(name="Alpha", candidate_id="A", smiles="CCO", score=0.8)
    duplicate = _candidate_with(name="Alpha duplicate", candidate_id="A2", smiles="CCO", score=0.78)
    diverse = _candidate_with(name="Diverse", candidate_id="D", smiles="c1ccccc1", score=0.7)

    batch = suggest_next_experiments(
        [alpha, duplicate, diverse],
        [],
        [],
        [],
        {"strategy": "diversity", "top_k": 2, "diversity_similarity_threshold": 0.85},
    )

    names = {suggestion.candidate_name for suggestion in batch.suggestions}
    assert len(batch.suggestions) == 2
    assert "Diverse" in names
    assert not {"Alpha", "Alpha duplicate"}.issubset(names)


def test_evidence_gap_prioritizes_candidates_without_results():
    tested = _candidate()
    gap = _candidate_with(name="Gap candidate", candidate_id="GAP", smiles="CCN", score=0.76)

    batch = suggest_next_experiments(
        [tested, gap],
        [],
        [_result("tested-result")],
        [],
        {"strategy": "evidence_gap", "top_k": 2},
    )

    assert batch.suggestions[0].candidate_name == "Gap candidate"
    assert batch.suggestions[0].metadata["experimental_result_count"] == 0


def test_safety_risk_penalizes_candidate():
    risky = _candidate_with(
        name="Risky",
        candidate_id="RISK",
        smiles="O=[N+]([O-])c1ccccc1",
        score=0.92,
        developability_score=0.15,
        warnings=["serious safety warning"],
    )
    safer = _candidate_with(name="Safer", candidate_id="SAFE", smiles="CCN", score=0.72)

    batch = suggest_next_experiments(
        [risky, safer],
        [],
        [],
        [],
        {"strategy": "balanced", "top_k": 2},
    )

    assert batch.suggestions[0].candidate_name == "Safer"
    risky_suggestion = next(item for item in batch.suggestions if item.candidate_name == "Risky")
    assert risky_suggestion.risk_penalty is not None
    assert risky_suggestion.risk_penalty > 0
    assert risky_suggestion.warnings


def test_generated_no_direct_evidence_is_handled_as_hypothesis():
    generated = _generated_candidate()

    batch = suggest_next_experiments(
        [],
        [generated],
        [],
        [],
        {"strategy": "evidence_gap", "top_k": 1},
    )

    suggestion = batch.suggestions[0]
    assert suggestion.candidate_name == "Generated-MAOB-001"
    assert suggestion.candidate_origin == "generated"
    assert suggestion.metadata["has_direct_experimental_evidence"] is False
    assert any("generated" in warning.lower() for warning in suggestion.warnings)


def test_active_learning_suggestion_tracks_model_uncertainty_influence():
    generated = _generated_candidate().model_copy(
        update={
            "generation_metadata": {
                "generation_score": 0.58,
                "model_predictions": [
                    {
                        "prediction_id": "pred-1",
                        "model_id": "model-1",
                        "model_version": "1",
                        "endpoint_id": "endpoint-maob",
                        "predicted_probability": 0.68,
                        "prediction_label": "positive",
                        "uncertainty": 0.76,
                        "confidence": 0.74,
                        "applicability_domain": "near_domain",
                        "calibration_status": "calibrated",
                        "warnings": [],
                        "not_evidence": True,
                        "not_assay_result": True,
                    }
                ],
            }
        }
    )

    batch = suggest_next_experiments(
        [],
        [generated],
        [],
        [],
        {"strategy": "uncertainty", "top_k": 1},
    )

    suggestion = batch.suggestions[0]
    influence = suggestion.metadata["model_influence"]
    assert influence["used_prediction_count"] == 1
    assert influence["not_evidence"] is True
    assert "model uncertainty" in suggestion.rationale.lower()
    assert "evidenceitem" not in str(suggestion.model_dump()).lower()


def test_expert_priority_uses_review_decisions_and_outputs_guardrail_text():
    item = _review_item(review_item_id="review-gap", candidate_id="GAP", candidate_name="Gap")
    candidate = _candidate_with(name="Gap", candidate_id="GAP", smiles="CCN", score=0.6)
    decision = _review_decision("review-gap", "needs_more_data")

    batch = suggest_next_experiments(
        [candidate],
        [],
        [],
        [item, decision],
        {"strategy": "expert_priority", "top_k": 1},
    )

    suggestion = batch.suggestions[0]
    assert suggestion.candidate_name == "Gap"
    assert suggestion.metadata["review_decision_count"] == 1
    assert "not an instruction to run experiments" in suggestion.rationale
    assert suggestion.warnings
