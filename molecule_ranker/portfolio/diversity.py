from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from .schemas import PortfolioCandidate


def diversity_bonus(
    candidate: PortfolioCandidate,
    selected: Sequence[PortfolioCandidate],
) -> float:
    if not selected:
        return 0.08
    selected_targets = {target for item in selected for target in item.target_symbols}
    selected_scaffolds = {item.scaffold_id for item in selected}
    selected_series = {item.chemical_series_id for item in selected}
    bonus = 0.0
    if not set(candidate.target_symbols).intersection(selected_targets):
        bonus += 0.07
    if candidate.scaffold_id not in selected_scaffolds:
        bonus += 0.05
    if candidate.chemical_series_id not in selected_series:
        bonus += 0.05
    return bonus


def correlated_risk_penalty(
    candidate: PortfolioCandidate,
    selected: Sequence[PortfolioCandidate],
) -> float:
    selected_flags = {flag for item in selected for flag in item.risk_flags}
    return min(0.24, 0.08 * len(selected_flags.intersection(candidate.risk_flags)))


def summarize_diversity(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    diversity = compute_portfolio_diversity(candidates)
    targets = Counter(target for candidate in candidates for target in candidate.target_symbols)
    mechanisms = Counter(candidate.mechanism_label or "unspecified" for candidate in candidates)
    series = Counter(candidate.chemical_series_id or "unspecified" for candidate in candidates)
    scaffolds = Counter(candidate.scaffold_id or "unspecified" for candidate in candidates)
    return {
        "targets": dict(sorted(targets.items())),
        "mechanisms": dict(sorted(mechanisms.items())),
        "chemical_series": dict(sorted(series.items())),
        "scaffolds": dict(sorted(scaffolds.items())),
        "target_count": len(targets),
        "chemical_series_count": len(series),
        "scaffold_count": len(scaffolds),
        "overrepresented_targets": [key for key, value in targets.items() if value > 2],
        "overrepresented_chemical_series": [key for key, value in series.items() if value > 2],
        "overrepresented_scaffolds": [key for key, value in scaffolds.items() if value > 2],
        "dimension_scores": diversity["dimension_scores"],
        "overall_diversity_score": diversity["overall_diversity_score"],
        "near_duplicate_pairs": diversity["near_duplicate_pairs"],
    }


def target_coverage(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    targets = sorted({target for candidate in candidates for target in candidate.target_symbols})
    return {"covered_targets": targets, "covered_target_count": len(targets)}


def compute_portfolio_diversity(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    candidates = list(candidates)
    target_counts = Counter(
        target for candidate in candidates for target in candidate.target_symbols
    )
    mechanism_counts = Counter(
        candidate.mechanism_label or "unspecified" for candidate in candidates
    )
    scaffold_counts = Counter(_scaffold_key(candidate) for candidate in candidates)
    evidence_source_counts = Counter(
        source
        for candidate in candidates
        for source in _evidence_sources(candidate) or ["unspecified"]
    )
    origin_counts = Counter(candidate.origin for candidate in candidates)
    experimental_status_counts = Counter(
        _experimental_status(candidate) for candidate in candidates
    )
    matrix = compute_pairwise_similarity_matrix(candidates)
    fingerprint_diversity = _mean_pairwise_distance(matrix)
    dimension_scores = {
        "target_diversity": _category_diversity(target_counts, len(candidates)),
        "mechanism_diversity": _category_diversity(mechanism_counts, len(candidates)),
        "scaffold_diversity": _category_diversity(scaffold_counts, len(candidates)),
        "fingerprint_diversity": fingerprint_diversity,
        "evidence_source_diversity": _category_diversity(evidence_source_counts, len(candidates)),
        "origin_diversity": _category_diversity(origin_counts, len(candidates)),
        "experimental_status_diversity": _category_diversity(
            experimental_status_counts, len(candidates)
        ),
    }
    near_duplicate_pairs = [
        {"candidate_ids": [left, right], "similarity": score}
        for left, row in matrix.items()
        for right, score in row.items()
        if left < right and score >= 0.85
    ]
    return {
        "candidate_count": len(candidates),
        "dimension_scores": dimension_scores,
        "overall_diversity_score": _round(_mean(dimension_scores.values())),
        "target_coverage": target_coverage(candidates),
        "category_counts": {
            "targets": dict(sorted(target_counts.items())),
            "mechanisms": dict(sorted(mechanism_counts.items())),
            "scaffolds": dict(sorted(scaffold_counts.items())),
            "evidence_sources": dict(sorted(evidence_source_counts.items())),
            "origins": dict(sorted(origin_counts.items())),
            "experimental_statuses": dict(sorted(experimental_status_counts.items())),
        },
        "pairwise_similarity_matrix": matrix,
        "near_duplicate_pairs": near_duplicate_pairs,
        "warnings": _diversity_warnings(dimension_scores, near_duplicate_pairs),
    }


def compute_pairwise_similarity_matrix(
    candidates: Sequence[PortfolioCandidate],
) -> dict[str, dict[str, float]]:
    candidates = list(candidates)
    matrix: dict[str, dict[str, float]] = {
        candidate.portfolio_candidate_id: {} for candidate in candidates
    }
    for candidate in candidates:
        for other in candidates:
            if candidate.portfolio_candidate_id == other.portfolio_candidate_id:
                score = 1.0
            else:
                score = _candidate_similarity(candidate, other)
            matrix[candidate.portfolio_candidate_id][other.portfolio_candidate_id] = _round(score)
    return matrix


def suggest_diversity_improvements(
    selection: Sequence[PortfolioCandidate],
    candidate_pool: Sequence[PortfolioCandidate],
) -> list[dict[str, Any]]:
    selected_ids = {candidate.portfolio_candidate_id for candidate in selection}
    selected_targets = {target for candidate in selection for target in candidate.target_symbols}
    selected_scaffolds = {_scaffold_key(candidate) for candidate in selection}
    selected_origins = {candidate.origin for candidate in selection}
    matrix_pool = list(candidate_pool)
    suggestions: list[dict[str, Any]] = []
    for candidate in matrix_pool:
        if candidate.portfolio_candidate_id in selected_ids:
            continue
        reasons: list[str] = []
        if set(candidate.target_symbols).difference(selected_targets):
            reasons.append("adds_underrepresented_target")
        if _scaffold_key(candidate) not in selected_scaffolds:
            reasons.append("adds_scaffold_or_series")
        if candidate.origin not in selected_origins:
            reasons.append("adds_origin_balance")
        max_similarity = max(
            (
                _candidate_similarity(candidate, selected_candidate)
                for selected_candidate in selection
            ),
            default=0.0,
        )
        if max_similarity < 0.65:
            reasons.append("low_near_duplicate_similarity")
        if not reasons:
            continue
        suggestions.append(
            {
                "candidate_id": candidate.portfolio_candidate_id,
                "reasons": sorted(reasons),
                "max_similarity_to_selection": _round(max_similarity),
                "target_symbols": list(candidate.target_symbols),
                "scaffold_or_series": _scaffold_key(candidate),
                "origin": candidate.origin,
            }
        )
    return sorted(
        suggestions,
        key=lambda item: (len(item["reasons"]), -float(item["max_similarity_to_selection"])),
        reverse=True,
    )


def _candidate_similarity(
    candidate: PortfolioCandidate,
    other: PortfolioCandidate,
) -> float:
    metadata_similarity = _metadata_pairwise_similarity(candidate, other)
    if metadata_similarity is not None:
        return metadata_similarity
    if candidate.inchi_key and candidate.inchi_key == other.inchi_key:
        return 1.0
    if candidate.canonical_smiles and candidate.canonical_smiles == other.canonical_smiles:
        return 1.0
    fingerprint_similarity = _fingerprint_similarity(candidate, other)
    scaffold_similarity = 1.0 if _scaffold_key(candidate) == _scaffold_key(other) else 0.0
    series_similarity = (
        1.0
        if candidate.chemical_series_id and candidate.chemical_series_id == other.chemical_series_id
        else 0.0
    )
    target_similarity = _jaccard(set(candidate.target_symbols), set(other.target_symbols))
    origin_similarity = 1.0 if candidate.origin == other.origin else 0.0
    return max(
        fingerprint_similarity,
        0.35 * scaffold_similarity
        + 0.25 * series_similarity
        + 0.25 * target_similarity
        + 0.15 * origin_similarity,
    )


def _metadata_pairwise_similarity(
    candidate: PortfolioCandidate,
    other: PortfolioCandidate,
) -> float | None:
    for source, target in ((candidate, other), (other, candidate)):
        pairwise = source.metadata.get("pairwise_similarity")
        if not isinstance(pairwise, dict):
            continue
        value = pairwise.get(target.portfolio_candidate_id)
        if isinstance(value, int | float):
            return _bounded(float(value))
    return None


def _fingerprint_similarity(
    candidate: PortfolioCandidate,
    other: PortfolioCandidate,
) -> float:
    left = _fingerprint_features(candidate)
    right = _fingerprint_features(other)
    if left or right:
        return _jaccard(left, right)
    return 0.0


def _fingerprint_features(candidate: PortfolioCandidate) -> set[str]:
    for container in (candidate.diversity_features, candidate.metadata):
        for key in ("fingerprint", "fingerprints", "ecfp", "morgan_fingerprint"):
            value = container.get(key)
            features = _feature_set(value)
            if features:
                return features
    return set()


def _feature_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key, bit in value.items() if bool(bit)}
    if isinstance(value, list | tuple | set):
        return {str(item) for item in value}
    if isinstance(value, str):
        return {item for item in value.replace(",", " ").split() if item}
    return set()


def _category_diversity(counts: Counter[str], candidate_count: int) -> float:
    if candidate_count <= 0 or not counts:
        return 0.0
    return _round(min(1.0, len(counts) / candidate_count))


def _mean_pairwise_distance(matrix: dict[str, dict[str, float]]) -> float:
    distances = [
        1.0 - score for left, row in matrix.items() for right, score in row.items() if left < right
    ]
    return _round(_mean(distances))


def _diversity_warnings(
    dimension_scores: dict[str, float],
    near_duplicate_pairs: Sequence[dict[str, Any]],
) -> list[str]:
    warnings = []
    if near_duplicate_pairs:
        warnings.append("near_duplicate_candidates_reduce_diversity")
    for dimension, score in dimension_scores.items():
        if score < 0.35:
            warnings.append(f"low_{dimension}")
    return sorted(set(warnings))


def _evidence_sources(candidate: PortfolioCandidate) -> list[str]:
    sources = candidate.metadata.get("evidence_sources")
    if isinstance(sources, list):
        return [str(source) for source in sources if source]
    refs = candidate.metadata.get("artifact_refs")
    if isinstance(refs, dict):
        return [str(value) for key, value in refs.items() if "evidence" in str(key)]
    return []


def _experimental_status(candidate: PortfolioCandidate) -> str:
    if candidate.direct_experimental_evidence:
        return "direct_evidence"
    if candidate.experimental_support_score is not None:
        return "linked_summary"
    if candidate.generated_without_direct_evidence:
        return "generated_without_direct_evidence"
    return "no_direct_experimental_evidence"


def _scaffold_key(candidate: PortfolioCandidate) -> str:
    return candidate.scaffold_id or candidate.chemical_series_id or "unspecified"


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def _mean(values: Sequence[float] | Any) -> float:
    concrete = [float(value) for value in values]
    if not concrete:
        return 0.0
    return sum(concrete) / len(concrete)


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def _round(value: float) -> float:
    return round(_bounded(value), 3)
