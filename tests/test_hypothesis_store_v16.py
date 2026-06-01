from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.hypotheses import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisGenerationRun,
    HypothesisLifecycleEvent,
    HypothesisReviewDecision,
    ResearchHypothesis,
)
from molecule_ranker.hypotheses import (
    TestableResearchQuestion as ResearchQuestionSchema,
)
from molecule_ranker.hypotheses.store import HypothesisStore


def test_hypothesis_store_crud_filters_and_lifecycle(tmp_path: Path) -> None:
    store = HypothesisStore(tmp_path / "hypotheses.sqlite")
    hypothesis = _hypothesis(
        "hyp-1",
        status="proposed",
        metadata={"project_id": "project-a", "program_id": "program-a"},
    )

    created = store.create_hypothesis(hypothesis)
    updated = store.update_hypothesis(
        "hyp-1",
        {"status": "accepted_for_planning", "priority_score": 0.83},
        actor="reviewer-1",
    )

    assert created.hypothesis_id == "hyp-1"
    assert updated.status == "accepted_for_planning"
    assert updated.priority_score == 0.83
    assert store.get_hypothesis("hyp-1").status == "accepted_for_planning"
    assert [item.hypothesis_id for item in store.list_hypotheses(project_id="project-a")] == [
        "hyp-1"
    ]
    assert [item.hypothesis_id for item in store.list_hypotheses(program_id="program-a")] == [
        "hyp-1"
    ]
    accepted = store.list_hypotheses(status="accepted_for_planning")
    assert [item.hypothesis_id for item in accepted] == ["hyp-1"]
    assert [item.hypothesis_id for item in store.list_hypotheses(type="molecule_target")] == [
        "hyp-1"
    ]

    events = store.list_lifecycle_events("hyp-1")
    assert [event.event_type for event in events] == ["created", "updated"]
    assert events[1].actor == "reviewer-1"
    assert events[1].before is not None
    assert events[1].before["status"] == "proposed"
    assert events[1].after is not None
    assert events[1].after["status"] == "accepted_for_planning"


def test_hypothesis_store_related_records_and_review_not_evidence(tmp_path: Path) -> None:
    store = HypothesisStore(tmp_path / "hypotheses.sqlite")
    hypothesis = store.create_hypothesis(_hypothesis("hyp-review"))
    question = store.add_research_question(
        ResearchQuestionSchema(
            question_id="rq-1",
            hypothesis_id=hypothesis.hypothesis_id,
            question_text="Would high-level evidence reduce ambiguity?",
            question_type="evidence_gap_closure",
            high_level_validation_category="expert review",
            expected_observation_if_supported="Evidence aligns with the graph claim.",
            expected_observation_if_not_supported="Evidence does not align.",
        )
    )
    criterion = store.add_falsification_criterion(
        FalsificationCriterion(
            criterion_id="fc-1",
            hypothesis_id=hypothesis.hypothesis_id,
            criterion_text="A graph update could lower priority.",
            evidence_type_needed="graph_update",
            would_support=False,
            would_contradict=True,
            decision_impact="decrease_priority",
        )
    )
    gap = store.add_evidence_gap(
        EvidenceGap(
            gap_id="gap-1",
            hypothesis_id=hypothesis.hypothesis_id,
            gap_type="missing_direct_experimental_result",
            description="No exact imported result is linked.",
            severity="high",
            suggested_high_level_resolution="Review source artifacts for exact linked results.",
        )
    )
    decision = store.add_review_decision(
        HypothesisReviewDecision(
            decision_id="decision-1",
            hypothesis_id=hypothesis.hypothesis_id,
            reviewer_id="reviewer-1",
            decision="needs_more_evidence",
            rationale="Review decision is planning-only.",
            confidence=0.6,
        )
    )

    reloaded = store.get_hypothesis(hypothesis.hypothesis_id)
    events = store.list_lifecycle_events(hypothesis.hypothesis_id)

    assert question.question_id == "rq-1"
    assert criterion.criterion_id == "fc-1"
    assert gap.gap_id == "gap-1"
    assert decision.decision == "needs_more_evidence"
    assert reloaded.evidence_item_ids == []
    assert reloaded.assay_result_ids == []
    assert any(event.event_type == "reviewed" for event in events)


def test_hypothesis_store_export_import_roundtrip(tmp_path: Path) -> None:
    first = HypothesisStore(tmp_path / "first.sqlite")
    first.create_hypothesis(_hypothesis("hyp-export", metadata={"project_id": "project-a"}))
    first.add_research_question(
        ResearchQuestionSchema(
            question_id="rq-export",
            hypothesis_id="hyp-export",
            question_text="Would expert review resolve this evidence gap?",
            question_type="evidence_gap_closure",
            high_level_validation_category="expert review",
            expected_observation_if_supported="Review supports planning.",
            expected_observation_if_not_supported="Review does not support planning.",
        )
    )
    first.add_falsification_criterion(
        FalsificationCriterion(
            criterion_id="fc-export",
            hypothesis_id="hyp-export",
            criterion_text="A contradiction could retire the hypothesis.",
            evidence_type_needed="graph_update",
            would_support=False,
            would_contradict=True,
            decision_impact="retire_hypothesis",
        )
    )
    first.add_evidence_gap(
        EvidenceGap(
            gap_id="gap-export",
            hypothesis_id="hyp-export",
            gap_type="missing_literature",
            description="No cited literature claim is linked.",
            severity="medium",
            suggested_high_level_resolution="Review source-backed literature claims.",
        )
    )
    first.add_review_decision(
        HypothesisReviewDecision(
            decision_id="decision-export",
            hypothesis_id="hyp-export",
            reviewer_id="reviewer-1",
            decision="hold",
            rationale="Hold for more context.",
        )
    )
    first.add_lifecycle_event(
        HypothesisLifecycleEvent(
            event_id="manual-event",
            hypothesis_id="hyp-export",
            event_type="made_stale",
            summary="Manual stale marker.",
        )
    )
    first.add_generation_run(
        HypothesisGenerationRun(
            generation_run_id="gen-run-1",
            project_id="project-a",
            graph_build_id="graph-build-1",
            input_artifact_ids=["artifact:kg"],
            hypothesis_count=1,
            accepted_count=0,
            rejected_count=0,
        )
    )
    export_path = tmp_path / "hypotheses.json"

    first.export_hypotheses_json(export_path)
    second = HypothesisStore(tmp_path / "second.sqlite")
    second.import_hypotheses_json(export_path)
    payload = json.loads(export_path.read_text())

    assert set(payload) == {
        "research_hypotheses",
        "testable_research_questions",
        "falsification_criteria",
        "evidence_gaps",
        "hypothesis_review_decisions",
        "hypothesis_lifecycle_events",
        "hypothesis_generation_runs",
    }
    assert second.get_hypothesis("hyp-export").metadata["project_id"] == "project-a"
    assert second.list_research_questions("hyp-export")[0].question_id == "rq-export"
    assert second.list_falsification_criteria("hyp-export")[0].criterion_id == "fc-export"
    assert second.list_evidence_gaps("hyp-export")[0].gap_id == "gap-export"
    assert second.list_review_decisions("hyp-export")[0].decision_id == "decision-export"
    assert any(
        event.event_id == "manual-event"
        for event in second.list_lifecycle_events("hyp-export")
    )
    assert second.list_generation_runs()[0].generation_run_id == "gen-run-1"


def _hypothesis(
    hypothesis_id: str,
    *,
    status: str = "proposed",
    metadata: dict[str, str] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=hypothesis_id,
        hypothesis_type="molecule_target",
        title="Review source-backed molecule-target hypothesis",
        statement="Graph-backed relationship needs expert review.",
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
        status=status,  # type: ignore[arg-type]
        metadata=metadata or {},
    )
