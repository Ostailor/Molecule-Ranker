from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, cast

from pydantic import BaseModel, Field

from molecule_ranker.hypotheses.engine import rank_hypotheses as rank_legacy_hypotheses
from molecule_ranker.hypotheses.schemas import EvidenceGap, Hypothesis, ResearchHypothesis

__all__ = [
    "HypothesisRanker",
    "RankingComponents",
    "rank_hypothesis",
    "rank_hypotheses",
    "rank_research_hypotheses",
]


class RankingComponents(BaseModel):
    support_score: float = Field(ge=0.0, le=1.0)
    contradiction_importance: float = Field(ge=0.0, le=1.0)
    testability_score: float = Field(ge=0.0, le=1.0)
    evidence_gap_importance: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    portfolio_relevance: float = Field(ge=0.0, le=1.0)
    active_learning_value: float = Field(ge=0.0, le=1.0)
    expert_review_interest: float = Field(ge=0.0, le=1.0)
    risk_penalty: float = Field(ge=0.0, le=1.0)
    staleness_penalty: float = Field(ge=0.0, le=1.0)
    uncertainty_value: float = Field(ge=0.0, le=1.0)


class HypothesisRanker:
    """Rank hypotheses for research planning, not proof."""

    def rank(
        self,
        hypothesis: ResearchHypothesis,
        *,
        evidence_gaps: Iterable[EvidenceGap] | None = None,
    ) -> ResearchHypothesis:
        gap_list = list(evidence_gaps or [])
        components = self._components(hypothesis, gap_list)
        raw_priority = _priority_from_components(components)
        priority = _apply_priority_rules(hypothesis, components, raw_priority)
        confidence = _confidence_from_components(hypothesis, components)
        requires_review = _requires_review_before_follow_up(hypothesis, components, gap_list)
        metadata = {
            **hypothesis.metadata,
            "ranking": {
                "components": components.model_dump(mode="json"),
                "raw_priority_score": round(raw_priority, 6),
                "planning_not_proof": True,
                "requires_review_before_follow_up": requires_review,
                "high_uncertainty_can_increase_learning_value": True,
                "contradictions_can_rank_high_for_resolution": True,
            },
        }
        status = (
            "under_review"
            if requires_review and hypothesis.status == "proposed"
            else hypothesis.status
        )
        return hypothesis.model_copy(
            update={
                "priority_score": priority,
                "confidence": confidence,
                "status": status,
                "metadata": metadata,
            }
        )

    def rank_many(
        self,
        hypotheses: Iterable[ResearchHypothesis],
        *,
        evidence_gaps_by_hypothesis: dict[str, list[EvidenceGap]] | None = None,
    ) -> list[ResearchHypothesis]:
        gap_map = evidence_gaps_by_hypothesis or {}
        ranked = [
            self.rank(
                hypothesis,
                evidence_gaps=gap_map.get(hypothesis.hypothesis_id, []),
            )
            for hypothesis in hypotheses
        ]
        return sorted(ranked, key=lambda item: (-item.priority_score, item.hypothesis_id))

    def _components(
        self,
        hypothesis: ResearchHypothesis,
        evidence_gaps: list[EvidenceGap],
    ) -> RankingComponents:
        contradiction_importance = max(
            hypothesis.contradiction_score,
            0.92 if hypothesis.contradicting_relation_ids else 0.0,
            _gap_importance(
                [
                    gap
                    for gap in evidence_gaps
                    if gap.gap_type == "contradictory_results"
                ]
            ),
        )
        uncertainty_value = _bounded(hypothesis.uncertainty_score)
        active_learning_value = max(
            _metadata_float(hypothesis.metadata, "active_learning_value"),
            uncertainty_value * 0.85,
            0.7 if hypothesis.hypothesis_type == "active_learning" else 0.0,
        )
        return RankingComponents(
            support_score=_bounded(hypothesis.support_score),
            contradiction_importance=_bounded(contradiction_importance),
            testability_score=_bounded(hypothesis.testability_score),
            evidence_gap_importance=_gap_importance(evidence_gaps),
            novelty_score=_bounded(hypothesis.novelty_score),
            portfolio_relevance=_portfolio_relevance(hypothesis),
            active_learning_value=_bounded(active_learning_value),
            expert_review_interest=_expert_review_interest(hypothesis, evidence_gaps),
            risk_penalty=_risk_penalty(hypothesis, evidence_gaps),
            staleness_penalty=_staleness_penalty(hypothesis, evidence_gaps),
            uncertainty_value=uncertainty_value,
        )


def rank_hypothesis(
    hypothesis: ResearchHypothesis,
    *,
    evidence_gaps: Iterable[EvidenceGap] | None = None,
) -> ResearchHypothesis:
    return HypothesisRanker().rank(hypothesis, evidence_gaps=evidence_gaps)


def rank_research_hypotheses(
    hypotheses: Iterable[ResearchHypothesis],
    *,
    evidence_gaps_by_hypothesis: dict[str, list[EvidenceGap]] | None = None,
) -> list[ResearchHypothesis]:
    return HypothesisRanker().rank_many(
        hypotheses,
        evidence_gaps_by_hypothesis=evidence_gaps_by_hypothesis,
    )


def rank_hypotheses(
    hypotheses: Sequence[ResearchHypothesis] | Sequence[Hypothesis],
    *,
    evidence_gaps_by_hypothesis: dict[str, list[EvidenceGap]] | None = None,
) -> list[ResearchHypothesis] | list[Hypothesis]:
    if not hypotheses:
        return []
    first = hypotheses[0]
    if isinstance(first, ResearchHypothesis):
        return rank_research_hypotheses(
            cast(Sequence[ResearchHypothesis], hypotheses),
            evidence_gaps_by_hypothesis=evidence_gaps_by_hypothesis,
        )
    return rank_legacy_hypotheses(list(cast(Sequence[Hypothesis], hypotheses)))


def _priority_from_components(components: RankingComponents) -> float:
    positive = (
        components.support_score * 0.28
        + components.contradiction_importance * 0.18
        + components.testability_score * 0.16
        + components.evidence_gap_importance * 0.16
        + components.novelty_score * 0.08
        + components.portfolio_relevance * 0.08
        + components.active_learning_value * 0.08
        + components.expert_review_interest * 0.08
        + components.uncertainty_value * 0.08
    )
    penalties = components.risk_penalty * 0.55 + components.staleness_penalty * 0.25
    return _bounded(positive - penalties)


def _apply_priority_rules(
    hypothesis: ResearchHypothesis,
    components: RankingComponents,
    raw_priority: float,
) -> float:
    priority = raw_priority
    if components.risk_penalty >= 0.85:
        priority = min(priority, 0.5)
    if (
        hypothesis.hypothesis_type == "generated_molecule"
        and not hypothesis.review_decision_ids
    ):
        priority = min(priority, 0.72)
    return round(_bounded(priority), 6)


def _confidence_from_components(
    hypothesis: ResearchHypothesis,
    components: RankingComponents,
) -> float:
    confidence = (
        hypothesis.confidence
        if hypothesis.confidence > 0
        else components.support_score * 0.75 + components.testability_score * 0.25
    )
    confidence -= components.contradiction_importance * 0.2
    confidence -= components.uncertainty_value * 0.22
    confidence -= components.risk_penalty * 0.2
    confidence -= components.staleness_penalty * 0.2
    return round(_bounded(confidence), 6)


def _requires_review_before_follow_up(
    hypothesis: ResearchHypothesis,
    components: RankingComponents,
    evidence_gaps: list[EvidenceGap],
) -> bool:
    if hypothesis.hypothesis_type == "generated_molecule" and not hypothesis.review_decision_ids:
        return True
    if components.risk_penalty >= 0.55:
        return True
    if any(gap.severity == "critical" for gap in evidence_gaps):
        return True
    return False


def _gap_importance(gaps: Iterable[EvidenceGap]) -> float:
    severity_values = {
        "critical": 0.95,
        "high": 0.72,
        "medium": 0.42,
        "low": 0.18,
    }
    return max((severity_values[gap.severity] for gap in gaps), default=0.0)


def _portfolio_relevance(hypothesis: ResearchHypothesis) -> float:
    return _bounded(
        max(
            _metadata_float(hypothesis.metadata, "portfolio_relevance"),
            0.78 if hypothesis.hypothesis_type == "portfolio_decision" else 0.0,
        )
    )


def _expert_review_interest(
    hypothesis: ResearchHypothesis,
    evidence_gaps: list[EvidenceGap],
) -> float:
    if hypothesis.review_decision_ids:
        return 0.25
    value = 0.25
    if hypothesis.contradicting_relation_ids:
        value = max(value, 0.9)
    if any(gap.severity in {"critical", "high"} for gap in evidence_gaps):
        value = max(value, 0.75)
    if hypothesis.generated_molecule_entity_ids:
        value = max(value, 0.65)
    return value


def _risk_penalty(
    hypothesis: ResearchHypothesis,
    evidence_gaps: list[EvidenceGap],
) -> float:
    if _metadata_bool(hypothesis.metadata, "critical_risk"):
        return 0.95
    if any(
        gap.severity == "critical"
        and gap.gap_type in {
            "missing_safety_data",
            "missing_developability_data",
            "unreviewed_generated_molecule",
        }
        for gap in evidence_gaps
    ):
        return 0.95
    if hypothesis.hypothesis_type in {"safety_risk", "developability_risk"}:
        return 0.5
    if any(
        gap.severity == "high"
        and gap.gap_type in {"missing_safety_data", "missing_developability_data"}
        for gap in evidence_gaps
    ):
        return 0.45
    return 0.0


def _staleness_penalty(
    hypothesis: ResearchHypothesis,
    evidence_gaps: list[EvidenceGap],
) -> float:
    if hypothesis.status == "stale" or _metadata_bool(hypothesis.metadata, "is_stale"):
        return 0.55
    if any(gap.gap_type == "stale_model_prediction" for gap in evidence_gaps):
        return 0.45
    return 0.0


def _metadata_float(metadata: dict[str, Any], key: str) -> float:
    value = metadata.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return _bounded(float(value))
    if isinstance(value, str):
        try:
            return _bounded(float(value))
        except ValueError:
            return 0.0
    return 0.0


def _metadata_bool(metadata: dict[str, Any], key: str) -> bool:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return False


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))
