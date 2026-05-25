from __future__ import annotations

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


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
