from __future__ import annotations

from molecule_ranker.campaigns.budget import (
    check_budget_constraints,
    compute_campaign_budget_summary,
    compute_resource_utilization,
    estimate_work_package_resources,
    identify_budget_bottlenecks,
    suggest_budget_adjustments,
)
from molecule_ranker.campaigns.schemas import (
    CampaignBudget,
    CampaignObjective,
    CampaignPlan,
    CampaignWorkPackage,
)


def test_budget_summary_computes_assay_slot_usage() -> None:
    plan = _plan(
        [
            _package("pkg-assay-1", "assay_triage_request", estimated_assay_slots=2),
            _package("pkg-assay-2", "assay_triage_request", estimated_assay_slots=1),
        ],
        budget=_budget(max_assay_slots=4),
    )

    summary = compute_campaign_budget_summary(plan)
    utilization = compute_resource_utilization(plan)

    assert summary["totals"]["assay_slots"] == 3
    assert summary["limits"]["assay_slots"] == 4
    assert utilization["assay_slots"]["used"] == 3
    assert utilization["assay_slots"]["fraction"] == 0.75


def test_budget_summary_computes_review_hour_usage() -> None:
    plan = _plan(
        [
            _package("pkg-review-1", "expert_review", estimated_review_hours=1.5),
            _package("pkg-review-2", "developability_review", estimated_review_hours=2.0),
        ],
        budget=_budget(max_review_hours=4.0),
    )

    summary = compute_campaign_budget_summary(plan)

    assert summary["totals"]["review_hours"] == 3.5
    assert summary["within_limits"] is True


def test_budget_constraints_emit_exceeded_warning() -> None:
    plan = _plan(
        [_package("pkg-assay", "assay_triage_request", estimated_assay_slots=5)],
        budget=_budget(max_assay_slots=2),
    )

    check = check_budget_constraints(plan, plan.budget)
    bottlenecks = identify_budget_bottlenecks(plan)
    suggestions = suggest_budget_adjustments(plan)

    assert check["within_budget"] is False
    assert "assay_slots" in check["exceeded_dimensions"]
    assert any(item["dimension"] == "assay_slots" for item in bottlenecks)
    assert any("assay_slots" in suggestion for suggestion in suggestions)


def test_unknown_cost_is_handled_without_fabricating_external_costs() -> None:
    package = _package("pkg-review", "expert_review", estimated_cost=None)
    plan = _plan([package], budget=_budget(max_total_cost=1000.0))

    resources = estimate_work_package_resources(package, {})
    summary = compute_campaign_budget_summary(plan)

    assert resources["budget_cost"]["value"] is None
    assert resources["budget_cost"]["status"] == "unknown"
    assert summary["totals"]["budget_cost"] is None
    assert "unknown" in summary["cost_basis"]
    assert "vendor" not in summary["cost_basis"].lower()
    assert "lab pricing" not in summary["cost_basis"].lower()


def test_generated_molecule_packages_use_configured_review_requirement() -> None:
    package = _package(
        "pkg-generated",
        "hypothesis_review",
        metadata={"generated_molecule": True},
        required_approvals=[],
    )

    resources = estimate_work_package_resources(
        package,
        {
            "require_generated_molecule_review": True,
            "generated_molecule_review_hours": 2.5,
            "default_estimates": {"hypothesis_review": {"review_hours": 0.5}},
        },
    )

    assert resources["review_hours"]["value"] == 2.5
    assert resources["review_hours"]["basis"] == "configured_placeholder"
    assert resources["review_required"] is True
    assert "generated_molecule_review_gate" in resources["required_approvals"]


def _plan(
    packages: list[CampaignWorkPackage],
    *,
    budget: CampaignBudget,
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
    return CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id="campaign-1",
        objectives=[objective],
        work_packages=packages,
        budget=budget,
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.5,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={},
        recommended_sequence=[package.work_package_id for package in packages],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={},
    )


def _budget(**overrides: object) -> CampaignBudget:
    data = {
        "budget_id": "budget-1",
        "campaign_id": "campaign-1",
        "max_total_cost": None,
        "cost_units": None,
        "max_assay_slots": None,
        "max_review_hours": None,
        "max_compute_units": None,
        "max_codex_tasks": None,
        "max_external_sync_jobs": None,
        "reserved_budget": {},
        "metadata": {},
    }
    data.update(overrides)
    return CampaignBudget(**data)


def _package(
    package_id: str,
    package_type: str,
    *,
    estimated_cost: float | None = None,
    estimated_review_hours: float | None = None,
    estimated_compute_units: float | None = None,
    estimated_assay_slots: int | None = None,
    required_approvals: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id=package_id,
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type=package_type,  # type: ignore[arg-type]
        title=f"{package_type} package",
        description="High-level campaign planning work package.",
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="planning review",
        dependencies=[],
        required_approvals=required_approvals or ["campaign_advancement_approval"],
        estimated_cost=estimated_cost,
        cost_units=None,
        estimated_review_hours=estimated_review_hours,
        estimated_compute_units=estimated_compute_units,
        estimated_assay_slots=estimated_assay_slots,
        status="proposed",
        blocking_reasons=[],
        warnings=[],
        metadata=metadata or {},
    )
