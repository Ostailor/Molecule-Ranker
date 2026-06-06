from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

from molecule_ranker.biologics.schemas import (
    AntibodyDevelopabilityAssessment,
    AntibodyNoveltyAssessment,
    AntigenContext,
    BiologicCandidate,
    GeneratedAntibodyHypothesis,
)

GENERATED_ANTIBODY_CONFIDENCE_CAP = 0.45
GENERATED_ANTIBODY_IMPORTED_RESULT_CONFIDENCE_CAP = 0.75


class BiologicsScoreComponents(TypedDict):
    target_context_score: float
    evidence_score: float
    sequence_validity_score: float
    novelty_score: float
    developability_heuristic_score: float
    antigen_context_score: float
    experimental_support_score: float
    review_readiness_score: float
    uncertainty_penalty: float
    risk_penalty: float
    total_score: float
    effective_confidence: float


def score_biologic_candidate(candidate: BiologicCandidate) -> float:
    """Return a bounded ranking score for an existing biologic candidate."""

    return score_biologic_candidate_components(candidate)["total_score"]


def score_biologic_candidate_components(
    candidate: BiologicCandidate,
    *,
    antigen_context: AntigenContext | None = None,
    novelty: AntibodyNoveltyAssessment | None = None,
    developability: AntibodyDevelopabilityAssessment | None = None,
) -> BiologicsScoreComponents:
    """Score an existing biologic without inferring missing antibody facts."""

    metadata = candidate.metadata
    target_context_score = 1.0 if candidate.target_symbols else 0.2
    evidence_score = _evidence_score(candidate.evidence_item_ids)
    sequence_validity_score = _sequence_validity_score(
        metadata.get("sequence_validation"),
        has_sequences=bool(candidate.sequence_ids),
        missing_sequence_allowed=True,
    )
    novelty_score = _existing_novelty_score(
        novelty or _metadata_novelty_assessment(metadata.get("novelty"))
    )
    developability_heuristic_score = _developability_score(
        developability,
        metadata.get("developability"),
    )
    antigen_context_score = _antigen_context_score(
        antigen_context,
        has_antigen_names=bool(candidate.antigen_names),
    )
    experimental_support_score = (
        1.0
        if candidate.direct_experimental_evidence
        else 0.35
        if candidate.evidence_item_ids
        else 0.0
    )
    review_readiness_score = _review_readiness_score(
        warnings=candidate.warnings,
        has_target=bool(candidate.target_symbols),
        has_evidence=bool(candidate.evidence_item_ids),
        has_sequence=bool(candidate.sequence_ids),
        validation_metadata=metadata.get("sequence_validation"),
        novelty_known=novelty is not None or "novelty" in metadata,
        developability_known=developability is not None or "developability" in metadata,
    )
    uncertainty_penalty = _candidate_uncertainty_penalty(
        candidate,
        has_novelty=novelty is not None or "novelty" in metadata,
        has_developability=developability is not None or "developability" in metadata,
    )
    risk_penalty = _candidate_risk_penalty(
        candidate,
        novelty=novelty,
        developability=developability,
        developability_metadata=metadata.get("developability"),
    )
    total_score = _weighted_total(
        target_context_score=target_context_score,
        evidence_score=evidence_score,
        sequence_validity_score=sequence_validity_score,
        novelty_score=novelty_score,
        developability_heuristic_score=developability_heuristic_score,
        antigen_context_score=antigen_context_score,
        experimental_support_score=experimental_support_score,
        review_readiness_score=review_readiness_score,
        uncertainty_penalty=uncertainty_penalty,
        risk_penalty=risk_penalty,
    )
    effective_confidence = _clamp(
        0.35
        + 0.2 * target_context_score
        + 0.2 * evidence_score
        + 0.15 * experimental_support_score
        + 0.1 * review_readiness_score
        - 0.2 * uncertainty_penalty
    )
    return {
        "target_context_score": target_context_score,
        "evidence_score": evidence_score,
        "sequence_validity_score": sequence_validity_score,
        "novelty_score": novelty_score,
        "developability_heuristic_score": developability_heuristic_score,
        "antigen_context_score": antigen_context_score,
        "experimental_support_score": experimental_support_score,
        "review_readiness_score": review_readiness_score,
        "uncertainty_penalty": uncertainty_penalty,
        "risk_penalty": risk_penalty,
        "total_score": total_score,
        "effective_confidence": effective_confidence,
    }


def rank_biologic_candidates(
    candidates: Sequence[BiologicCandidate],
) -> list[BiologicCandidate]:
    """Rank existing biologic candidates separately from generated hypotheses."""

    return sorted(
        candidates,
        key=lambda candidate: (
            -score_biologic_candidate(candidate),
            candidate.biologic_id,
        ),
    )


def score_generated_antibody_hypothesis(
    hypothesis: GeneratedAntibodyHypothesis,
    *,
    exact_imported_result_ids: Iterable[str] = (),
) -> BiologicsScoreComponents:
    """Score a generated antibody hypothesis without converting context into evidence."""

    metadata = hypothesis.metadata
    imported_result_linked = _has_exact_imported_result(
        hypothesis,
        exact_imported_result_ids,
    )
    target_context_score = 0.8 if hypothesis.target_symbols else 0.2
    parent_context_ids = _metadata_string_list(metadata, "parent_evidence_item_ids")
    seed_context_ids = _metadata_string_list(metadata, "seed_evidence_item_ids")
    evidence_score = (
        1.0
        if imported_result_linked
        else 0.15
        if parent_context_ids or seed_context_ids
        else 0.0
    )
    sequence_validity_score = _sequence_validity_score(
        metadata.get("sequence_validation"),
        has_sequences=bool(hypothesis.generated_sequence_ids),
        missing_sequence_allowed=False,
    )
    novelty_score = _generated_novelty_score(metadata.get("novelty"))
    developability_heuristic_score = _developability_score(
        None,
        metadata.get("developability"),
    )
    antigen_context_score = (
        _antigen_context_score(None, has_antigen_names=True)
        if hypothesis.antigen_context_id
        else 0.35
    )
    experimental_support_score = 1.0 if imported_result_linked else 0.0
    review_readiness_score = _review_readiness_score(
        warnings=hypothesis.warnings,
        has_target=bool(hypothesis.target_symbols),
        has_evidence=imported_result_linked,
        has_sequence=bool(hypothesis.generated_sequence_ids),
        validation_metadata=metadata.get("sequence_validation"),
        novelty_known="novelty" in metadata,
        developability_known="developability" in metadata,
    )
    uncertainty_penalty = _generated_uncertainty_penalty(
        hypothesis,
        imported_result_linked=imported_result_linked,
    )
    risk_penalty = _generated_risk_penalty(hypothesis)
    total_score = _weighted_total(
        target_context_score=target_context_score,
        evidence_score=evidence_score,
        sequence_validity_score=sequence_validity_score,
        novelty_score=novelty_score,
        developability_heuristic_score=developability_heuristic_score,
        antigen_context_score=antigen_context_score,
        experimental_support_score=experimental_support_score,
        review_readiness_score=review_readiness_score,
        uncertainty_penalty=uncertainty_penalty,
        risk_penalty=risk_penalty,
    )
    effective_confidence = _clamp(hypothesis.confidence)
    if imported_result_linked:
        effective_confidence = min(
            effective_confidence,
            GENERATED_ANTIBODY_IMPORTED_RESULT_CONFIDENCE_CAP,
        )
    else:
        effective_confidence = min(
            effective_confidence,
            GENERATED_ANTIBODY_CONFIDENCE_CAP,
        )

    return {
        "target_context_score": target_context_score,
        "evidence_score": evidence_score,
        "sequence_validity_score": sequence_validity_score,
        "novelty_score": novelty_score,
        "developability_heuristic_score": developability_heuristic_score,
        "antigen_context_score": antigen_context_score,
        "experimental_support_score": experimental_support_score,
        "review_readiness_score": review_readiness_score,
        "uncertainty_penalty": uncertainty_penalty,
        "risk_penalty": risk_penalty,
        "total_score": total_score,
        "effective_confidence": effective_confidence,
    }


def rank_generated_antibody_hypotheses(
    hypotheses: Sequence[GeneratedAntibodyHypothesis],
    *,
    exact_imported_result_ids: Iterable[str] = (),
) -> list[GeneratedAntibodyHypothesis]:
    """Rank generated antibody hypotheses independently from existing candidates."""

    imported_result_ids = tuple(str(identifier) for identifier in exact_imported_result_ids)
    return sorted(
        hypotheses,
        key=lambda hypothesis: (
            -score_generated_antibody_hypothesis(
                hypothesis,
                exact_imported_result_ids=imported_result_ids,
            )["total_score"],
            hypothesis.generated_antibody_id,
        ),
    )


def _weighted_total(
    *,
    target_context_score: float,
    evidence_score: float,
    sequence_validity_score: float,
    novelty_score: float,
    developability_heuristic_score: float,
    antigen_context_score: float,
    experimental_support_score: float,
    review_readiness_score: float,
    uncertainty_penalty: float,
    risk_penalty: float,
) -> float:
    positive = (
        0.14 * target_context_score
        + 0.15 * evidence_score
        + 0.11 * sequence_validity_score
        + 0.1 * novelty_score
        + 0.13 * developability_heuristic_score
        + 0.09 * antigen_context_score
        + 0.18 * experimental_support_score
        + 0.1 * review_readiness_score
    )
    return _clamp(positive - 0.1 * uncertainty_penalty - 0.12 * risk_penalty)


def _evidence_score(evidence_item_ids: Sequence[str]) -> float:
    if not evidence_item_ids:
        return 0.0
    return _clamp(0.4 + min(len(evidence_item_ids), 3) * 0.2)


def _sequence_validity_score(
    validation_metadata: Any,
    *,
    has_sequences: bool,
    missing_sequence_allowed: bool,
) -> float:
    metadata = _mapping(validation_metadata)
    if metadata:
        if metadata.get("valid") is False:
            return 0.0
        warnings = _string_list(metadata.get("warnings"))
        return _clamp(1.0 - min(len(warnings), 4) * 0.1)
    if has_sequences:
        return 0.65
    return 0.35 if missing_sequence_allowed else 0.0


def _existing_novelty_score(novelty: AntibodyNoveltyAssessment | Mapping[str, Any] | None) -> float:
    novelty_class = _novelty_class(novelty)
    if novelty_class == "known":
        return 0.85
    if novelty_class == "near_duplicate":
        return 0.55
    if novelty_class == "close_variant":
        return 0.7
    if novelty_class == "novel_candidate":
        return 0.75
    return 0.45


def _generated_novelty_score(novelty_metadata: Any) -> float:
    novelty_class = _novelty_class(_mapping(novelty_metadata))
    if novelty_class == "known":
        return 0.0
    if novelty_class == "near_duplicate":
        return 0.25
    if novelty_class == "close_variant":
        return 0.55
    if novelty_class == "novel_candidate":
        return 0.75
    return 0.35


def _developability_score(
    developability: AntibodyDevelopabilityAssessment | None,
    developability_metadata: Any,
) -> float:
    if developability is not None:
        return _clamp(developability.overall_developability_score)
    metadata = _mapping(developability_metadata)
    raw_score = metadata.get("overall_developability_score") or metadata.get("score")
    if isinstance(raw_score, int | float):
        return _clamp(float(raw_score))
    flags = _string_list(metadata.get("sequence_liability_flags")) + _string_list(
        metadata.get("cdr_liability_flags")
    )
    if flags:
        return _clamp(0.65 - min(len(flags), 5) * 0.08)
    return 0.55


def _antigen_context_score(
    antigen_context: AntigenContext | None,
    *,
    has_antigen_names: bool,
) -> float:
    if antigen_context is not None:
        base = 0.6 if antigen_context.target_symbol else 0.35
        if antigen_context.epitope_description and antigen_context.epitope_source:
            base = 1.0
        elif antigen_context.evidence_item_ids:
            base = max(base, 0.75)
        return _clamp(base * max(antigen_context.confidence, 0.4))
    return 0.65 if has_antigen_names else 0.35


def _review_readiness_score(
    *,
    warnings: Sequence[str],
    has_target: bool,
    has_evidence: bool,
    has_sequence: bool,
    validation_metadata: Any,
    novelty_known: bool,
    developability_known: bool,
) -> float:
    score = 0.2
    if has_target:
        score += 0.15
    if has_evidence:
        score += 0.15
    if has_sequence:
        score += 0.15
    if _mapping(validation_metadata).get("valid") is True:
        score += 0.1
    if novelty_known:
        score += 0.1
    if developability_known:
        score += 0.1
    score -= min(len(warnings), 4) * 0.06
    return _clamp(score)


def _candidate_uncertainty_penalty(
    candidate: BiologicCandidate,
    *,
    has_novelty: bool,
    has_developability: bool,
) -> float:
    penalty = 0.0
    if not candidate.target_symbols:
        penalty += 0.2
    if not candidate.evidence_item_ids:
        penalty += 0.25
    if not candidate.sequence_ids:
        penalty += 0.15
    if not has_novelty:
        penalty += 0.1
    if not has_developability:
        penalty += 0.1
    return _clamp(penalty)


def _generated_uncertainty_penalty(
    hypothesis: GeneratedAntibodyHypothesis,
    *,
    imported_result_linked: bool,
) -> float:
    metadata = hypothesis.metadata
    penalty = 0.2
    if not imported_result_linked:
        penalty += 0.25
    if not hypothesis.target_symbols:
        penalty += 0.15
    if not hypothesis.antigen_context_id:
        penalty += 0.1
    if not hypothesis.generated_sequence_ids:
        penalty += 0.25
    if "novelty" not in metadata:
        penalty += 0.1
    if "developability" not in metadata:
        penalty += 0.1
    return _clamp(penalty)


def _candidate_risk_penalty(
    candidate: BiologicCandidate,
    *,
    novelty: AntibodyNoveltyAssessment | None,
    developability: AntibodyDevelopabilityAssessment | None,
    developability_metadata: Any,
) -> float:
    penalty = min(len(candidate.warnings), 5) * 0.08
    if novelty is not None and novelty.novelty_class == "near_duplicate":
        penalty += 0.1
    penalty += _developability_risk_penalty(developability, developability_metadata)
    return _clamp(penalty)


def _generated_risk_penalty(hypothesis: GeneratedAntibodyHypothesis) -> float:
    metadata = hypothesis.metadata
    penalty = min(len(hypothesis.warnings), 5) * 0.08
    validation = _mapping(metadata.get("sequence_validation"))
    if validation.get("valid") is False:
        penalty += 0.45
    novelty_class = _novelty_class(_mapping(metadata.get("novelty")))
    if novelty_class == "known":
        penalty += 0.45
    elif novelty_class == "near_duplicate":
        penalty += 0.25
    penalty += _developability_risk_penalty(None, metadata.get("developability"))
    if _truthy(metadata.get("binding_claim")) or _truthy(
        metadata.get("activity_claim")
    ):
        penalty += 0.25
    return _clamp(penalty)


def _developability_risk_penalty(
    developability: AntibodyDevelopabilityAssessment | None,
    developability_metadata: Any,
) -> float:
    if developability is not None:
        risks = [
            developability.aggregation_risk,
            developability.polyreactivity_risk,
            developability.immunogenicity_risk,
            developability.viscosity_risk,
            developability.stability_risk,
            developability.expression_risk,
        ]
        flags = developability.sequence_liability_flags + developability.cdr_liability_flags
    else:
        metadata = _mapping(developability_metadata)
        risks = [
            str(metadata.get(key))
            for key in (
                "aggregation_risk",
                "polyreactivity_risk",
                "immunogenicity_risk",
                "viscosity_risk",
                "stability_risk",
                "expression_risk",
            )
            if metadata.get(key) is not None
        ]
        flags = _string_list(metadata.get("sequence_liability_flags")) + _string_list(
            metadata.get("cdr_liability_flags")
        )
    penalty = risks.count("high") * 0.12 + risks.count("medium") * 0.06
    penalty += min(len(flags), 6) * 0.05
    return _clamp(penalty)


def _has_exact_imported_result(
    hypothesis: GeneratedAntibodyHypothesis,
    exact_imported_result_ids: Iterable[str],
) -> bool:
    linked_ids = {
        hypothesis.generated_antibody_id,
        hypothesis.biologic_id,
        *hypothesis.generated_sequence_ids,
        *_metadata_string_list(hypothesis.metadata, "exact_imported_result_ids"),
    }
    return bool(
        linked_ids.intersection(
            str(identifier) for identifier in exact_imported_result_ids
        )
    )


def _metadata_novelty_assessment(value: Any) -> Mapping[str, Any] | None:
    metadata = _mapping(value)
    return metadata or None


def _novelty_class(
    novelty: AntibodyNoveltyAssessment | Mapping[str, Any] | None,
) -> str:
    if novelty is None:
        return "unknown"
    if isinstance(novelty, AntibodyNoveltyAssessment):
        return novelty.novelty_class
    return str(novelty.get("novelty_class") or "unknown")


def _metadata_string_list(metadata: Mapping[str, Any], key: str) -> list[str]:
    return _string_list(metadata.get(key))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


__all__ = [
    "GENERATED_ANTIBODY_CONFIDENCE_CAP",
    "GENERATED_ANTIBODY_IMPORTED_RESULT_CONFIDENCE_CAP",
    "BiologicsScoreComponents",
    "rank_biologic_candidates",
    "rank_generated_antibody_hypotheses",
    "score_biologic_candidate",
    "score_biologic_candidate_components",
    "score_generated_antibody_hypothesis",
]
