from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .schemas import PortfolioCandidate

UNCERTAINTY_SOURCE_WEIGHTS = {
    "sparse_experimental_data": 0.14,
    "uncalibrated_model_prediction": 0.12,
    "out_of_domain_model_prediction": 0.14,
    "weak_literature_evidence": 0.10,
    "generated_only_molecule": 0.14,
    "weak_structure_context": 0.10,
    "conflicting_review_decisions": 0.12,
    "ambiguous_external_mapping": 0.08,
    "missing_developability_assessment": 0.06,
}


def uncertainty_from_confidence(confidence: float | None) -> float:
    if confidence is None:
        return 0.5
    return round(min(1.0, max(0.0, 1.0 - float(confidence))), 3)


def summarize_uncertainty(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    values = [candidate.uncertainty_score or 0.0 for candidate in candidates]
    if not values:
        return {
            "mean_uncertainty": 0.0,
            "mean_source_uncertainty": 0.0,
            "high_uncertainty_candidate_ids": [],
            "uncertainty_sources": {},
        }
    source_summaries = {
        candidate.portfolio_candidate_id: compute_candidate_uncertainty(candidate)
        for candidate in candidates
    }
    source_counts: dict[str, int] = {}
    for summary in source_summaries.values():
        for source, value in summary["sources"].items():
            if value > 0:
                source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "mean_uncertainty": round(sum(values) / len(values), 3),
        "mean_source_uncertainty": round(
            sum(summary["uncertainty_score"] for summary in source_summaries.values())
            / len(source_summaries),
            3,
        ),
        "high_uncertainty_candidate_ids": [
            candidate.portfolio_candidate_id
            for candidate in candidates
            if (candidate.uncertainty_score or 0.0) >= 0.7
        ],
        "uncertainty_sources": dict(sorted(source_counts.items())),
        "candidate_uncertainty": source_summaries,
    }


def compute_candidate_uncertainty(candidate: PortfolioCandidate) -> dict[str, Any]:
    sources = candidate_uncertainty_sources(candidate)
    weighted = sum(UNCERTAINTY_SOURCE_WEIGHTS[source] * value for source, value in sources.items())
    score = max(candidate.uncertainty_score or 0.0, weighted)
    return {
        "candidate_id": candidate.portfolio_candidate_id,
        "uncertainty_score": _round(score),
        "sources": {key: _round(value) for key, value in sources.items()},
        "dominant_sources": sorted([source for source, value in sources.items() if value >= 0.75]),
    }


def candidate_uncertainty_sources(candidate: PortfolioCandidate) -> dict[str, float]:
    metadata = candidate.metadata
    sources = {
        "sparse_experimental_data": float(_has_sparse_experimental_data(candidate)),
        "uncalibrated_model_prediction": float(_has_uncalibrated_model_prediction(candidate)),
        "out_of_domain_model_prediction": float(_has_out_of_domain_model_prediction(candidate)),
        "weak_literature_evidence": float(_has_weak_literature_evidence(candidate)),
        "generated_only_molecule": float(candidate.generated_without_direct_evidence),
        "weak_structure_context": _weak_structure_context_score(candidate),
        "conflicting_review_decisions": float(_has_conflicting_review_decisions(candidate)),
        "ambiguous_external_mapping": float(_has_ambiguous_external_mapping(candidate)),
        "missing_developability_assessment": float(candidate.developability_score is None),
    }
    overrides = metadata.get("uncertainty_sources")
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in UNCERTAINTY_SOURCE_WEIGHTS and isinstance(value, int | float):
                sources[str(key)] = _bounded(float(value))
    return sources


def _has_sparse_experimental_data(candidate: PortfolioCandidate) -> bool:
    if candidate.direct_experimental_evidence:
        result_count = candidate.metadata.get("experimental_result_count")
        return isinstance(result_count, int) and result_count <= 1
    return candidate.experimental_support_score is None


def _has_uncalibrated_model_prediction(candidate: PortfolioCandidate) -> bool:
    if candidate.predictive_model_score is None:
        return False
    return candidate.metadata.get("model_prediction_calibrated") is not True


def _has_out_of_domain_model_prediction(candidate: PortfolioCandidate) -> bool:
    return bool(
        candidate.metadata.get("model_out_of_domain")
        or candidate.metadata.get("applicability_domain") == "out_of_domain"
    )


def _has_weak_literature_evidence(candidate: PortfolioCandidate) -> bool:
    literature_score = candidate.metadata.get("literature_evidence_score")
    if isinstance(literature_score, int | float):
        return float(literature_score) < 0.35
    sources = candidate.metadata.get("evidence_sources")
    return (
        isinstance(sources, list)
        and "literature" in {str(source).lower() for source in sources}
        and (candidate.evidence_score or 0.0) < 0.35
    )


def _weak_structure_context_score(candidate: PortfolioCandidate) -> float:
    if candidate.structure_score is None:
        return 0.5
    if candidate.structure_score < 0.35:
        return 1.0
    if candidate.structure_score < 0.6:
        return 0.5
    return 0.0


def _has_conflicting_review_decisions(candidate: PortfolioCandidate) -> bool:
    if candidate.metadata.get("conflicting_review_decisions"):
        return True
    decisions = set()
    for record in candidate.metadata.get("review_records", []):
        if isinstance(record, dict):
            decision = record.get("decision") or record.get("review_status") or record.get("status")
            if decision:
                decisions.add(str(decision).lower())
    return len(decisions) > 1


def _has_ambiguous_external_mapping(candidate: PortfolioCandidate) -> bool:
    if candidate.metadata.get("ambiguous_external_mapping"):
        return True
    if candidate.metadata.get("identifier_conflicts"):
        return True
    confidence = candidate.metadata.get("mapping_confidence")
    return isinstance(confidence, int | float) and float(confidence) < 0.5


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def _round(value: float) -> float:
    return round(_bounded(value), 3)
