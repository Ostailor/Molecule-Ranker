"""Active-learning dataset construction from imported assay results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.guardrails import sanitize_experimental_output_text
from molecule_ranker.experiments.schemas import (
    ActiveLearningBatch,
    ActiveLearningSuggestion,
    AssayResult,
    CandidateOrigin,
    ExperimentalLearningDataset,
)
from molecule_ranker.generation.chemistry import (
    descriptors_from_mol,
    mol_from_smiles,
    morgan_fingerprint,
    tanimoto_similarity,
)
from molecule_ranker.schemas import DevelopabilityAssessment, MoleculeCandidate

PRIORITY_SCORES = {
    "high_priority": 1.0,
    "medium_priority": 0.66,
    "needs_review": 0.5,
    "low_priority": 0.33,
    "reject_suggested": 0.0,
}
REVIEW_STATUS_SCORES = {
    "accepted": 1.0,
    "in_review": 0.7,
    "pending": 0.5,
    "needs_more_data": 0.45,
    "escalated": 0.4,
    "deprioritized": 0.2,
    "rejected": 0.0,
}


@dataclass(frozen=True)
class _CandidateFeatures:
    candidate_id: str | None
    candidate_name: str
    candidate_origin: str
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    known_targets: tuple[str, ...] = ()
    existing_ranking_score: float | None = None
    developability_score: float | None = None
    generation_score: float | None = None
    literature_quality: float | None = None
    evidence_summary: Mapping[str, Any] | None = None
    model_predictions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class _Label:
    label: float | int
    label_type: str
    binary_label: int | float | None = None
    continuous_label: float | None = None
    safety_label: int | float | None = None


@dataclass(frozen=True)
class _SuggestionCandidate:
    features: _CandidateFeatures
    review: Mapping[str, Any] | None
    result_ids: tuple[str, ...]
    result_count: int
    positive_result_count: int
    negative_result_count: int
    safety_result_count: int
    has_direct_experimental_evidence: bool
    evidence_score: float
    uncertainty_score: float
    diversity_score: float
    developability_score: float
    review_priority_score: float
    experimental_gap_score: float
    risk_penalty: float
    model_influence: Mapping[str, Any]
    warnings: tuple[str, ...]


def build_experimental_learning_dataset(
    assay_results: Sequence[AssayResult],
    *,
    existing_candidates: Sequence[MoleculeCandidate] = (),
    generated_molecules: Sequence[Any] = (),
    developability_assessments: Sequence[DevelopabilityAssessment] = (),
    evidence_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    review_items: Sequence[Any] = (),
    review_decisions: Sequence[Any] = (),
    disease_name: str | None = None,
    target_symbol: str | None = None,
    endpoint_name: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> ExperimentalLearningDataset:
    """Build an endpoint-specific learning dataset from imported assay results.

    Candidate and review inputs are used only as feature sources. Unlabeled candidates are
    never emitted as positive or negative training rows.
    """

    config = dict(config or {})
    allow_endpoint_pooling = bool(config.get("allow_endpoint_pooling", False))
    allow_context_pooling = bool(config.get("allow_context_pooling", False))
    inconclusive_policy = str(config.get("inconclusive_label_policy", "exclude")).lower()
    selected_endpoint = endpoint_name or _first_endpoint_name(assay_results) or "unknown"
    selected_disease = disease_name or (
        None if allow_context_pooling else _first_context_value(assay_results, "disease_name")
    )
    selected_target = target_symbol or (
        None if allow_context_pooling else _first_context_value(assay_results, "target_symbol")
    )

    candidates = _candidate_feature_index(
        existing_candidates=existing_candidates,
        generated_molecules=generated_molecules,
        developability_assessments=developability_assessments,
        evidence_summaries=evidence_summaries or {},
    )
    reviews = _review_feature_index(review_items, review_decisions)
    fingerprint_n_bits = int(config.get("fingerprint_n_bits", 2048))

    rows: list[dict[str, Any]] = []
    included_result_ids: list[str] = []
    excluded_result_ids: list[str] = []
    exclusion_reasons: dict[str, str] = {}

    for result in assay_results:
        result_id = result.result_id
        exclusion = _exclusion_reason(
            result,
            selected_endpoint=selected_endpoint,
            selected_disease=selected_disease,
            selected_target=selected_target,
            allow_endpoint_pooling=allow_endpoint_pooling,
            allow_context_pooling=allow_context_pooling,
            include_failed_qc=bool(config.get("include_failed_qc", False)),
        )
        if exclusion is not None:
            excluded_result_ids.append(result_id)
            exclusion_reasons[result_id] = exclusion
            continue

        label = _label_for_result(result, inconclusive_policy=inconclusive_policy)
        if label is None:
            excluded_result_ids.append(result_id)
            exclusion_reasons[result_id] = _label_exclusion_reason(result)
            continue

        candidate = _find_candidate_features(result, candidates)
        review = _find_review_features(result, reviews)
        row = _dataset_row(
            result,
            label=label,
            candidate=candidate,
            review=review,
            fingerprint_n_bits=fingerprint_n_bits,
        )
        rows.append(row)
        included_result_ids.append(result_id)

    dataset_id = _dataset_id(
        endpoint_name=selected_endpoint,
        disease_name=selected_disease,
        target_symbol=selected_target,
        result_ids=included_result_ids,
    )
    return ExperimentalLearningDataset(
        dataset_id=dataset_id,
        created_at=datetime.now(UTC),
        disease_name=selected_disease,
        target_symbol=selected_target,
        endpoint_name=selected_endpoint,
        rows=rows,
        feature_schema=_feature_schema(fingerprint_n_bits),
        label_schema=_label_schema(inconclusive_policy),
        included_result_ids=included_result_ids,
        excluded_result_ids=excluded_result_ids,
        exclusion_reasons=exclusion_reasons,
        metadata={
            "source_result_count": len(assay_results),
            "candidate_feature_count": len(candidates),
            "review_feature_count": len(reviews),
            "unlabeled_candidates_not_included": True,
            "allow_endpoint_pooling": allow_endpoint_pooling,
            "allow_context_pooling": allow_context_pooling,
            "inconclusive_label_policy": inconclusive_policy,
        },
    )


def suggest_next_experiments(
    candidates: Sequence[MoleculeCandidate],
    generated: Sequence[Any],
    results: Sequence[AssayResult],
    reviews: Sequence[Any],
    config: Mapping[str, Any] | None = None,
) -> ActiveLearningBatch:
    """Suggest next candidates for expert triage using high-level assay classes only."""

    config = dict(config or {})
    strategy = str(config.get("strategy", "balanced")).lower()
    if strategy not in {
        "uncertainty",
        "diversity",
        "expected_improvement",
        "evidence_gap",
        "balanced",
        "expert_priority",
    }:
        strategy = "balanced"
    top_k = max(1, int(config.get("top_k", 10)))
    review_items, review_decisions = _split_review_inputs(reviews)
    candidate_features = _suggestion_feature_list(
        existing_candidates=candidates,
        generated_molecules=generated,
        evidence_summaries=config.get("evidence_summaries"),
    )
    review_index = _review_feature_index(review_items, review_decisions)
    result_index = _result_index(results)
    suggestion_candidates = [
        _suggestion_candidate(feature, review_index, result_index)
        for feature in candidate_features
    ]

    if strategy == "diversity":
        selected_candidates = _select_diverse_candidates(
            suggestion_candidates,
            top_k=top_k,
            similarity_threshold=float(config.get("diversity_similarity_threshold", 0.85)),
        )
    else:
        selected_candidates = sorted(
            suggestion_candidates,
            key=lambda item: _acquisition_score(item, strategy),
            reverse=True,
        )[:top_k]

    suggestions = [
        _active_learning_suggestion(item, strategy)
        for item in selected_candidates
        if _acquisition_score(item, strategy) > 0 or strategy in {"diversity", "expert_priority"}
    ]
    excluded = [
        {
            "candidate_id": item.features.candidate_id,
            "candidate_name": item.features.candidate_name,
            "reason": "not_selected_by_active_learning_strategy",
            "risk_penalty": round(item.risk_penalty, 3),
        }
        for item in suggestion_candidates
        if item.features.candidate_name
        not in {suggestion.candidate_name for suggestion in suggestions}
    ]
    endpoint_name = str(
        config.get("endpoint_name") or _first_endpoint_name(results) or "unspecified_endpoint"
    )
    disease_name = _config_or_first(config, results, "disease_name")
    target_symbol = _config_or_first(config, results, "target_symbol")
    batch_id = _batch_id(
        strategy=strategy,
        endpoint_name=endpoint_name,
        candidate_names=[suggestion.candidate_name for suggestion in suggestions],
    )
    return ActiveLearningBatch(
        batch_id=batch_id,
        created_at=datetime.now(UTC),
        disease_name=disease_name,
        target_symbol=target_symbol,
        endpoint_name=endpoint_name,
        strategy=strategy,
        suggestions=suggestions,
        excluded_candidates=excluded,
        metadata={
            "suggestion_scope": "expert_triage",
            "safety_note": (
                "Suggestions are prioritization hypotheses only and do not provide "
                "operational wet-lab details, animal or human testing steps, "
                "or clinical-use guidance."
            ),
            "source_candidate_count": len(candidate_features),
            "source_result_count": len(results),
        },
    )


def _dataset_row(
    result: AssayResult,
    *,
    label: _Label,
    candidate: _CandidateFeatures | None,
    review: Mapping[str, Any] | None,
    fingerprint_n_bits: int,
) -> dict[str, Any]:
    endpoint = result.assay_context.endpoint
    canonical_smiles = (
        result.canonical_smiles
        or (candidate.canonical_smiles if candidate is not None else None)
    )
    descriptors, fingerprint = _structure_features(canonical_smiles, fingerprint_n_bits)
    candidate_id = (
        result.metadata.get("linked_candidate_id")
        or result.candidate_id
        or (candidate.candidate_id if candidate is not None else None)
    )
    known_targets = list(candidate.known_targets) if candidate is not None else []
    known_target_set = {target.upper() for target in known_targets}
    target_symbol = result.target_symbol or result.assay_context.target_symbol

    row: dict[str, Any] = {
        "result_id": result.result_id,
        "candidate_id": candidate_id,
        "candidate_name": result.candidate_name,
        "candidate_origin": result.candidate_origin,
        "canonical_smiles": canonical_smiles,
        "inchi_key": result.inchi_key or (candidate.inchi_key if candidate is not None else None),
        "disease_name": result.disease_name or result.assay_context.disease_name,
        "target_symbol": target_symbol,
        "assay_name": result.assay_context.assay_name,
        "assay_type": result.assay_context.assay_type,
        "endpoint_name": endpoint.name,
        "endpoint_category": endpoint.endpoint_category,
        "endpoint_directionality": endpoint.directionality,
        "measured_value_numeric": result.measured_value_numeric,
        "normalized_value": result.normalized_value,
        "normalized_unit": result.normalized_unit,
        "outcome_label": result.outcome_label,
        "activity_direction": result.activity_direction,
        "qc_status": result.qc_status,
        "result_confidence": result.confidence,
        "replicate_count": result.replicate_count,
        "uncertainty": result.uncertainty,
        "label": label.label,
        "label_type": label.label_type,
        "binary_label": label.binary_label,
        "continuous_label": label.continuous_label,
        "safety_label": label.safety_label,
        "known_target_match": int(
            bool(target_symbol) and target_symbol.upper() in known_target_set
        ),
        "existing_ranking_score": candidate.existing_ranking_score if candidate else None,
        "developability_score": candidate.developability_score if candidate else None,
        "generation_score": candidate.generation_score if candidate else None,
        "literature_quality": candidate.literature_quality if candidate else None,
        "morgan_fp_on_bits": fingerprint,
        "morgan_fp_n_bits": fingerprint_n_bits,
    }
    for name, value in descriptors.items():
        row[f"desc_{name}"] = value
    if review:
        row.update(review)
    else:
        row.update(
            {
                "review_priority_bucket": None,
                "review_priority_score": None,
                "review_status": None,
                "review_status_score": None,
                "review_decision_count": 0,
                "review_positive_decision_count": 0,
                "review_negative_decision_count": 0,
            }
        )
    return row


def _suggestion_feature_list(
    *,
    existing_candidates: Sequence[MoleculeCandidate],
    generated_molecules: Sequence[Any],
    evidence_summaries: object,
) -> list[_CandidateFeatures]:
    summaries = evidence_summaries if isinstance(evidence_summaries, Mapping) else {}
    features = [
        _features_from_molecule_candidate(
            candidate,
            assessments={},
            evidence_summaries=summaries,
        )
        for candidate in existing_candidates
    ]
    features.extend(
        _features_from_generated(candidate, {}, summaries)
        for candidate in generated_molecules
    )
    return features


def _suggestion_candidate(
    feature: _CandidateFeatures,
    review_index: Mapping[str, Mapping[str, Any]],
    result_index: Mapping[str, list[AssayResult]],
) -> _SuggestionCandidate:
    review = _review_for_feature(feature, review_index)
    result_list = _results_for_feature(feature, result_index)
    evidence_score = _candidate_evidence_score(feature)
    uncertainty_score = _uncertainty_score(evidence_score, review)
    model_influence = _model_prediction_influence(feature)
    uncertainty_score = _clamp(
        uncertainty_score + 0.15 * float(model_influence["uncertainty_sampling"])
    )
    developability_score = feature.developability_score
    if developability_score is None:
        developability_score = 0.45 if feature.candidate_origin == "generated" else 0.5
    review_priority_score = _review_priority_score(review)
    positive_count = sum(1 for result in result_list if result.outcome_label == "positive")
    negative_count = sum(1 for result in result_list if result.outcome_label == "negative")
    safety_count = sum(1 for result in result_list if _result_is_safety_concern(result))
    risk_penalty, warnings = _risk_penalty(feature, result_list)
    risk_penalty = _clamp(risk_penalty + float(model_influence["domain_penalty"]))
    warnings.extend(str(warning) for warning in model_influence["warnings"])
    result_count = len(result_list)
    has_direct = result_count > 0
    experimental_gap = 1.0 if result_count == 0 else max(0.0, 1.0 - min(result_count / 3.0, 1.0))
    if feature.candidate_origin == "generated" and not has_direct:
        warnings.append(
            "Generated molecule has no exact linked imported assay result; "
            "treat as an unvalidated hypothesis."
        )
    return _SuggestionCandidate(
        features=feature,
        review=review,
        result_ids=tuple(result.result_id for result in result_list),
        result_count=result_count,
        positive_result_count=positive_count,
        negative_result_count=negative_count,
        safety_result_count=safety_count,
        has_direct_experimental_evidence=has_direct,
        evidence_score=evidence_score,
        uncertainty_score=uncertainty_score,
        diversity_score=1.0,
        developability_score=_clamp(developability_score),
        review_priority_score=review_priority_score,
        experimental_gap_score=experimental_gap,
        risk_penalty=risk_penalty,
        model_influence=model_influence,
        warnings=tuple(warnings),
    )


def _active_learning_suggestion(
    item: _SuggestionCandidate,
    strategy: str,
) -> ActiveLearningSuggestion:
    acquisition_score = _acquisition_score(item, strategy)
    rationale = _suggestion_rationale(item, strategy)
    return ActiveLearningSuggestion(
        suggestion_id=_suggestion_id(item, strategy),
        candidate_id=item.features.candidate_id,
        candidate_name=item.features.candidate_name,
        candidate_origin=_candidate_origin(item.features.candidate_origin),
        target_symbol=item.features.known_targets[0] if item.features.known_targets else None,
        canonical_smiles=item.features.canonical_smiles,
        acquisition_score=round(acquisition_score, 3),
        acquisition_strategy=strategy,
        rationale=rationale,
        uncertainty_score=round(item.uncertainty_score, 3),
        diversity_score=round(item.diversity_score, 3),
        expected_value_score=round(_expected_value_score(item), 3),
        risk_penalty=round(item.risk_penalty, 3),
        constraints_satisfied=item.risk_penalty < 0.8,
        warnings=list(item.warnings) or [
            "Suggestion is for expert triage only, not an instruction to run experiments."
        ],
        metadata={
            "suggested_assay_class": _suggested_assay_class(item),
            "experimental_result_ids": list(item.result_ids),
            "experimental_result_count": item.result_count,
            "positive_result_count": item.positive_result_count,
            "negative_result_count": item.negative_result_count,
            "safety_result_count": item.safety_result_count,
            "has_direct_experimental_evidence": item.has_direct_experimental_evidence,
            "review_decision_count": int(
                item.review.get("review_decision_count", 0) if item.review else 0
            ),
            "review_priority_score": item.review_priority_score,
            "experimental_gap_score": item.experimental_gap_score,
            "model_influence": dict(item.model_influence),
            "model_predictions_are_not_evidence": True,
        },
    )


def _acquisition_score(item: _SuggestionCandidate, strategy: str) -> float:
    score = {
        "uncertainty": _clamp(
            0.85 * item.uncertainty_score + 0.15 * item.evidence_score - item.risk_penalty
        ),
        "diversity": _clamp(
            0.65 * item.diversity_score
            + 0.25 * item.evidence_score
            + 0.10 * item.developability_score
            - item.risk_penalty
        ),
        "expected_improvement": _expected_value_score(item),
        "evidence_gap": _clamp(
            0.55 * item.evidence_score
            + 0.35 * item.experimental_gap_score
            + 0.10 * item.developability_score
            - item.risk_penalty
        ),
        "expert_priority": _clamp(
            0.75 * item.review_priority_score
            + 0.15 * item.experimental_gap_score
            + 0.10 * item.evidence_score
            - item.risk_penalty
        ),
        "balanced": _clamp(
            0.25 * item.evidence_score
            + 0.18 * item.uncertainty_score
            + 0.15 * item.diversity_score
            + 0.16 * item.developability_score
            + 0.13 * item.review_priority_score
            + 0.13 * item.experimental_gap_score
            - item.risk_penalty
        ),
    }.get(strategy, 0.0)
    return _clamp(score)


def _expected_value_score(item: _SuggestionCandidate) -> float:
    model_influence = item.model_influence
    return _clamp(
        0.60 * item.evidence_score
        + 0.25 * item.developability_score
        + 0.15 * item.review_priority_score
        + 0.12 * float(model_influence["expected_improvement"])
        - item.risk_penalty
    )


def _select_diverse_candidates(
    candidates: Sequence[_SuggestionCandidate],
    *,
    top_k: int,
    similarity_threshold: float,
) -> list[_SuggestionCandidate]:
    selected: list[_SuggestionCandidate] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            _candidate_evidence_score(item.features)
            + 0.2 * item.developability_score
            - item.risk_penalty
        ),
        reverse=True,
    )
    for item in ordered:
        diversity = _diversity_against_selected(item, selected)
        if diversity < 1.0 - similarity_threshold:
            continue
        selected.append(_with_diversity_score(item, diversity))
        if len(selected) >= top_k:
            break
    return selected


def _diversity_against_selected(
    item: _SuggestionCandidate,
    selected: Sequence[_SuggestionCandidate],
) -> float:
    smiles = item.features.canonical_smiles
    mol = mol_from_smiles(smiles) if smiles else None
    if mol is None or not selected:
        return 1.0
    similarities: list[float] = []
    for other in selected:
        other_smiles = other.features.canonical_smiles
        other_mol = mol_from_smiles(other_smiles) if other_smiles else None
        if other_mol is not None:
            similarities.append(tanimoto_similarity(mol, other_mol))
    return _clamp(1.0 - max(similarities, default=0.0))


def _with_diversity_score(
    item: _SuggestionCandidate,
    diversity_score: float,
) -> _SuggestionCandidate:
    return _SuggestionCandidate(
        features=item.features,
        review=item.review,
        result_ids=item.result_ids,
        result_count=item.result_count,
        positive_result_count=item.positive_result_count,
        negative_result_count=item.negative_result_count,
        safety_result_count=item.safety_result_count,
        has_direct_experimental_evidence=item.has_direct_experimental_evidence,
        evidence_score=item.evidence_score,
        uncertainty_score=item.uncertainty_score,
        diversity_score=_clamp(diversity_score),
        developability_score=item.developability_score,
        review_priority_score=item.review_priority_score,
        experimental_gap_score=item.experimental_gap_score,
        risk_penalty=item.risk_penalty,
        model_influence=item.model_influence,
        warnings=item.warnings,
    )


def _split_review_inputs(reviews: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    review_items: list[Any] = []
    review_decisions: list[Any] = []
    for item in reviews:
        if hasattr(item, "decision") and hasattr(item, "review_item_id"):
            review_decisions.append(item)
        elif hasattr(item, "candidate_name") and hasattr(item, "priority_bucket"):
            review_items.append(item)
    return review_items, review_decisions


def _result_index(results: Sequence[AssayResult]) -> dict[str, list[AssayResult]]:
    index: dict[str, list[AssayResult]] = {}
    for result in results:
        candidate_id = str(
            result.metadata.get("linked_candidate_id") or result.candidate_id or ""
        )
        for key in _identity_keys(
            candidate_id=candidate_id,
            candidate_name=result.candidate_name,
            canonical_smiles=result.canonical_smiles,
            inchi_key=result.inchi_key,
            review_item_id=result.review_item_id,
        ):
            index.setdefault(key, []).append(result)
    return index


def _results_for_feature(
    feature: _CandidateFeatures,
    result_index: Mapping[str, list[AssayResult]],
) -> list[AssayResult]:
    seen: set[str] = set()
    matches: list[AssayResult] = []
    for key in _identity_keys(
        candidate_id=feature.candidate_id,
        candidate_name=feature.candidate_name,
        canonical_smiles=feature.canonical_smiles,
        inchi_key=feature.inchi_key,
        review_item_id=None,
    ):
        for result in result_index.get(key, []):
            if result.result_id not in seen:
                seen.add(result.result_id)
                matches.append(result)
    return matches


def _review_for_feature(
    feature: _CandidateFeatures,
    review_index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for key in _identity_keys(
        candidate_id=feature.candidate_id,
        candidate_name=feature.candidate_name,
        canonical_smiles=feature.canonical_smiles,
        inchi_key=feature.inchi_key,
        review_item_id=None,
    ):
        if key in review_index:
            return review_index[key]
    return None


def _candidate_evidence_score(feature: _CandidateFeatures) -> float:
    score = feature.existing_ranking_score
    if score is None:
        score = feature.generation_score
    return _clamp(score if score is not None else 0.35)


def _uncertainty_score(evidence_score: float, review: Mapping[str, Any] | None) -> float:
    score_uncertainty = 1.0 - min(abs(evidence_score - 0.5) * 2.0, 1.0)
    if review is None:
        return _clamp(score_uncertainty + 0.1)
    status = str(review.get("review_status") or "")
    if status in {"needs_more_data", "escalated"}:
        score_uncertainty += 0.2
    return _clamp(score_uncertainty)


def _review_priority_score(review: Mapping[str, Any] | None) -> float:
    if review is None:
        return 0.5
    decision_values = review.get("review_decision_values")
    if isinstance(decision_values, list):
        if any(value in {"accept_for_followup", "needs_more_data"} for value in decision_values):
            return 1.0
        if any(value in {"reject", "deprioritize"} for value in decision_values):
            return 0.0
    value = review.get("review_priority_score")
    return _clamp(float(value)) if isinstance(value, int | float) else 0.5


def _risk_penalty(
    feature: _CandidateFeatures,
    results: Sequence[AssayResult],
) -> tuple[float, list[str]]:
    penalty = 0.0
    warnings: list[str] = []
    if feature.developability_score is not None and feature.developability_score < 0.3:
        penalty += 0.35
        warnings.append("Low developability score penalizes this suggestion.")
    for text in _feature_warning_text(feature):
        if any(term in text for term in ("safety", "toxic", "hERG".lower(), "ames")):
            penalty += 0.2
            warnings.append("Safety or toxicity warning penalizes this suggestion.")
            break
    if any(_result_is_safety_concern(result) for result in results):
        penalty += 0.35
        warnings.append("Imported safety assay concern penalizes this suggestion.")
    if any(result.outcome_label == "negative" for result in results):
        penalty += 0.12
        warnings.append("Imported negative assay result lowers priority.")
    return _clamp(penalty), sorted(set(warnings))


def _model_prediction_influence(feature: _CandidateFeatures) -> dict[str, Any]:
    predictions = [dict(prediction) for prediction in feature.model_predictions]
    if not predictions:
        return {
            "prediction_count": 0,
            "used_prediction_count": 0,
            "expected_improvement": 0.0,
            "uncertainty_sampling": 0.0,
            "domain_penalty": 0.0,
            "warnings": [],
            "not_evidence": True,
            "not_assay_result": True,
        }
    used: list[dict[str, Any]] = []
    warnings: list[str] = []
    out_of_domain_count = 0
    for prediction in predictions:
        calibration_status = str(prediction.get("calibration_status") or "")
        applicability_domain = str(prediction.get("applicability_domain") or "unknown")
        confidence = prediction.get("confidence")
        if applicability_domain == "out_of_domain":
            out_of_domain_count += 1
            warnings.append("Model prediction out of domain; penalized suggestion.")
        if calibration_status == "insufficient_data":
            warnings.append("Model prediction had insufficient calibration data; ignored.")
            continue
        if calibration_status not in {"calibrated", "not_applicable"}:
            warnings.append("Uncalibrated model prediction ignored.")
            continue
        if not isinstance(confidence, (int, float)) or float(confidence) < 0.5:
            warnings.append("Low-confidence model prediction ignored.")
            continue
        if applicability_domain not in {"in_domain", "near_domain"}:
            continue
        used.append(prediction)
    probabilities = [
        float(prediction["predicted_probability"])
        for prediction in used
        if isinstance(prediction.get("predicted_probability"), (int, float))
    ]
    uncertainties = [
        float(prediction["uncertainty"])
        for prediction in used
        if isinstance(prediction.get("uncertainty"), (int, float))
    ]
    best_probability = max(probabilities, default=0.5)
    mean_uncertainty = sum(uncertainties) / len(uncertainties) if uncertainties else 0.0
    return {
        "prediction_count": len(predictions),
        "used_prediction_count": len(used),
        "out_of_domain_count": out_of_domain_count,
        "expected_improvement": round(_clamp((best_probability - 0.5) * 2.0), 3),
        "uncertainty_sampling": round(_clamp(mean_uncertainty), 3),
        "domain_penalty": 0.12 if out_of_domain_count else 0.0,
        "warnings": sorted(set(warnings)),
        "not_evidence": True,
        "not_assay_result": True,
    }


def _feature_warning_text(feature: _CandidateFeatures) -> list[str]:
    summary = feature.evidence_summary or {}
    warning_values = summary.get("warnings", []) if isinstance(summary, Mapping) else []
    if not isinstance(warning_values, list):
        warning_values = []
    return [str(value).lower() for value in warning_values]


def _result_is_safety_concern(result: AssayResult) -> bool:
    return (
        result.assay_context.endpoint.endpoint_category == "safety"
        and result.activity_direction in {"toxic", "worsened"}
    ) or result.activity_direction == "toxic"


def _suggestion_rationale(item: _SuggestionCandidate, strategy: str) -> str:
    assay_class = _suggested_assay_class(item)
    model_sentence = ""
    if int(item.model_influence.get("used_prediction_count", 0)) > 0:
        model_sentence = (
            " Model uncertainty and calibrated surrogate prediction influence are "
            "reported as weak prioritization signals only."
        )
    elif int(item.model_influence.get("out_of_domain_count", 0)) > 0:
        model_sentence = (
            " Out-of-domain model predictions lower priority and are not experimental "
            "feedback."
        )
    return sanitize_experimental_output_text(
        f"{item.features.candidate_name} is suggested for expert triage using the "
        f"{strategy} active-learning strategy. Suggested assay class: {assay_class}. "
        f"Score reflects ranking evidence, uncertainty, diversity, developability, "
        f"review priority, experimental evidence gap, and risk penalties where available. "
        f"{model_sentence}"
        "This is not an instruction to run experiments and does not include operational "
        "wet-lab details, animal or human testing steps, or clinical-use guidance."
    )


def _suggested_assay_class(item: _SuggestionCandidate) -> str:
    if item.safety_result_count or item.risk_penalty >= 0.35:
        return "high_level_safety_or_developability_assay"
    if item.features.known_targets:
        return "high_level_target_or_activity_assay"
    return "high_level_exploratory_assay"


def _candidate_origin(origin: str) -> CandidateOrigin:
    if origin in {"existing", "generated", "unknown"}:
        return cast(CandidateOrigin, origin)
    return "unknown"


def _suggestion_id(item: _SuggestionCandidate, strategy: str) -> str:
    basis = "|".join([strategy, item.features.candidate_id or "", item.features.candidate_name])
    return f"active-learning-suggestion-{uuid5(NAMESPACE_URL, basis)}"


def _batch_id(*, strategy: str, endpoint_name: str, candidate_names: Sequence[str]) -> str:
    basis = "|".join([strategy, endpoint_name, ",".join(candidate_names)])
    return f"active-learning-batch-{uuid5(NAMESPACE_URL, basis)}"


def _config_or_first(
    config: Mapping[str, Any],
    results: Sequence[AssayResult],
    key: str,
) -> str | None:
    value = config.get(key)
    if value:
        return str(value)
    return _first_context_value(results, key)


def _exclusion_reason(
    result: AssayResult,
    *,
    selected_endpoint: str,
    selected_disease: str | None,
    selected_target: str | None,
    allow_endpoint_pooling: bool,
    allow_context_pooling: bool,
    include_failed_qc: bool,
) -> str | None:
    if not result.candidate_name.strip():
        return "missing_candidate_name"
    if result.qc_status == "failed" or result.outcome_label == "failed_qc":
        return None if include_failed_qc else "failed_qc_excluded"
    if result.outcome_label in {"invalid", "not_tested"}:
        return f"{result.outcome_label}_excluded"
    if not allow_endpoint_pooling and result.assay_context.endpoint.name != selected_endpoint:
        return "endpoint_mismatch"
    if not allow_context_pooling:
        result_disease = result.disease_name or result.assay_context.disease_name
        result_target = result.target_symbol or result.assay_context.target_symbol
        if (
            selected_disease
            and result_disease
            and result_disease.lower() != selected_disease.lower()
        ):
            return "disease_mismatch"
        if selected_target and result_target and result_target.upper() != selected_target.upper():
            return "target_mismatch"
    return None


def _label_for_result(result: AssayResult, *, inconclusive_policy: str) -> _Label | None:
    continuous = result.normalized_value
    if continuous is None:
        continuous = result.measured_value_numeric
    endpoint = result.assay_context.endpoint
    if result.outcome_label == "inconclusive":
        if inconclusive_policy == "weak":
            return _Label(
                label=0.5,
                label_type="weak_inconclusive",
                binary_label=0.5,
                continuous_label=continuous,
            )
        return None
    if result.outcome_label in {"failed_qc", "invalid", "not_tested"}:
        return None
    is_safety = (
        endpoint.endpoint_category == "safety" or result.assay_context.assay_type == "safety"
    )
    if is_safety:
        safety = _safety_label(result)
        if safety is None:
            return None
        return _Label(
            label=safety,
            label_type="safety_binary",
            binary_label=safety,
            continuous_label=continuous,
            safety_label=safety,
        )
    binary = _binary_activity_label(result)
    if binary is None and continuous is None:
        return None
    if binary is None and continuous is not None:
        return _Label(label=continuous, label_type="continuous", continuous_label=continuous)
    if binary is None:
        return None
    return _Label(
        label=binary,
        label_type="binary",
        binary_label=binary,
        continuous_label=continuous,
    )


def _binary_activity_label(result: AssayResult) -> int | None:
    if result.outcome_label == "positive":
        return 1
    if result.outcome_label == "negative":
        return 0
    if result.activity_direction in {"active", "improved"}:
        return 1
    if result.activity_direction in {"inactive", "no_effect"}:
        return 0
    return None


def _safety_label(result: AssayResult) -> int | None:
    if result.activity_direction in {"toxic", "worsened"}:
        return 1
    if result.activity_direction in {"non_toxic", "no_effect", "improved"}:
        return 0
    if result.outcome_label == "positive":
        return 0
    if result.outcome_label == "negative":
        return 1
    return None


def _label_exclusion_reason(result: AssayResult) -> str:
    if result.outcome_label == "inconclusive":
        return "inconclusive_excluded"
    return "unlabeled_or_ambiguous"


def _candidate_feature_index(
    *,
    existing_candidates: Sequence[MoleculeCandidate],
    generated_molecules: Sequence[Any],
    developability_assessments: Sequence[DevelopabilityAssessment],
    evidence_summaries: Mapping[str, Mapping[str, Any]],
) -> dict[str, _CandidateFeatures]:
    assessments = _assessment_index(developability_assessments)
    index: dict[str, _CandidateFeatures] = {}
    for candidate in existing_candidates:
        feature = _features_from_molecule_candidate(
            candidate,
            assessments=assessments,
            evidence_summaries=evidence_summaries,
        )
        _add_feature(index, feature)
    for candidate in generated_molecules:
        feature = _features_from_generated(candidate, assessments, evidence_summaries)
        _add_feature(index, feature)
    return index


def _features_from_molecule_candidate(
    candidate: MoleculeCandidate,
    *,
    assessments: Mapping[str, DevelopabilityAssessment],
    evidence_summaries: Mapping[str, Mapping[str, Any]],
) -> _CandidateFeatures:
    candidate_id = (
        candidate.identifiers.get("chembl")
        or candidate.identifiers.get("pubchem_cid")
        or candidate.identifiers.get("generated")
        or candidate.name
    )
    canonical_smiles = _candidate_smiles(candidate)
    assessment = candidate.developability_assessment or _assessment_for(
        assessments,
        candidate_id=candidate_id,
        candidate_name=candidate.name,
        canonical_smiles=canonical_smiles,
    )
    literature_quality = (
        candidate.literature_evidence.quality_score
        if candidate.literature_evidence is not None
        else None
    )
    return _CandidateFeatures(
        candidate_id=candidate_id,
        candidate_name=candidate.name,
        candidate_origin=candidate.origin,
        canonical_smiles=canonical_smiles,
        inchi_key=_metadata_string(candidate.chemical_metadata, "inchi_key"),
        known_targets=tuple(candidate.known_targets),
        existing_ranking_score=candidate.score if candidate.origin == "existing" else None,
        developability_score=assessment.developability_score if assessment is not None else None,
        generation_score=_generation_score(candidate),
        literature_quality=literature_quality,
        evidence_summary=_summary_for(evidence_summaries, candidate_id, candidate.name),
        model_predictions=_model_predictions_from_mapping(
            {
                **candidate.chemical_metadata,
                **candidate.generation_metadata,
            }
        ),
    )


def _features_from_generated(
    candidate: Any,
    assessments: Mapping[str, DevelopabilityAssessment],
    evidence_summaries: Mapping[str, Mapping[str, Any]],
) -> _CandidateFeatures:
    candidate_id = str(
        getattr(candidate, "generated_id", None)
        or getattr(candidate, "candidate_id", None)
        or getattr(candidate, "name", None)
        or ""
    ) or None
    candidate_name = str(getattr(candidate, "name", None) or candidate_id or "generated")
    canonical_smiles = _object_string(candidate, "canonical_smiles")
    assessment = getattr(candidate, "developability_assessment", None) or _assessment_for(
        assessments,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        canonical_smiles=canonical_smiles,
    )
    target = _object_string(candidate, "target_symbol")
    conditioned_targets = getattr(candidate, "conditioned_targets", [])
    known_targets = tuple(conditioned_targets) if isinstance(conditioned_targets, list) else ()
    if target:
        known_targets = (*known_targets, target)
    return _CandidateFeatures(
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin="generated",
        canonical_smiles=canonical_smiles,
        inchi_key=_object_string(candidate, "inchi_key"),
        known_targets=known_targets,
        developability_score=(
            assessment.developability_score
            if isinstance(assessment, DevelopabilityAssessment)
            else None
        ),
        generation_score=_object_float(candidate, "generation_score")
        or _metadata_float(getattr(candidate, "generation_metadata", {}), "generation_score"),
        evidence_summary=_summary_for(evidence_summaries, candidate_id, candidate_name),
        model_predictions=_model_predictions_from_object(candidate),
    )


def _review_feature_index(
    review_items: Sequence[Any],
    review_decisions: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    decisions_by_item: dict[str, list[Any]] = {}
    for decision in review_decisions:
        review_item_id = _object_string(decision, "review_item_id")
        if review_item_id:
            decisions_by_item.setdefault(review_item_id, []).append(decision)

    index: dict[str, dict[str, Any]] = {}
    for item in review_items:
        item_id = _object_string(item, "review_item_id")
        decisions = decisions_by_item.get(item_id or "", [])
        priority = _object_string(item, "priority_bucket")
        status = _object_string(item, "review_status")
        features = {
            "review_item_id": item_id,
            "review_priority_bucket": priority,
            "review_priority_score": PRIORITY_SCORES.get(priority or ""),
            "review_status": status,
            "review_status_score": REVIEW_STATUS_SCORES.get(status or ""),
            "review_decision_count": len(decisions),
            "review_decision_values": [
                value for decision in decisions if (value := _decision_value(decision))
            ],
            "review_positive_decision_count": sum(
                1
                for decision in decisions
                if _decision_value(decision) in {"accept_for_followup", "needs_more_data"}
            ),
            "review_negative_decision_count": sum(
                1
                for decision in decisions
                if _decision_value(decision) in {"deprioritize", "reject"}
            ),
        }
        for key in _identity_keys(
            candidate_id=_object_string(item, "candidate_id"),
            candidate_name=_object_string(item, "candidate_name"),
            canonical_smiles=_object_string(item, "canonical_smiles"),
            inchi_key=None,
            review_item_id=item_id,
        ):
            index[key] = features
    return index


def _find_candidate_features(
    result: AssayResult,
    index: Mapping[str, _CandidateFeatures],
) -> _CandidateFeatures | None:
    for key in _identity_keys(
        candidate_id=str(result.metadata.get("linked_candidate_id") or result.candidate_id or ""),
        candidate_name=result.candidate_name,
        canonical_smiles=result.canonical_smiles,
        inchi_key=result.inchi_key,
        review_item_id=result.review_item_id,
    ):
        if key in index:
            return index[key]
    return None


def _find_review_features(
    result: AssayResult,
    index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for key in _identity_keys(
        candidate_id=str(result.metadata.get("linked_candidate_id") or result.candidate_id or ""),
        candidate_name=result.candidate_name,
        canonical_smiles=result.canonical_smiles,
        inchi_key=result.inchi_key,
        review_item_id=result.review_item_id,
    ):
        if key in index:
            return index[key]
    return None


def _add_feature(index: dict[str, _CandidateFeatures], feature: _CandidateFeatures) -> None:
    for key in _identity_keys(
        candidate_id=feature.candidate_id,
        candidate_name=feature.candidate_name,
        canonical_smiles=feature.canonical_smiles,
        inchi_key=feature.inchi_key,
        review_item_id=None,
    ):
        index[key] = feature


def _identity_keys(
    *,
    candidate_id: str | None,
    candidate_name: str | None,
    canonical_smiles: str | None,
    inchi_key: str | None,
    review_item_id: str | None,
) -> list[str]:
    keys: list[str] = []
    if candidate_id:
        keys.append(f"id:{candidate_id.strip().lower()}")
    if inchi_key:
        keys.append(f"inchi:{inchi_key.strip().upper()}")
    if canonical_smiles:
        keys.append(f"smiles:{canonical_smiles.strip()}")
    if candidate_name:
        keys.append(f"name:{candidate_name.strip().lower()}")
    if review_item_id:
        keys.append(f"review:{review_item_id.strip().lower()}")
    return keys


def _structure_features(
    canonical_smiles: str | None,
    fingerprint_n_bits: int,
) -> tuple[dict[str, float], list[int]]:
    if not canonical_smiles:
        return {}, []
    mol = mol_from_smiles(canonical_smiles)
    if mol is None:
        return {}, []
    descriptors = descriptors_from_mol(mol)
    fingerprint = morgan_fingerprint(mol, n_bits=fingerprint_n_bits)
    return descriptors, [int(bit) for bit in fingerprint.GetOnBits()]


def _assessment_index(
    assessments: Sequence[DevelopabilityAssessment],
) -> dict[str, DevelopabilityAssessment]:
    index: dict[str, DevelopabilityAssessment] = {}
    for assessment in assessments:
        for key in _identity_keys(
            candidate_id=None,
            candidate_name=assessment.molecule_name,
            canonical_smiles=assessment.canonical_smiles,
            inchi_key=None,
            review_item_id=None,
        ):
            index[key] = assessment
    return index


def _assessment_for(
    assessments: Mapping[str, DevelopabilityAssessment],
    *,
    candidate_id: str | None,
    candidate_name: str,
    canonical_smiles: str | None,
) -> DevelopabilityAssessment | None:
    for key in _identity_keys(
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        canonical_smiles=canonical_smiles,
        inchi_key=None,
        review_item_id=None,
    ):
        if key in assessments:
            return assessments[key]
    return None


def _candidate_smiles(candidate: MoleculeCandidate) -> str | None:
    smiles = candidate.chemical_metadata.get("canonical_smiles")
    if smiles is None and candidate.developability_assessment is not None:
        smiles = candidate.developability_assessment.canonical_smiles
    return str(smiles) if smiles is not None else None


def _generation_score(candidate: MoleculeCandidate) -> float | None:
    if candidate.origin != "generated":
        return None
    return candidate.score or _metadata_float(candidate.generation_metadata, "generation_score")


def _summary_for(
    summaries: Mapping[str, Mapping[str, Any]],
    candidate_id: str | None,
    candidate_name: str,
) -> Mapping[str, Any] | None:
    for key in (candidate_id, candidate_name):
        if key and key in summaries:
            return summaries[key]
    return None


def _first_endpoint_name(results: Sequence[AssayResult]) -> str | None:
    for result in results:
        if result.assay_context.endpoint.name:
            return result.assay_context.endpoint.name
    return None


def _first_context_value(results: Sequence[AssayResult], key: str) -> str | None:
    for result in results:
        value = getattr(result, key)
        if value:
            return str(value)
        context_value = getattr(result.assay_context, key, None)
        if context_value:
            return str(context_value)
    return None


def _feature_schema(fingerprint_n_bits: int) -> dict[str, Any]:
    return {
        "identity": {
            "candidate_id": "string|null",
            "candidate_name": "string",
            "candidate_origin": "existing|generated|unknown",
            "canonical_smiles": "string|null",
            "inchi_key": "string|null",
        },
        "context": {
            "disease_name": "string|null",
            "target_symbol": "string|null",
            "endpoint_name": "string",
            "endpoint_category": "string",
            "assay_type": "string",
            "known_target_match": "0|1",
        },
        "descriptor_features": {
            "desc_molecular_weight": "float|null",
            "desc_logp": "float|null",
            "desc_tpsa": "float|null",
            "desc_hbd": "float|null",
            "desc_hba": "float|null",
            "desc_rotatable_bonds": "float|null",
            "desc_aromatic_rings": "float|null",
            "desc_heavy_atom_count": "float|null",
            "desc_formal_charge": "float|null",
        },
        "fingerprint": {
            "morgan_fp_on_bits": "list[int]",
            "morgan_fp_n_bits": fingerprint_n_bits,
        },
        "ranking_and_review": {
            "existing_ranking_score": "float|null",
            "developability_score": "float|null",
            "generation_score": "float|null",
            "literature_quality": "float|null",
            "review_priority_score": "float|null",
            "review_status_score": "float|null",
        },
    }


def _label_schema(inconclusive_policy: str) -> dict[str, Any]:
    return {
        "label": "binary, continuous, safety binary, or weak inconclusive depending on label_type",
        "binary_label": {"active": 1, "inactive": 0},
        "continuous_label": "normalized_value when present, otherwise measured_value_numeric",
        "safety_label": {"toxic": 1, "non_toxic": 0},
        "excluded_by_default": ["failed_qc", "invalid", "not_tested"],
        "inconclusive_label_policy": inconclusive_policy,
    }


def _dataset_id(
    *,
    endpoint_name: str,
    disease_name: str | None,
    target_symbol: str | None,
    result_ids: Sequence[str],
) -> str:
    basis = "|".join(
        [
            disease_name or "",
            target_symbol or "",
            endpoint_name,
            ",".join(result_ids),
        ]
    )
    return f"experimental-learning-{uuid5(NAMESPACE_URL, basis)}"


def _metadata_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return str(value) if value not in (None, "") else None


def _model_predictions_from_mapping(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    predictions = metadata.get("model_predictions")
    if not isinstance(predictions, list):
        return ()
    return tuple(item for item in predictions if isinstance(item, Mapping))


def _model_predictions_from_object(candidate: Any) -> tuple[Mapping[str, Any], ...]:
    predictions: list[Mapping[str, Any]] = []
    for attr in ("metadata", "generation_metadata", "trace"):
        metadata = getattr(candidate, attr, None)
        if isinstance(metadata, Mapping):
            predictions.extend(_model_predictions_from_mapping(metadata))
    return tuple(predictions)


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _metadata_float(metadata: Mapping[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    if not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _object_string(obj: Any, attr: str) -> str | None:
    value = getattr(obj, attr, None)
    return str(value) if value not in (None, "") else None


def _object_float(obj: Any, attr: str) -> float | None:
    value = getattr(obj, attr, None)
    if not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _decision_value(decision: Any) -> str | None:
    value = getattr(decision, "decision", None)
    return str(value) if value not in (None, "") else None


__all__ = ["build_experimental_learning_dataset", "suggest_next_experiments"]
