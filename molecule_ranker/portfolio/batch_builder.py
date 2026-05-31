from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from .schemas import PortfolioBatch, PortfolioBatchType, PortfolioCandidate, PortfolioSelection

REVIEW_APPROVED_STATUSES = {
    "approved",
    "expert_approved",
    "reviewed",
    "triaged",
    "ready",
}

HIGH_RISK_TERMS = {
    "critical",
    "severe",
    "blocking",
    "toxicophore",
    "high_risk",
    "high-risk",
}

DEFAULT_CATEGORIES: dict[PortfolioBatchType, list[str]] = {
    "expert_review_batch": [
        "evidence review",
        "risk review",
        "uncertainty review",
    ],
    "assay_triage_batch": [
        "target-engagement category",
        "cell-context category",
        "selectivity category",
        "orthogonal-confirmation category",
    ],
    "active_learning_batch": [
        "uncertainty resolution",
        "diversity sampling",
        "model-domain clarification",
    ],
    "structure_review_batch": [
        "structure-context review",
        "pose-confidence review",
        "model-context review",
    ],
    "developability_review_batch": [
        "admet risk review",
        "liability review",
        "readiness review",
    ],
}

DEFAULT_PURPOSE: dict[PortfolioBatchType, str] = {
    "expert_review_batch": "Prioritize candidates for expert triage.",
    "assay_triage_batch": "Prioritize candidates for high-level assay category planning.",
    "active_learning_batch": "Prioritize candidates that may improve portfolio learning.",
    "structure_review_batch": "Prioritize candidates needing structure-context review.",
    "developability_review_batch": "Prioritize candidates needing developability review.",
}


def build_portfolio_batch(
    candidates: Sequence[PortfolioCandidate],
    *,
    batch_type: PortfolioBatchType,
    selection: PortfolioSelection | None = None,
    candidate_ids: Sequence[str] | None = None,
    max_candidates: int | None = None,
    high_level_followup_categories: Sequence[str] | None = None,
    purpose: str | None = None,
    require_generated_review_approval: bool = True,
    exclude_high_risk: bool = True,
    batch_id: str | None = None,
) -> PortfolioBatch:
    """Build a deterministic high-level review or planning batch."""

    candidate_pool = _filter_candidate_scope(candidates, selection, candidate_ids)
    sorted_candidates = sorted(
        candidate_pool,
        key=lambda candidate: (
            -_batch_priority(candidate, batch_type),
            candidate.portfolio_candidate_id,
        ),
    )
    selected: list[PortfolioCandidate] = []
    excluded: dict[str, str] = {}
    required_approvals: set[str] = set()

    for candidate in sorted_candidates:
        exclusion = _exclusion_reason(
            candidate,
            batch_type=batch_type,
            require_generated_review_approval=require_generated_review_approval,
            exclude_high_risk=exclude_high_risk,
        )
        if exclusion is not None:
            excluded[candidate.portfolio_candidate_id] = exclusion
            if exclusion == "generated_review_approval_required":
                required_approvals.add("generated_candidate_review_approval")
            continue
        selected.append(candidate)
        if max_candidates is not None and len(selected) >= max_candidates:
            break

    warnings = [
        "Batch is a high-level research planning artifact, not an execution instruction.",
        "Follow-up categories are intentionally broad and omit operating details.",
    ]
    if excluded:
        warnings.append("Some candidates were excluded by deterministic review or risk rules.")
    if batch_type == "assay_triage_batch" and require_generated_review_approval:
        warnings.append(
            "Generated hypotheses require review approval before assay triage batching."
        )

    metadata: dict[str, Any] = {
        "deterministic_batch": True,
        "excluded_candidate_ids": sorted(excluded),
        "excluded_reasons": excluded,
        "candidate_rationales": {
            candidate.portfolio_candidate_id: _candidate_rationale(candidate, batch_type)
            for candidate in selected
        },
        "candidate_risks": {
            candidate.portfolio_candidate_id: {
                "risk_flags": list(candidate.risk_flags),
                "blocking_risks": list(candidate.blocking_risks),
            }
            for candidate in selected
        },
        "candidate_uncertainty": {
            candidate.portfolio_candidate_id: candidate.uncertainty_score for candidate in selected
        },
        "excluded_high_risk_by_default": exclude_high_risk,
        "generated_review_required_by_default": require_generated_review_approval,
    }
    selected_ids = [candidate.portfolio_candidate_id for candidate in selected]
    return PortfolioBatch(
        batch_id=batch_id or _stable_batch_id(batch_type, selected_ids, excluded),
        batch_type=batch_type,
        candidate_ids=selected_ids,
        purpose=purpose or DEFAULT_PURPOSE[batch_type],
        high_level_followup_categories=list(
            high_level_followup_categories or DEFAULT_CATEGORIES[batch_type]
        ),
        rationale=_batch_rationale(batch_type, selected, excluded),
        required_approvals=sorted(required_approvals),
        warnings=warnings,
        metadata=metadata,
    )


def build_expert_review_batch(
    candidates: Sequence[PortfolioCandidate],
    **kwargs: Any,
) -> PortfolioBatch:
    return build_portfolio_batch(candidates, batch_type="expert_review_batch", **kwargs)


def build_assay_triage_batch(
    candidates: Sequence[PortfolioCandidate],
    **kwargs: Any,
) -> PortfolioBatch:
    return build_portfolio_batch(candidates, batch_type="assay_triage_batch", **kwargs)


def build_active_learning_batch(
    candidates: Sequence[PortfolioCandidate],
    **kwargs: Any,
) -> PortfolioBatch:
    return build_portfolio_batch(candidates, batch_type="active_learning_batch", **kwargs)


def build_structure_review_batch(
    candidates: Sequence[PortfolioCandidate],
    **kwargs: Any,
) -> PortfolioBatch:
    return build_portfolio_batch(candidates, batch_type="structure_review_batch", **kwargs)


def build_developability_review_batch(
    candidates: Sequence[PortfolioCandidate],
    **kwargs: Any,
) -> PortfolioBatch:
    return build_portfolio_batch(candidates, batch_type="developability_review_batch", **kwargs)


def _filter_candidate_scope(
    candidates: Sequence[PortfolioCandidate],
    selection: PortfolioSelection | None,
    candidate_ids: Sequence[str] | None,
) -> list[PortfolioCandidate]:
    allowed: set[str] | None = set(candidate_ids) if candidate_ids is not None else None
    if selection is not None and allowed is None:
        allowed = set(selection.selected_candidate_ids)
    if allowed is None:
        return list(candidates)
    return [candidate for candidate in candidates if candidate.portfolio_candidate_id in allowed]


def _exclusion_reason(
    candidate: PortfolioCandidate,
    *,
    batch_type: PortfolioBatchType,
    require_generated_review_approval: bool,
    exclude_high_risk: bool,
) -> str | None:
    if exclude_high_risk and _has_high_risk(candidate):
        return "high_risk_excluded"
    if (
        batch_type == "assay_triage_batch"
        and require_generated_review_approval
        and candidate.origin == "generated"
        and not _review_approved(candidate)
    ):
        return "generated_review_approval_required"
    return None


def _batch_priority(candidate: PortfolioCandidate, batch_type: PortfolioBatchType) -> float:
    if batch_type == "expert_review_batch":
        return _average(
            [
                candidate.uncertainty_score,
                candidate.novelty_score,
                1.0 if candidate.origin == "generated" else 0.4,
                0.7 if candidate.risk_flags else 0.2,
            ]
        )
    if batch_type == "assay_triage_batch":
        return _average(
            [
                candidate.experiment_readiness_score,
                candidate.evidence_score,
                candidate.experimental_support_score,
                1.0 if _review_approved(candidate) else 0.3,
            ]
        )
    if batch_type == "active_learning_batch":
        return _average(
            [
                candidate.uncertainty_score,
                candidate.novelty_score,
                1.0 if candidate.diversity_features else 0.4,
                1.0 - (candidate.evidence_score or 0.0),
            ]
        )
    if batch_type == "structure_review_batch":
        return _average(
            [
                candidate.structure_score,
                candidate.predictive_model_score,
                candidate.uncertainty_score,
            ]
        )
    return _average(
        [
            1.0 - (candidate.developability_score or 0.5),
            0.7 if candidate.risk_flags else 0.2,
            candidate.experiment_readiness_score,
        ]
    )


def _candidate_rationale(
    candidate: PortfolioCandidate,
    batch_type: PortfolioBatchType,
) -> str:
    signals: list[str] = []
    if candidate.uncertainty_score is not None:
        signals.append(f"uncertainty {candidate.uncertainty_score:.3f}")
    if candidate.experiment_readiness_score is not None:
        signals.append(f"readiness {candidate.experiment_readiness_score:.3f}")
    if candidate.developability_score is not None:
        signals.append(f"developability {candidate.developability_score:.3f}")
    if candidate.risk_flags:
        signals.append("risk flags present")
    if candidate.origin == "generated":
        signals.append("generated hypothesis")
    signal_text = ", ".join(signals) or "available portfolio signals"
    return f"Included in {batch_type} based on {signal_text}."


def _batch_rationale(
    batch_type: PortfolioBatchType,
    selected: Sequence[PortfolioCandidate],
    excluded: dict[str, str],
) -> str:
    if not selected:
        return (
            f"No candidates were included in {batch_type}; deterministic filters excluded "
            f"{len(excluded)} candidate(s)."
        )
    return (
        f"Included {len(selected)} candidate(s) in {batch_type} using deterministic priority "
        f"signals, risk filters, uncertainty, and review-gate requirements; excluded "
        f"{len(excluded)} candidate(s)."
    )


def _review_approved(candidate: PortfolioCandidate) -> bool:
    return (candidate.review_status or "").strip().lower() in REVIEW_APPROVED_STATUSES


def _has_high_risk(candidate: PortfolioCandidate) -> bool:
    if candidate.blocking_risks:
        return True
    risk_text = " ".join(candidate.risk_flags).lower()
    return any(term in risk_text for term in HIGH_RISK_TERMS)


def _average(values: Sequence[float | None]) -> float:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return 0.0
    return sum(numeric) / len(numeric)


def _stable_batch_id(
    batch_type: PortfolioBatchType,
    selected_ids: Sequence[str],
    excluded: dict[str, str],
) -> str:
    key = "|".join([batch_type, *selected_ids, *sorted(excluded)])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{batch_type}-{digest}"


__all__ = [
    "build_active_learning_batch",
    "build_assay_triage_batch",
    "build_developability_review_batch",
    "build_expert_review_batch",
    "build_portfolio_batch",
    "build_structure_review_batch",
]
