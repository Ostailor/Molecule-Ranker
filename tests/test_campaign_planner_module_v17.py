from __future__ import annotations

from molecule_ranker.campaigns.planner import plan_campaign
from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignBudget,
    CampaignObjective,
    CampaignWorkPackage,
)


def test_planner_selects_high_priority_packages_under_budget() -> None:
    campaign = _campaign()
    objectives = [
        _objective("objective-high", priority_weight=0.95),
        _objective("objective-low", priority_weight=0.2),
    ]
    packages = [
        _package("pkg-high", "expert_review", ["objective-high"], estimated_review_hours=1.0),
        _package("pkg-low", "expert_review", ["objective-low"], estimated_review_hours=1.0),
    ]

    plan = plan_campaign(
        campaign=campaign,
        objectives=objectives,
        work_packages=packages,
        budget=_budget(max_review_hours=1.0),
        hypothesis_ranking={"hypothesis-high": 0.95, "hypothesis-low": 0.2},
    )

    assert [package.work_package_id for package in plan.work_packages] == ["pkg-high"]
    assert plan.budget_summary["within_limits"] is True
    assert plan.metadata["excluded_work_package_ids"] == ["pkg-low"]


def test_planner_respects_dependency_order() -> None:
    campaign = _campaign()
    objective = _objective("objective-1")
    first = _package("pkg-first", "expert_review", ["objective-1"])
    second = _package(
        "pkg-second",
        "computational_rerun",
        ["objective-1"],
        dependencies=["pkg-first"],
        estimated_compute_units=1.0,
    )

    plan = plan_campaign(
        campaign=campaign,
        objectives=[objective],
        work_packages=[second, first],
        budget=_budget(max_review_hours=5.0, max_compute_units=5.0),
    )

    assert plan.recommended_sequence == ["pkg-first", "pkg-second"]


def test_generated_package_blocked_without_review() -> None:
    campaign = _campaign()
    objective = _objective("objective-generated")
    package = _package(
        "pkg-generated",
        "hypothesis_review",
        ["objective-generated"],
        required_approvals=["campaign_advancement_approval"],
        metadata={"generated_molecule": True},
    )

    plan = plan_campaign(
        campaign=campaign,
        objectives=[objective],
        work_packages=[package],
        budget=_budget(max_review_hours=5.0),
        review_status={},
        config={"require_generated_molecule_review": True},
    )

    assert plan.work_packages == []
    assert plan.metadata["blocked_work_packages"]["pkg-generated"] == "generated_review_required"
    assert plan.human_approval_required is True


def test_safety_contradiction_prioritized() -> None:
    campaign = _campaign()
    objectives = [
        _objective("objective-safe", objective_type="resolve_contradiction", priority_weight=0.7),
        _objective("objective-normal", priority_weight=0.8),
    ]
    packages = [
        _package(
            "pkg-safety",
            "developability_review",
            ["objective-safe"],
            warnings=["critical safety contradiction"],
            blocking_reasons=["stop pending expert risk review"],
            estimated_review_hours=1.0,
        ),
        _package("pkg-normal", "expert_review", ["objective-normal"], estimated_review_hours=1.0),
    ]

    plan = plan_campaign(
        campaign=campaign,
        objectives=objectives,
        work_packages=packages,
        budget=_budget(max_review_hours=1.0),
        graph_contradictions={"pkg-safety": 1.0},
    )

    assert [package.work_package_id for package in plan.work_packages] == ["pkg-safety"]
    assert "contradiction_detected" in plan.replan_triggers
    assert plan.risk_summary["critical_safety_or_contradiction_packages"] == ["pkg-safety"]


def test_budget_limit_enforced() -> None:
    campaign = _campaign()
    objective = _objective("objective-1")
    packages = [
        _package("pkg-1", "assay_triage_request", ["objective-1"], estimated_assay_slots=1),
        _package("pkg-2", "assay_triage_request", ["objective-1"], estimated_assay_slots=1),
    ]

    plan = plan_campaign(
        campaign=campaign,
        objectives=[objective],
        work_packages=packages,
        budget=_budget(max_assay_slots=1),
    )

    assert len(plan.work_packages) == 1
    assert plan.budget_summary["totals"]["assay_slots"] == 1
    assert plan.metadata["excluded_work_package_ids"]


def _campaign() -> Campaign:
    return Campaign(
        campaign_id="campaign-1",
        project_id="project-1",
        program_id="program-1",
        name="Planner campaign",
        description="Planner test campaign.",
        disease_focus=["PD"],
        target_focus=["MAOB"],
        hypothesis_ids=["hypothesis-high", "hypothesis-low"],
        portfolio_selection_ids=["selection-1"],
        status="draft",
        metadata={},
    )


def _objective(
    objective_id: str,
    *,
    objective_type: str = "validate_hypothesis",
    priority_weight: float = 0.5,
) -> CampaignObjective:
    suffix = objective_id.replace("objective-", "")
    return CampaignObjective(
        objective_id=objective_id,
        campaign_id="campaign-1",
        name=objective_id,
        objective_type=objective_type,  # type: ignore[arg-type]
        linked_hypothesis_ids=[f"hypothesis-{suffix}"],
        linked_candidate_ids=[f"candidate-{suffix}"],
        success_criteria=["Review deterministic artifacts."],
        stop_criteria=["Stop if source-backed review rejects the work."],
        priority_weight=priority_weight,
        metadata={"linked_hypothesis_ids": [f"hypothesis-{suffix}"]},
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
    objective_ids: list[str],
    *,
    dependencies: list[str] | None = None,
    required_approvals: list[str] | None = None,
    estimated_review_hours: float | None = None,
    estimated_compute_units: float | None = None,
    estimated_assay_slots: int | None = None,
    warnings: list[str] | None = None,
    blocking_reasons: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> CampaignWorkPackage:
    suffix = package_id.replace("pkg-", "")
    return CampaignWorkPackage(
        work_package_id=package_id,
        campaign_id="campaign-1",
        objective_ids=objective_ids,
        package_type=package_type,  # type: ignore[arg-type]
        title=f"{package_id} title",
        description="High-level advisory planning unit.",
        linked_candidate_ids=[f"candidate-{suffix}"],
        linked_hypothesis_ids=[f"hypothesis-{suffix}"],
        high_level_activity_category="planning review",
        dependencies=dependencies or [],
        required_approvals=required_approvals or ["campaign_advancement_approval"],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=estimated_review_hours,
        estimated_compute_units=estimated_compute_units,
        estimated_assay_slots=estimated_assay_slots,
        status="proposed",
        blocking_reasons=blocking_reasons or [],
        warnings=warnings or [],
        metadata=metadata or {},
    )
