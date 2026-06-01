from __future__ import annotations

from molecule_ranker.campaigns.replanning import evaluate_replanning
from molecule_ranker.campaigns.schemas import (
    CampaignBudget,
    CampaignObjective,
    CampaignPlan,
    CampaignWorkPackage,
)


def test_positive_result_triggers_replan_and_reprioritizes_related_hypothesis() -> None:
    report = evaluate_replanning(
        _plan(),
        new_events=[
            {
                "event_id": "event-positive",
                "event_type": "result_imported",
                "result_interpretation": "positive",
                "candidate_id": "candidate-1",
                "hypothesis_id": "hypothesis-1",
            }
        ],
    )

    assert [trigger.trigger_type for trigger in report.triggers] == ["new_positive_result"]
    assert report.updated_plan.metadata["reprioritized_hypothesis_ids"] == ["hypothesis-1"]
    assert report.updated_plan.metadata["codex_triggered_execution"] is False


def test_safety_result_triggers_pause_and_expert_review() -> None:
    report = evaluate_replanning(
        _plan(),
        new_events=[
            {
                "event_id": "event-safety",
                "event_type": "result_imported",
                "result_interpretation": "safety_concern",
                "candidate_id": "candidate-1",
                "hypothesis_id": "hypothesis-1",
            }
        ],
    )

    trigger = report.triggers[0]
    assert trigger.trigger_type == "safety_concern"
    assert trigger.severity == "critical"
    assert trigger.recommended_action == "expert_review"
    assert report.updated_plan.metadata["recommended_campaign_status"] == "paused"
    assert "safety_review_required" in report.updated_plan.metadata["required_reviews"]


def test_failed_qc_triggers_more_data_review_not_false_negative() -> None:
    report = evaluate_replanning(
        _plan(),
        new_events=[
            {
                "event_id": "event-qc",
                "event_type": "result_imported",
                "result_interpretation": "failed_qc",
                "candidate_id": "candidate-1",
                "hypothesis_id": "hypothesis-1",
            }
        ],
    )

    trigger = report.triggers[0]
    assert trigger.trigger_type == "failed_qc"
    assert trigger.recommended_action == "expert_review"
    assert "false_negative_conclusion" not in report.rationale.lower()
    assert "more-data/review" in report.rationale


def test_budget_exceeded_trigger() -> None:
    report = evaluate_replanning(
        _plan(
            budget_summary={
                "within_limits": False,
                "exceeded_dimensions": ["assay_slots"],
            }
        ),
        new_events=[{"event_id": "event-budget", "event_type": "budget_changed"}],
    )

    assert report.triggers[0].trigger_type == "budget_exceeded"
    assert report.triggers[0].recommended_action == "pause"
    assert report.updated_plan.metadata["recommended_campaign_status"] == "paused"


def test_stale_hypothesis_trigger() -> None:
    report = evaluate_replanning(
        _plan(),
        new_events=[
            {
                "event_id": "event-stale",
                "event_type": "hypothesis_status_change",
                "hypothesis_id": "hypothesis-1",
                "status": "stale",
            }
        ],
    )

    assert report.triggers[0].trigger_type == "hypothesis_retired"
    assert report.triggers[0].severity == "medium"
    assert report.updated_plan.metadata["deprioritized_hypothesis_ids"] == ["hypothesis-1"]


def _plan(
    *,
    budget_summary: dict[str, object] | None = None,
) -> CampaignPlan:
    objective = CampaignObjective(
        objective_id="objective-1",
        campaign_id="campaign-1",
        name="Objective",
        objective_type="validate_hypothesis",
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-1"],
        success_criteria=["Review deterministic evidence."],
        stop_criteria=["Stop if review rejects the hypothesis."],
        priority_weight=0.7,
        metadata={"linked_hypothesis_ids": ["hypothesis-1"]},
    )
    package = CampaignWorkPackage(
        work_package_id="pkg-1",
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type="expert_review",
        title="Review package",
        description="High-level campaign review package.",
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="planning review",
        dependencies=[],
        required_approvals=[],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=1.0,
        estimated_compute_units=None,
        estimated_assay_slots=None,
        status="proposed",
        blocking_reasons=[],
        warnings=[],
        metadata={},
    )
    return CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id="campaign-1",
        objectives=[objective],
        work_packages=[package],
        budget=CampaignBudget(
            budget_id="budget-1",
            campaign_id="campaign-1",
            max_total_cost=None,
            cost_units=None,
            max_assay_slots=None,
            max_review_hours=None,
            max_compute_units=None,
            max_codex_tasks=None,
            max_external_sync_jobs=None,
            reserved_budget={},
            metadata={},
        ),
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.5,
        risk_summary={},
        uncertainty_summary={},
        budget_summary=budget_summary or {"within_limits": True, "exceeded_dimensions": []},
        recommended_sequence=["pkg-1"],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={},
    )
