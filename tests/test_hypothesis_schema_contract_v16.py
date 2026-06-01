from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisGenerationRun,
    HypothesisLifecycleEvent,
    HypothesisReviewDecision,
    ResearchHypothesis,
)
from molecule_ranker.hypotheses.schemas import (
    TestableResearchQuestion as ResearchQuestionSchema,
)


def test_research_hypothesis_schema_accepts_grounded_provenance_refs() -> None:
    hypothesis = ResearchHypothesis(
        hypothesis_id="hyp-1",
        hypothesis_type="molecule_target",
        title="Review MAOB relationship",
        statement="Graph-backed relationship requires expert review.",
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:rasagiline"],
        supporting_relation_ids=["rel:target"],
        source_artifact_ids=["artifact:kg"],
        support_score=0.7,
        contradiction_score=0.1,
        novelty_score=0.3,
        testability_score=0.8,
        uncertainty_score=0.4,
        priority_score=0.6,
        confidence=0.65,
        status="proposed",
    )

    assert hypothesis.created_at.tzinfo is not None
    assert hypothesis.updated_at.tzinfo is not None
    assert hypothesis.hypothesis_type == "molecule_target"


def test_research_hypothesis_rejects_missing_provenance_bad_scores_and_naive_times() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        ResearchHypothesis(
            hypothesis_id="hyp-no-provenance",
            hypothesis_type="mechanism",
            title="No provenance",
            statement="Unsupported draft.",
        )

    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ResearchHypothesis(
            hypothesis_id="hyp-bad-score",
            hypothesis_type="mechanism",
            title="Bad score",
            statement="Score out of range.",
            supporting_relation_ids=["rel:mechanism"],
            priority_score=1.2,
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        ResearchHypothesis(
            hypothesis_id="hyp-naive",
            hypothesis_type="mechanism",
            title="Naive timestamp",
            statement="Timestamp lacks timezone.",
            supporting_relation_ids=["rel:mechanism"],
            created_at=datetime(2026, 1, 1),
        )


def test_testable_research_question_rejects_procedural_lab_details() -> None:
    question = ResearchQuestionSchema(
        question_id="rq-1",
        hypothesis_id="hyp-1",
        question_text="Would orthogonal evidence reduce ambiguity for this relationship?",
        question_type="target_engagement",
        high_level_validation_category="orthogonal binding assessment",
        linked_entity_ids=["target:MAOB"],
        required_context=["source-backed graph relation"],
        expected_observation_if_supported="Independent evidence aligns with the graph claim.",
        expected_observation_if_not_supported="Independent evidence does not align.",
        ambiguity_notes=["Assay context and claim scope remain unresolved."],
    )

    assert question.forbidden_detail_check is True

    with pytest.raises(ValidationError, match="procedural lab details"):
        ResearchQuestionSchema(
            question_id="rq-bad",
            hypothesis_id="hyp-1",
            question_text="Incubate cells for 24 hours at 37 C with 10 uM compound.",
            question_type="target_engagement",
            high_level_validation_category="cellular pathway modulation",
            expected_observation_if_supported="Signal changes.",
            expected_observation_if_not_supported="Signal does not change.",
        )


def test_falsification_gap_review_lifecycle_and_run_schemas() -> None:
    criterion = FalsificationCriterion(
        criterion_id="fc-1",
        hypothesis_id="hyp-1",
        criterion_text="A source-backed contradiction would reduce priority.",
        evidence_type_needed="assay_result",
        would_support=False,
        would_contradict=True,
        decision_impact="decrease_priority",
    )
    gap = EvidenceGap(
        gap_id="gap-1",
        hypothesis_id="hyp-1",
        gap_type="missing_direct_experimental_result",
        description="No direct imported result is linked.",
        severity="high",
        suggested_high_level_resolution=(
            "Review whether imported evidence exists for the exact entity."
        ),
    )
    decision = HypothesisReviewDecision(
        decision_id="decision-1",
        hypothesis_id="hyp-1",
        reviewer_id="reviewer-1",
        decision="needs_more_evidence",
        rationale="The graph lacks direct support.",
    )
    event = HypothesisLifecycleEvent(
        event_id="event-1",
        hypothesis_id="hyp-1",
        event_type="reviewed",
        actor="reviewer-1",
        timestamp=datetime.now(UTC),
        summary="Reviewer requested more evidence.",
    )
    run = HypothesisGenerationRun(
        generation_run_id="run-1",
        project_id=None,
        program_id="program-1",
        graph_build_id="graph-build-1",
        input_artifact_ids=["artifact:kg"],
        hypothesis_count=3,
        accepted_count=1,
        rejected_count=0,
        started_at=datetime.now(UTC),
    )

    assert criterion.decision_impact == "decrease_priority"
    assert gap.severity == "high"
    assert decision.created_at.tzinfo is not None
    assert event.timestamp.tzinfo is not None
    assert run.completed_at is None
