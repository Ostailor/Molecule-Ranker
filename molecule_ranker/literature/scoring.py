from __future__ import annotations

from collections import Counter
from typing import Any

from molecule_ranker.literature.schemas import LiteratureEvidenceBundle


def literature_quality_score(bundle: LiteratureEvidenceBundle) -> float:
    """Score literature bundle quality from real papers and extracted claims."""

    if not bundle.papers or not bundle.claims:
        return 0.0
    claim_confidence = sum(claim.confidence for claim in bundle.claims) / len(bundle.claims)
    clinical_bonus = 0.15 if any(paper.is_clinical for paper in bundle.papers) else 0.0
    review_bonus = 0.05 if any(paper.is_review for paper in bundle.papers) else 0.0
    retraction_penalty = 0.3 if any(paper.is_retracted for paper in bundle.papers) else 0.0
    provenance_score = min(
        sum(1 for paper in bundle.papers if paper.pmid or paper.doi or paper.openalex_id)
        / len(bundle.papers),
        1.0,
    )
    return _clamp(
        0.65 * claim_confidence
        + 0.20 * provenance_score
        + clinical_bonus
        + review_bonus
        - retraction_penalty
    )


def aggregate_literature_claims(bundle: LiteratureEvidenceBundle) -> dict[str, Any]:
    claim_type_counts = Counter(claim.claim_type for claim in bundle.claims)
    direction_counts = Counter(claim.direction for claim in bundle.claims)
    paper_ids = {claim.paper_id for claim in bundle.claims}
    return {
        "claim_count": len(bundle.claims),
        "paper_count": len(bundle.papers),
        "claiming_paper_count": len(paper_ids),
        "claim_type_counts": dict(sorted(claim_type_counts.items())),
        "direction_counts": dict(sorted(direction_counts.items())),
        "clinical_paper_count": sum(1 for paper in bundle.papers if paper.is_clinical),
        "review_paper_count": sum(1 for paper in bundle.papers if paper.is_review),
        "retracted_paper_count": sum(1 for paper in bundle.papers if paper.is_retracted),
    }


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
