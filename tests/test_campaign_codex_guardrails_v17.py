from __future__ import annotations

from molecule_ranker.campaigns.codex_assistant import (
    CAMPAIGN_CODEX_TASKS,
    validate_campaign_codex_output,
)
from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignBudget,
    CampaignObjective,
    CampaignPlan,
    CampaignWorkPackage,
)
from molecule_ranker.codex_backbone.schemas import CodexTaskResult


def test_campaign_codex_fake_work_package_id_flagged() -> None:
    checked = validate_campaign_codex_output(
        _result(
            "campaign-1 plan-1 cites work package wp-fake, hypothesis-1, "
            "selection-1, artifact:campaign-plan."
        ),
        plan=_plan(),
        campaign=_campaign(),
        artifact_ids={"artifact:campaign-plan"},
    )

    assert checked.status == "guardrail_failed"
    assert any("unknown work package ID" in warning for warning in checked.guardrail_warnings)


def test_campaign_codex_invented_cost_flagged() -> None:
    checked = validate_campaign_codex_output(
        _result(
            "campaign-1 plan-1 cites wp-1, hypothesis-1, selection-1, artifact:campaign-plan. "
            "The package will cost $5000."
        ),
        plan=_plan(),
        campaign=_campaign(),
        artifact_ids={"artifact:campaign-plan"},
    )

    assert checked.status == "guardrail_failed"
    assert any("invented cost" in warning.lower() for warning in checked.guardrail_warnings)


def test_campaign_codex_protocol_text_flagged() -> None:
    checked = validate_campaign_codex_output(
        _result(
            "campaign-1 plan-1 cites wp-1, hypothesis-1, selection-1, artifact:campaign-plan. "
            "Use reagent concentration 10 nM and incubate at 37 C."
        ),
        plan=_plan(),
        campaign=_campaign(),
        artifact_ids={"artifact:campaign-plan"},
    )

    assert checked.status == "guardrail_failed"
    assert any("procedural lab detail" in warning.lower() for warning in checked.guardrail_warnings)


def test_campaign_codex_safe_memo_passes() -> None:
    checked = validate_campaign_codex_output(
        _result(
            "Campaign memo for campaign-1 and plan-1 cites work package wp-1, "
            "hypothesis hypothesis-1, portfolio selection selection-1, and "
            "artifact artifact:campaign-plan. Tradeoffs are budget fit, review gates, "
            "and uncertainty. Generated molecules remain computational hypotheses."
        ),
        plan=_plan(),
        campaign=_campaign(),
        artifact_ids={"artifact:campaign-plan"},
    )

    assert CAMPAIGN_CODEX_TASKS == {
        "draft_campaign_memo",
        "summarize_campaign_tradeoffs",
        "explain_replan_trigger",
        "draft_review_questions_for_campaign",
        "summarize_budget_bottlenecks",
        "draft_project_update_from_campaign",
    }
    assert checked.status == "succeeded"
    assert checked.guardrail_warnings == []


def _campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-1",
        project_id="project-1",
        program_id="program-1",
        name="Campaign",
        description="Campaign planning artifact.",
        disease_focus=["Disease A"],
        target_focus=["T1"],
        hypothesis_ids=["hypothesis-1"],
        portfolio_selection_ids=["selection-1"],
        status="draft",
        metadata={"source_artifacts": {"campaign_plan": "artifact:campaign-plan"}},
    )


def _objective() -> CampaignObjective:
    return CampaignObjective(
        objective_id="objective-1",
        campaign_id="campaign-1",
        name="Objective",
        objective_type="validate_hypothesis",
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-1"],
        success_criteria=["Review source-backed campaign context."],
        stop_criteria=["Stop if review rejects source-backed context."],
        priority_weight=0.8,
        metadata={"linked_hypothesis_ids": ["hypothesis-1"]},
    )


def _package() -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id="wp-1",
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type="expert_review",
        title="Review package",
        description="High-level advisory planning unit.",
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="planning review",
        dependencies=[],
        required_approvals=["campaign_advancement_approval"],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=1.0,
        estimated_compute_units=None,
        estimated_assay_slots=None,
        status="proposed",
        blocking_reasons=[],
        warnings=[],
        metadata={"artifact_ids": ["artifact:campaign-plan"]},
    )


def _plan() -> CampaignPlan:
    return CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id="campaign-1",
        objectives=[_objective()],
        work_packages=[_package()],
        budget=CampaignBudget(
            budget_id="budget-1",
            campaign_id="campaign-1",
            max_total_cost=None,
            cost_units=None,
            max_assay_slots=None,
            max_review_hours=2.0,
            max_compute_units=None,
            max_codex_tasks=None,
            max_external_sync_jobs=None,
            reserved_budget={},
            metadata={"cost_basis": "unknown"},
        ),
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.5,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={"usage": {"review_hours": 1.0}, "cost_basis": "unknown"},
        recommended_sequence=["wp-1"],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={"artifact_ids": ["artifact:campaign-plan"]},
    )


def _result(text: str) -> CodexTaskResult:
    return CodexTaskResult(
        task_id="campaign-codex",
        task_type="draft_campaign_memo",
        status="succeeded",
        output_text=text,
        output_json=None,
    )
