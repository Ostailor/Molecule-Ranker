from __future__ import annotations

import json
from pathlib import Path

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
from molecule_ranker.campaigns.stage_gates import build_campaign_approval_gate
from molecule_ranker.campaigns.store import CampaignStore


def test_campaign_store_crud_and_status_audit(tmp_path: Path) -> None:
    store = CampaignStore(tmp_path / "campaigns.sqlite")
    campaign = store.create_campaign(_campaign())

    assert store.get_campaign(campaign.campaign_id).name == "Campaign"
    assert [item.campaign_id for item in store.list_campaigns()] == ["campaign-1"]

    updated = store.update_campaign_status(
        "campaign-1",
        "under_review",
        actor="reviewer-1",
        rationale="Ready for review.",
    )
    events = store.list_execution_events("campaign-1")

    assert updated.status == "under_review"
    assert events[-1].event_type == "review_decision_added"
    assert events[-1].before is not None
    assert events[-1].before["status"] == "draft"
    assert events[-1].after is not None
    assert events[-1].after["status"] == "under_review"


def test_campaign_store_plan_work_package_trigger_gate_and_memo(tmp_path: Path) -> None:
    store = CampaignStore(tmp_path / "campaigns.sqlite")
    campaign = store.create_campaign(_campaign())
    objective = _objective()
    work_package = _work_package()
    budget = _budget()
    plan = _plan([objective], [work_package], budget)
    trigger = ReplanTrigger(
        trigger_id="trigger-1",
        campaign_id=campaign.campaign_id,
        trigger_type="budget_exceeded",
        severity="high",
        description="Budget changed.",
        linked_entity_ids=[],
        recommended_action="pause",
        metadata={},
    )
    gate = build_campaign_approval_gate(campaign.campaign_id)
    memo = CampaignMemo(
        memo_id="memo-1",
        campaign_id=campaign.campaign_id,
        title="Memo",
        executive_summary="Campaign memo.",
        objectives_summary="One objective.",
        selected_work_packages=[work_package.work_package_id],
        budget_summary="Within limits.",
        key_tradeoffs=[],
        risks=[],
        uncertainty_notes=[],
        replan_triggers=[trigger.trigger_id],
        approvals_required=["campaign:approve"],
        limitations=["Planning artifact only."],
        metadata={},
    )

    store.save_campaign_plan(plan)
    store.add_work_package(work_package)
    updated_package = store.update_work_package_status(
        work_package.work_package_id,
        "ready",
        actor="planner",
        rationale="Dependencies satisfied.",
    )
    store.add_replan_trigger(trigger)
    stored_gate = store.add_stage_gate_decision(gate)
    store.save_campaign_memo(memo)

    assert store.get_campaign_plan(plan.campaign_plan_id).campaign_id == campaign.campaign_id
    assert updated_package.status == "ready"
    assert store.list_replan_triggers(campaign.campaign_id)[0].trigger_type == "budget_exceeded"
    assert stored_gate["gate_type"] == "campaign_approval"
    assert store.list_campaign_memos(campaign.campaign_id)[0].memo_id == "memo-1"
    assert any(
        event.event_type == "stage_gate_decided"
        for event in store.list_execution_events(campaign.campaign_id)
    )


def test_campaign_store_add_execution_event_preserves_audit_trail(tmp_path: Path) -> None:
    store = CampaignStore(tmp_path / "campaigns.sqlite")
    store.create_campaign(_campaign())
    event = CampaignExecutionEvent(
        event_id="event-manual",
        campaign_id="campaign-1",
        work_package_id=None,
        event_type="paused",
        actor="owner",
        summary="Paused for review.",
        before={"status": "active"},
        after={"status": "paused"},
        metadata={},
    )

    store.add_execution_event(event)

    assert [item.event_id for item in store.list_execution_events("campaign-1")][-1] == (
        "event-manual"
    )


def test_campaign_store_export_import_round_trip(tmp_path: Path) -> None:
    source = CampaignStore(tmp_path / "source.sqlite")
    source.create_campaign(_campaign())
    source.save_campaign_plan(_plan([_objective()], [_work_package()], _budget()))
    source.save_campaign_memo(
        CampaignMemo(
            memo_id="memo-1",
            campaign_id="campaign-1",
            title="Memo",
            executive_summary="Campaign memo.",
            objectives_summary="One objective.",
            selected_work_packages=["work-package-1"],
            budget_summary="Within limits.",
            key_tradeoffs=[],
            risks=[],
            uncertainty_notes=[],
            replan_triggers=[],
            approvals_required=[],
            limitations=[],
            metadata={},
        )
    )
    export_path = tmp_path / "campaign-export.json"

    source.export_campaign_json("campaign-1", export_path)
    imported = CampaignStore(tmp_path / "imported.sqlite")
    imported.import_campaign_json(export_path)

    payload = json.loads(export_path.read_text())
    assert payload["campaign"]["campaign_id"] == "campaign-1"
    assert imported.get_campaign("campaign-1").name == "Campaign"
    assert imported.list_campaign_memos("campaign-1")[0].memo_id == "memo-1"


def _campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-1",
        project_id="project-1",
        program_id="program-1",
        name="Campaign",
        description="Campaign store test.",
        disease_focus=["PD"],
        target_focus=["MAOB"],
        hypothesis_ids=["hypothesis-1"],
        portfolio_selection_ids=["selection-1"],
        status="draft",
        metadata={},
    )


def _objective() -> CampaignObjective:
    return CampaignObjective(
        objective_id="objective-1",
        campaign_id="campaign-1",
        name="Objective",
        objective_type="validate_hypothesis",
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-1"],
        success_criteria=["Review evidence."],
        stop_criteria=["Stop if rejected."],
        priority_weight=0.7,
        metadata={"linked_hypothesis_ids": ["hypothesis-1"]},
    )


def _work_package() -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id="work-package-1",
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type="expert_review",
        title="Review package",
        description="High-level review package.",
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


def _budget() -> CampaignBudget:
    return CampaignBudget(
        budget_id="budget-1",
        campaign_id="campaign-1",
        max_total_cost=None,
        cost_units=None,
        max_assay_slots=None,
        max_review_hours=10.0,
        max_compute_units=None,
        max_codex_tasks=None,
        max_external_sync_jobs=None,
        reserved_budget={},
        metadata={},
    )


def _plan(
    objectives: list[CampaignObjective],
    work_packages: list[CampaignWorkPackage],
    budget: CampaignBudget,
) -> CampaignPlan:
    return CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id="campaign-1",
        objectives=objectives,
        work_packages=work_packages,
        budget=budget,
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.5,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={"within_limits": True},
        recommended_sequence=[item.work_package_id for item in work_packages],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={},
    )
