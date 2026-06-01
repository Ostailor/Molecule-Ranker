from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignBudget,
    CampaignExecutionEvent,
    CampaignMemo,
    CampaignObjective,
    CampaignPlan,
    CampaignWorkPackage,
    ReplanTrigger,
)


def test_campaign_schema_accepts_v17_management_artifacts() -> None:
    campaign = _campaign()
    objective = _objective(campaign.campaign_id)
    work_package = _work_package(campaign.campaign_id, [objective.objective_id])
    budget = _budget(campaign.campaign_id)
    plan = CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id=campaign.campaign_id,
        objectives=[objective],
        work_packages=[work_package],
        budget=budget,
        stage_gates=[{"gate_id": "gate-1", "decision": "pending"}],
        dependency_graph={"work-package-1": []},
        expected_learning_value=0.72,
        risk_summary={"blocking_risks": []},
        uncertainty_summary={"dominant_uncertainty": "candidate ranking"},
        budget_summary={"within_budget": True},
        recommended_sequence=[work_package.work_package_id],
        replan_triggers=["contradiction_detected"],
        human_approval_required=True,
        warnings=["Campaign plan is a management artifact, not a protocol."],
        metadata={"source_artifact_ids": ["artifact:campaign-inputs"]},
    )
    event = CampaignExecutionEvent(
        event_id="event-1",
        campaign_id=campaign.campaign_id,
        work_package_id=work_package.work_package_id,
        event_type="created",
        actor="planner",
        summary="Campaign work package created from deterministic artifacts.",
        before=None,
        after={"status": "proposed"},
        metadata={},
    )
    trigger = ReplanTrigger(
        trigger_id="trigger-1",
        campaign_id=campaign.campaign_id,
        trigger_type="contradiction_detected",
        severity="high",
        description="Graph contradiction changed campaign assumptions.",
        linked_entity_ids=["relation:1"],
        recommended_action="replan",
        metadata={},
    )
    memo = CampaignMemo(
        memo_id="memo-1",
        campaign_id=campaign.campaign_id,
        title="Campaign memo",
        executive_summary="Summarizes deterministic campaign planning artifacts.",
        objectives_summary="One objective remains under review.",
        selected_work_packages=[work_package.work_package_id],
        budget_summary="Within supplied budget constraints.",
        key_tradeoffs=["Review capacity versus learning value."],
        risks=["No direct experimental evidence for generated candidates."],
        uncertainty_notes=["Ranking uncertainty remains reviewable."],
        replan_triggers=[trigger.trigger_id],
        approvals_required=["campaign_advancement_approval"],
        limitations=["No lab protocols or synthesis instructions."],
        metadata={},
    )

    assert campaign.created_at.tzinfo is not None
    assert campaign.updated_at.tzinfo is not None
    assert plan.created_at.tzinfo is not None
    assert event.timestamp.tzinfo is not None
    assert memo.created_at.tzinfo is not None
    assert plan.expected_learning_value == 0.72
    assert work_package.status == "proposed"


def test_campaign_schemas_reject_invalid_enum_values() -> None:
    with pytest.raises(ValidationError):
        _campaign(status="running")

    with pytest.raises(ValidationError):
        _objective("campaign-1", objective_type="invent_metric")

    with pytest.raises(ValidationError):
        _work_package("campaign-1", ["objective-1"], package_type="wet_lab_protocol")

    with pytest.raises(ValidationError):
        CampaignExecutionEvent.model_validate(
            {
                "event_id": "event-1",
                "campaign_id": "campaign-1",
                "work_package_id": None,
                "event_type": "invented_event",
                "actor": None,
                "summary": "Bad event.",
                "before": None,
                "after": None,
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError):
        ReplanTrigger.model_validate(
            {
                "trigger_id": "trigger-1",
                "campaign_id": "campaign-1",
                "trigger_type": "portfolio_changed",
                "severity": "urgent",
                "description": "Bad severity.",
                "linked_entity_ids": [],
                "recommended_action": "replan",
                "metadata": {},
            }
        )


def test_campaign_scores_weights_and_estimates_are_bounded() -> None:
    with pytest.raises(ValidationError):
        _objective("campaign-1", priority_weight=1.1)

    with pytest.raises(ValidationError):
        CampaignPlan(
            campaign_plan_id="plan-1",
            campaign_id="campaign-1",
            objectives=[_objective("campaign-1")],
            work_packages=[_work_package("campaign-1", ["objective-1"])],
            budget=_budget("campaign-1"),
            stage_gates=[],
            dependency_graph={},
            expected_learning_value=-0.1,
            risk_summary={},
            uncertainty_summary={},
            budget_summary={},
            recommended_sequence=[],
            replan_triggers=[],
            human_approval_required=False,
            warnings=[],
            metadata={},
        )

    with pytest.raises(ValidationError):
        _work_package("campaign-1", ["objective-1"], estimated_assay_slots=-1)

    with pytest.raises(ValidationError):
        _budget("campaign-1", max_total_cost=-1.0)


def test_campaign_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _campaign(created_at=datetime(2026, 1, 1))

    with pytest.raises(ValidationError, match="timezone-aware"):
        CampaignExecutionEvent(
            event_id="event-1",
            campaign_id="campaign-1",
            work_package_id=None,
            event_type="created",
            actor=None,
            timestamp=datetime(2026, 1, 1),
            summary="Naive timestamp.",
            before=None,
            after=None,
            metadata={},
        )


def test_campaign_work_package_rejects_procedural_lab_details() -> None:
    with pytest.raises(ValidationError, match="procedural lab details"):
        _work_package(
            "campaign-1",
            ["objective-1"],
            description="Incubate with a reagent at 37 C.",
        )

    with pytest.raises(ValidationError, match="procedural lab details"):
        _work_package(
            "campaign-1",
            ["objective-1"],
            high_level_activity_category="step-by-step assay protocol",
        )


def _campaign(**overrides: object) -> Campaign:
    data = {
        "campaign_id": "campaign-1",
        "project_id": "project-1",
        "program_id": "program-1",
        "name": "V1.7 campaign",
        "description": "High-level campaign management artifact.",
        "disease_focus": ["Parkinson disease"],
        "target_focus": ["MAOB"],
        "hypothesis_ids": ["hypothesis-1"],
        "portfolio_selection_ids": ["selection-1"],
        "status": "draft",
        "metadata": {},
    }
    data.update(overrides)
    return Campaign(**data)


def _objective(
    campaign_id: str,
    *,
    objective_type: str = "validate_hypothesis",
    priority_weight: float = 0.7,
) -> CampaignObjective:
    return CampaignObjective(
        objective_id="objective-1",
        campaign_id=campaign_id,
        name="Validate hypothesis",
        objective_type=objective_type,  # type: ignore[arg-type]
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-1"],
        success_criteria=["Source-backed evidence supports continuing review."],
        stop_criteria=["Imported evidence contradicts the hypothesis."],
        priority_weight=priority_weight,
        metadata={},
    )


def _work_package(
    campaign_id: str,
    objective_ids: list[str],
    *,
    package_type: str = "expert_review",
    description: str = "Review deterministic artifacts at a high level.",
    high_level_activity_category: str = "expert evidence review",
    estimated_assay_slots: int | None = None,
) -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id="work-package-1",
        campaign_id=campaign_id,
        objective_ids=objective_ids,
        package_type=package_type,  # type: ignore[arg-type]
        title="Expert review work package",
        description=description,
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category=high_level_activity_category,
        dependencies=[],
        required_approvals=["campaign_advancement_approval"],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=1.0,
        estimated_compute_units=None,
        estimated_assay_slots=estimated_assay_slots,
        status="proposed",
        blocking_reasons=[],
        warnings=["No procedural instructions are included."],
        metadata={},
    )


def _budget(campaign_id: str, **overrides: object) -> CampaignBudget:
    data = {
        "budget_id": "budget-1",
        "campaign_id": campaign_id,
        "max_total_cost": 5000.0,
        "cost_units": "USD",
        "max_assay_slots": 4,
        "max_review_hours": 12.0,
        "max_compute_units": 20.0,
        "max_codex_tasks": 2,
        "max_external_sync_jobs": 1,
        "reserved_budget": {"review": 1000.0},
        "metadata": {},
    }
    data.update(overrides)
    return CampaignBudget(**data)
