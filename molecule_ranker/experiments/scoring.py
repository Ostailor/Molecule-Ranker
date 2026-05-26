"""Experimental evidence score recalibration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from molecule_ranker.schemas import EvidenceItem

EXPERIMENTAL_EVIDENCE_SOURCE = "Imported experimental result"
EXPERIMENTAL_POSITIVE = "experimental_positive"
EXPERIMENTAL_NEGATIVE = "experimental_negative"
EXPERIMENTAL_INCONCLUSIVE = "experimental_inconclusive"
EXPERIMENTAL_FAILED_QC = "experimental_failed_qc"
EXPERIMENTAL_SAFETY_CONCERN = "experimental_safety_concern"


@dataclass(frozen=True)
class ExperimentalScoreModifiers:
    """Bounded score modifiers from directly imported experimental results."""

    experimental_support_score: float = 0.0
    experimental_contradiction_score: float = 0.0
    experimental_safety_penalty: float = 0.0
    experimental_confidence_modifier: float = 0.0
    score_delta: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def support_score(self) -> float:
        return self.experimental_support_score

    @property
    def contradiction_score(self) -> float:
        return self.experimental_contradiction_score

    @property
    def safety_penalty(self) -> float:
        return self.experimental_safety_penalty

    @property
    def confidence_modifier(self) -> float:
        return self.experimental_confidence_modifier


def experimental_score_modifiers(
    evidence: list[EvidenceItem],
    *,
    context_relevant: bool,
) -> ExperimentalScoreModifiers:
    """Return conservative score modifiers from imported experimental evidence.

    The helper only considers explicit experimental EvidenceItem objects. Failed QC and
    inconclusive results are preserved as uncertainty signals but never add support.
    """

    items = [item for item in evidence if is_experimental_evidence(item)]
    if not items:
        return ExperimentalScoreModifiers()

    support_values: list[float] = []
    contradiction_values: list[float] = []
    safety_values: list[float] = []
    failed_or_inconclusive_count = 0
    counts = {
        "positive": 0,
        "negative": 0,
        "inconclusive": 0,
        "failed_qc": 0,
        "safety_concern": 0,
    }

    for item in items:
        evidence_type = item.evidence_type
        qc_status = str(item.metadata.get("qc_status") or "").lower()
        quality = _experimental_item_quality(item)
        if evidence_type == EXPERIMENTAL_FAILED_QC or qc_status == "failed":
            counts["failed_qc"] += 1
            failed_or_inconclusive_count += 1
            continue
        if evidence_type == EXPERIMENTAL_INCONCLUSIVE:
            counts["inconclusive"] += 1
            failed_or_inconclusive_count += 1
            continue
        if qc_status not in {"passed", "partial"}:
            failed_or_inconclusive_count += 1
            continue
        if evidence_type == EXPERIMENTAL_POSITIVE:
            counts["positive"] += 1
            support_values.append(quality)
        elif evidence_type == EXPERIMENTAL_NEGATIVE:
            counts["negative"] += 1
            contradiction_values.append(quality)
        elif evidence_type == EXPERIMENTAL_SAFETY_CONCERN:
            counts["safety_concern"] += 1
            safety_values.append(quality)

    relevance_weight = 1.0 if context_relevant else 0.35
    support_score = _clamp(max(support_values, default=0.0) * relevance_weight)
    contradiction_score = _clamp(max(contradiction_values, default=0.0))
    safety_penalty = _clamp(max(safety_values, default=0.0))

    support_delta = min(0.075, 0.075 * support_score)
    contradiction_delta = min(0.09, 0.09 * contradiction_score)
    safety_delta = min(0.12, 0.12 * safety_penalty)
    score_delta = support_delta - contradiction_delta - safety_delta

    confidence_modifier = (
        0.08 * support_score
        - 0.10 * contradiction_score
        - 0.12 * safety_penalty
        - min(0.04, 0.015 * failed_or_inconclusive_count)
    )

    return ExperimentalScoreModifiers(
        experimental_support_score=round(support_score, 6),
        experimental_contradiction_score=round(contradiction_score, 6),
        experimental_safety_penalty=round(safety_penalty, 6),
        experimental_confidence_modifier=round(confidence_modifier, 6),
        score_delta=round(score_delta, 6),
        counts=counts,
    )


def is_experimental_evidence(item: EvidenceItem) -> bool:
    return (
        item.source == EXPERIMENTAL_EVIDENCE_SOURCE
        or item.evidence_type
        in {
            EXPERIMENTAL_POSITIVE,
            EXPERIMENTAL_NEGATIVE,
            EXPERIMENTAL_INCONCLUSIVE,
            EXPERIMENTAL_FAILED_QC,
            EXPERIMENTAL_SAFETY_CONCERN,
        }
    )


def _experimental_item_quality(item: EvidenceItem) -> float:
    confidence = _clamp(item.confidence)
    qc_status = str(item.metadata.get("qc_status") or "").lower()
    link_confidence = _optional_float(item.metadata.get("link_confidence"))
    if link_confidence is not None:
        confidence *= _clamp(link_confidence)
    if qc_status == "partial":
        confidence *= 0.6
    return _clamp(confidence)


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "EXPERIMENTAL_EVIDENCE_SOURCE",
    "EXPERIMENTAL_FAILED_QC",
    "EXPERIMENTAL_INCONCLUSIVE",
    "EXPERIMENTAL_NEGATIVE",
    "EXPERIMENTAL_POSITIVE",
    "EXPERIMENTAL_SAFETY_CONCERN",
    "ExperimentalScoreModifiers",
    "experimental_score_modifiers",
    "is_experimental_evidence",
]
