from __future__ import annotations

from molecule_ranker.hypotheses.ranking import rank_hypotheses, rank_hypothesis
from molecule_ranker.hypotheses.schemas import EvidenceGap, ResearchHypothesis


def test_strong_support_with_evidence_gap_ranks_high_for_planning() -> None:
    hypothesis = _hypothesis(
        "molecule_target",
        support_score=0.92,
        testability_score=0.86,
        novelty_score=0.55,
        uncertainty_score=0.45,
    )
    ranked = rank_hypothesis(
        hypothesis,
        evidence_gaps=[
            _gap("missing_direct_experimental_result", "high"),
            _gap("missing_selectivity_data", "medium"),
        ],
    )

    assert ranked.priority_score >= 0.65
    assert ranked.metadata["ranking"]["components"]["evidence_gap_importance"] > 0
    assert ranked.metadata["ranking"]["planning_not_proof"] is True


def test_critical_risk_lowers_rank_and_forces_review() -> None:
    hypothesis = _hypothesis(
        "developability_risk",
        support_score=0.86,
        testability_score=0.7,
        novelty_score=0.4,
        metadata={"critical_risk": True},
    )
    ranked = rank_hypothesis(
        hypothesis,
        evidence_gaps=[_gap("missing_safety_data", "critical")],
    )

    assert ranked.priority_score <= 0.5
    assert ranked.metadata["ranking"]["components"]["risk_penalty"] > 0
    assert ranked.metadata["ranking"]["requires_review_before_follow_up"] is True


def test_contradiction_high_importance_can_rank_high() -> None:
    hypothesis = _hypothesis(
        "assay_contradiction",
        support_score=0.65,
        contradiction_score=0.92,
        testability_score=0.8,
        uncertainty_score=0.7,
        contradicting_relation_ids=["rel:negative"],
    )
    ranked = rank_hypothesis(
        hypothesis,
        evidence_gaps=[_gap("contradictory_results", "high")],
    )

    assert ranked.priority_score >= 0.65
    assert ranked.metadata["ranking"]["components"]["contradiction_importance"] >= 0.9
    assert ranked.confidence < ranked.priority_score


def test_stale_hypothesis_is_penalized() -> None:
    fresh = _hypothesis("molecule_target", support_score=0.8, testability_score=0.8)
    stale = _hypothesis(
        "molecule_target",
        support_score=0.8,
        testability_score=0.8,
        status="stale",
        metadata={"is_stale": True},
    )

    fresh_ranked = rank_hypothesis(fresh)
    stale_ranked = rank_hypothesis(stale, evidence_gaps=[_gap("stale_model_prediction", "medium")])

    assert stale_ranked.priority_score < fresh_ranked.priority_score
    assert stale_ranked.metadata["ranking"]["components"]["staleness_penalty"] > 0


def test_generated_hypothesis_requires_review_before_high_priority_follow_up() -> None:
    hypothesis = _hypothesis(
        "generated_molecule",
        support_score=0.9,
        testability_score=0.9,
        novelty_score=0.8,
        uncertainty_score=0.6,
        generated_molecule_entity_ids=["generated_molecule:gen-1"],
    )
    ranked = rank_hypothesis(
        hypothesis,
        evidence_gaps=[_gap("missing_direct_experimental_result", "high")],
    )

    assert ranked.metadata["ranking"]["requires_review_before_follow_up"] is True
    assert ranked.priority_score <= 0.72
    assert ranked.status == "under_review"


def test_rank_hypotheses_sorts_descending_by_priority() -> None:
    low = _hypothesis("molecule_target", hypothesis_id="hypothesis:low", support_score=0.2)
    high = _hypothesis("molecule_target", hypothesis_id="hypothesis:high", support_score=0.9)

    ranked = rank_hypotheses([low, high])

    assert [hypothesis.hypothesis_id for hypothesis in ranked] == [
        "hypothesis:high",
        "hypothesis:low",
    ]


def _hypothesis(
    hypothesis_type: str,
    *,
    hypothesis_id: str | None = None,
    support_score: float = 0.5,
    contradiction_score: float = 0.0,
    testability_score: float = 0.5,
    novelty_score: float = 0.2,
    uncertainty_score: float = 0.3,
    status: str = "proposed",
    generated_molecule_entity_ids: list[str] | None = None,
    contradicting_relation_ids: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=hypothesis_id or f"hypothesis:{hypothesis_type}",
        hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
        title="Hypothesis: ranking",
        statement="Hypothesis for review: graph-backed context needs planning priority.",
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
        generated_molecule_entity_ids=generated_molecule_entity_ids or [],
        supporting_relation_ids=["rel:support"],
        contradicting_relation_ids=contradicting_relation_ids or [],
        source_artifact_ids=["artifact:kg"],
        support_score=support_score,
        contradiction_score=contradiction_score,
        testability_score=testability_score,
        novelty_score=novelty_score,
        uncertainty_score=uncertainty_score,
        status=status,  # type: ignore[arg-type]
        metadata=metadata or {},
    )


def _gap(gap_type: str, severity: str) -> EvidenceGap:
    return EvidenceGap(
        hypothesis_id="hypothesis:test",
        gap_type=gap_type,  # type: ignore[arg-type]
        description="Gap for ranking test.",
        severity=severity,  # type: ignore[arg-type]
        suggested_high_level_resolution="High-level review of graph-backed context.",
    )
