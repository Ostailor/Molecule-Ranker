from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from molecule_ranker.experiments.schemas import AssayResult

from .schemas import PortfolioCandidate


def risk_flags_from_text(values: Sequence[str]) -> list[str]:
    flags: set[str] = set()
    for value in values:
        lowered = value.lower().replace(" ", "_")
        for token in (
            "critical",
            "toxicity",
            "safety",
            "developability",
            "liability",
            "alert",
            "rejected",
            "docking",
            "structure",
        ):
            if token in lowered:
                flags.add(token)
    return sorted(flags)


def blocking_risks_from_text(values: Sequence[str]) -> list[str]:
    text = " ".join(values).lower()
    risks: set[str] = set()
    if "critical" in text or "rejected" in text:
        risks.add("critical_or_rejected")
    if "tox" in text or "safety" in text:
        risks.add("safety_or_toxicity_review")
    return sorted(risks)


def risk_score(
    *,
    risk_flags: Sequence[str],
    blocking_risks: Sequence[str],
    developability_score: float | None,
    exact_results: Sequence[AssayResult] = (),
) -> float:
    score = 0.15
    if blocking_risks:
        score = max(score, 0.85)
    if any(flag in {"toxicity", "safety"} for flag in risk_flags):
        score = max(score, 0.62)
    if any(flag in {"alert", "liability", "developability"} for flag in risk_flags):
        score = max(score, 0.48)
    if developability_score is not None:
        score = max(score, 1.0 - developability_score)
    if any(result.assay_context.endpoint.endpoint_category == "safety" for result in exact_results):
        score = max(score, 0.58)
    return round(min(1.0, max(0.0, score)), 3)


def risk_clusters(candidates: Sequence[PortfolioCandidate]) -> dict[str, list[str]]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        for flag in candidate.risk_flags:
            clusters[flag].append(candidate.portfolio_candidate_id)
    return {flag: sorted(ids) for flag, ids in sorted(clusters.items()) if len(ids) > 1}


def summarize_risk(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    correlated = identify_correlated_risks(candidates)
    concentration = compute_risk_concentration(candidates)
    return {
        "risk_flag_count": sum(len(candidate.risk_flags) for candidate in candidates),
        "blocking_risk_count": sum(len(candidate.blocking_risks) for candidate in candidates),
        "candidates_with_blocking_risks": [
            candidate.portfolio_candidate_id for candidate in candidates if candidate.blocking_risks
        ],
        "correlated_risk_clusters": risk_clusters(candidates),
        "correlated_risks": correlated["clusters"],
        "risk_concentration": concentration,
    }


def identify_correlated_risks(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    candidates = list(candidates)
    by_dimension: dict[str, list[dict[str, Any]]] = {
        "shared_scaffold_liability": _shared_scaffold_liability(candidates),
        "shared_alert_or_toxicophore": _shared_flag_clusters(
            candidates,
            dimension="shared_alert_or_toxicophore",
            tokens={"alert", "toxicophore", "toxicity", "reactive", "liability"},
        ),
        "shared_admet_risk": _shared_flag_clusters(
            candidates,
            dimension="shared_admet_risk",
            tokens={
                "admet",
                "herg",
                "cyp",
                "clearance",
                "permeability",
                "solubility",
                "metabolic",
            },
        ),
        "shared_target_safety_risk": _shared_target_safety_risk(candidates),
        "shared_evidence_gap": _shared_binary_cluster(
            candidates,
            dimension="shared_evidence_gap",
            predicate=_has_evidence_gap,
            mode="no_direct_or_source_evidence",
        ),
        "shared_assay_uncertainty": _shared_binary_cluster(
            candidates,
            dimension="shared_assay_uncertainty",
            predicate=_has_assay_uncertainty,
            mode="high_assay_or_model_uncertainty",
        ),
        "shared_generated_only_status": _shared_binary_cluster(
            candidates,
            dimension="shared_generated_only_status",
            predicate=lambda candidate: candidate.generated_without_direct_evidence,
            mode="generated_without_direct_evidence",
        ),
        "shared_structure_confidence_weakness": _shared_binary_cluster(
            candidates,
            dimension="shared_structure_confidence_weakness",
            predicate=_has_structure_confidence_weakness,
            mode="low_structure_confidence",
        ),
    }
    clusters = [
        cluster
        for dimension_clusters in by_dimension.values()
        for cluster in dimension_clusters
        if len(cluster["candidate_ids"]) > 1
    ]
    return {
        "cluster_count": len(clusters),
        "clusters": sorted(
            clusters,
            key=lambda item: (
                str(item["risk_dimension"]),
                str(item["mode"]),
                item["candidate_ids"],
            ),
        ),
        "by_dimension": by_dimension,
        "warnings": _correlated_risk_warnings(clusters),
    }


def compute_risk_concentration(candidates: Sequence[PortfolioCandidate]) -> dict[str, Any]:
    candidates = list(candidates)
    count = len(candidates)
    correlated = identify_correlated_risks(candidates)
    dimension_fractions = {
        "blocking_risk_fraction": _fraction(
            sum(bool(candidate.blocking_risks) for candidate in candidates), count
        ),
        "risk_flag_fraction": _fraction(
            sum(bool(candidate.risk_flags) for candidate in candidates), count
        ),
        "generated_only_fraction": _fraction(
            sum(candidate.generated_without_direct_evidence for candidate in candidates), count
        ),
        "evidence_gap_fraction": _fraction(
            sum(_has_evidence_gap(candidate) for candidate in candidates), count
        ),
        "assay_uncertainty_fraction": _fraction(
            sum(_has_assay_uncertainty(candidate) for candidate in candidates), count
        ),
        "structure_confidence_weakness_fraction": _fraction(
            sum(_has_structure_confidence_weakness(candidate) for candidate in candidates), count
        ),
    }
    concentration_score = _round(
        0.45 * _fraction(correlated["cluster_count"], max(1, count))
        + 0.55 * _mean(dimension_fractions.values())
    )
    warnings = []
    if dimension_fractions["generated_only_fraction"] > 0.5:
        warnings.append("generated_only_concentration")
    if dimension_fractions["evidence_gap_fraction"] > 0.5:
        warnings.append("evidence_gap_concentration")
    if any(
        cluster["risk_dimension"] == "shared_alert_or_toxicophore"
        for cluster in correlated["clusters"]
    ):
        warnings.append("correlated_alert_or_toxicophore")
    return {
        "candidate_count": count,
        "dimension_fractions": dimension_fractions,
        "correlated_cluster_count": correlated["cluster_count"],
        "concentration_score": concentration_score,
        "warnings": sorted(set(warnings)),
    }


def _shared_scaffold_liability(
    candidates: Sequence[PortfolioCandidate],
) -> list[dict[str, Any]]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        if not _has_liability_signal(candidate):
            continue
        scaffold = candidate.scaffold_id or candidate.chemical_series_id
        if scaffold:
            clusters[str(scaffold)].append(candidate.portfolio_candidate_id)
    return _cluster_records("shared_scaffold_liability", clusters)


def _shared_flag_clusters(
    candidates: Sequence[PortfolioCandidate],
    *,
    dimension: str,
    tokens: set[str],
) -> list[dict[str, Any]]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        for flag in [*candidate.risk_flags, *candidate.blocking_risks]:
            normalized = _normalize(flag)
            if any(token in normalized for token in tokens):
                clusters[normalized].append(candidate.portfolio_candidate_id)
        for value in _metadata_values(candidate, dimension):
            normalized = _normalize(value)
            if any(token in normalized for token in tokens):
                clusters[normalized].append(candidate.portfolio_candidate_id)
    return _cluster_records(dimension, clusters)


def _shared_target_safety_risk(
    candidates: Sequence[PortfolioCandidate],
) -> list[dict[str, Any]]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        if not _has_target_safety_signal(candidate):
            continue
        for target in candidate.target_symbols or ["unspecified_target"]:
            clusters[str(target)].append(candidate.portfolio_candidate_id)
    return _cluster_records("shared_target_safety_risk", clusters)


def _shared_binary_cluster(
    candidates: Sequence[PortfolioCandidate],
    *,
    dimension: str,
    predicate: Any,
    mode: str,
) -> list[dict[str, Any]]:
    ids = [candidate.portfolio_candidate_id for candidate in candidates if predicate(candidate)]
    if len(ids) <= 1:
        return []
    return [{"risk_dimension": dimension, "mode": mode, "candidate_ids": sorted(ids)}]


def _cluster_records(dimension: str, clusters: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {"risk_dimension": dimension, "mode": mode, "candidate_ids": sorted(set(ids))}
        for mode, ids in sorted(clusters.items())
        if len(set(ids)) > 1
    ]


def _has_liability_signal(candidate: PortfolioCandidate) -> bool:
    text = _candidate_text(candidate)
    return any(token in text for token in ("liability", "alert", "toxicophore", "toxicity"))


def _has_target_safety_signal(candidate: PortfolioCandidate) -> bool:
    text = _candidate_text(candidate)
    metadata = candidate.metadata
    return "target_safety" in text or "safety" in text or bool(metadata.get("target_safety_risk"))


def _has_evidence_gap(candidate: PortfolioCandidate) -> bool:
    return (
        not candidate.direct_experimental_evidence
        and candidate.evidence_score is None
        and candidate.experimental_support_score is None
    )


def _has_assay_uncertainty(candidate: PortfolioCandidate) -> bool:
    metadata = candidate.metadata
    uncertainty = metadata.get("assay_uncertainty")
    if isinstance(uncertainty, int | float) and uncertainty >= 0.6:
        return True
    return bool(candidate.uncertainty_score is not None and candidate.uncertainty_score >= 0.75)


def _has_structure_confidence_weakness(candidate: PortfolioCandidate) -> bool:
    metadata = candidate.metadata
    confidence = metadata.get("structure_confidence")
    if isinstance(confidence, int | float):
        return confidence < 0.5
    return bool(candidate.structure_score is not None and candidate.structure_score < 0.35)


def _metadata_values(candidate: PortfolioCandidate, dimension: str) -> list[str]:
    key_map = {
        "shared_alert_or_toxicophore": (
            "alerts",
            "toxicophores",
            "chemical_liability_flags",
        ),
        "shared_admet_risk": ("admet_risks", "admet_flags"),
    }
    values: list[str] = []
    for key in key_map.get(dimension, ()):
        raw = candidate.metadata.get(key)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if item)
        elif raw:
            values.append(str(raw))
    return values


def _candidate_text(candidate: PortfolioCandidate) -> str:
    return " ".join([*candidate.risk_flags, *candidate.blocking_risks]).lower()


def _correlated_risk_warnings(clusters: Sequence[dict[str, Any]]) -> list[str]:
    warnings = []
    dimensions = {str(cluster["risk_dimension"]) for cluster in clusters}
    if "shared_alert_or_toxicophore" in dimensions:
        warnings.append("correlated_safety_or_alert_flags")
    if "shared_generated_only_status" in dimensions:
        warnings.append("generated_only_concentration")
    if "shared_evidence_gap" in dimensions:
        warnings.append("shared_evidence_gap")
    return sorted(warnings)


def _normalize(value: str) -> str:
    return "_".join(str(value).lower().replace("-", "_").split())


def _fraction(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _round(float(numerator) / float(denominator))


def _mean(values: Sequence[float] | Any) -> float:
    concrete = [float(value) for value in values]
    if not concrete:
        return 0.0
    return sum(concrete) / len(concrete)


def _round(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 3)
