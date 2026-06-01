from __future__ import annotations

import pytest

from molecule_ranker.campaigns.dependencies import (
    CampaignDependencyCycleError,
    build_dependency_graph,
    identify_blocked_work_packages,
    identify_parallelizable_packages,
    topological_sort_work_packages,
)
from molecule_ranker.campaigns.scheduler import schedule_campaign_work
from molecule_ranker.campaigns.schemas import CampaignWorkPackage


def test_dependency_sorting_respects_required_predecessors() -> None:
    review = _package("pkg-review", "expert_review")
    compute = _package(
        "pkg-compute",
        "computational_rerun",
        dependencies=["pkg-review"],
        metadata={
            "dependency_types": {
                "pkg-review": "requires_review_before",
            }
        },
    )

    graph = build_dependency_graph([compute, review])
    order = topological_sort_work_packages([compute, review])

    assert graph["edges"] == [
        {
            "from": "pkg-review",
            "to": "pkg-compute",
            "dependency_type": "requires_review_before",
        }
    ]
    assert order == ["pkg-review", "pkg-compute"]


def test_dependency_cycle_detection() -> None:
    first = _package("pkg-a", "expert_review", dependencies=["pkg-b"])
    second = _package("pkg-b", "computational_rerun", dependencies=["pkg-a"])

    with pytest.raises(CampaignDependencyCycleError, match="cycle"):
        topological_sort_work_packages([first, second])


def test_blocked_packages_include_safety_and_budget_blocks() -> None:
    safety = _package(
        "pkg-safety",
        "developability_review",
        metadata={
            "dependency_types": {
                "safety-review": "blocks_due_to_safety",
            }
        },
        blocking_reasons=["Safety review required."],
    )
    budget = _package(
        "pkg-budget",
        "assay_triage_request",
        metadata={
            "dependency_types": {
                "budget-approval": "blocks_due_to_budget",
            }
        },
    )

    blocked = identify_blocked_work_packages([safety, budget])

    assert blocked["pkg-safety"] == ["blocks_due_to_safety"]
    assert blocked["pkg-budget"] == ["blocks_due_to_budget"]


def test_parallel_groups_ignore_optional_parallel_edges() -> None:
    first = _package("pkg-a", "expert_review")
    second = _package(
        "pkg-b",
        "literature_update",
        dependencies=["pkg-a"],
        metadata={"dependency_types": {"pkg-a": "optional_parallel"}},
    )
    third = _package("pkg-c", "structure_review")

    groups = identify_parallelizable_packages([first, second, third])

    assert {"pkg-a", "pkg-b", "pkg-c"} in [set(group) for group in groups]


def test_scheduler_estimates_phases_and_recommended_sequence() -> None:
    packages = [
        _package("pkg-sync", "external_sync", dependencies=["pkg-review"]),
        _package("pkg-review", "expert_review"),
        _package("pkg-compute", "computational_rerun", dependencies=["pkg-review"]),
        _package("pkg-decision", "portfolio_reoptimization", dependencies=["pkg-sync"]),
    ]

    schedule = schedule_campaign_work(packages)

    assert schedule["recommended_sequence"][0] == "pkg-review"
    assert schedule["phases"]["phase_1_review"] == ["pkg-review"]
    assert schedule["phases"]["phase_2_computational_followup"] == ["pkg-compute"]
    assert schedule["phases"]["phase_3_external_sync_or_result_import"] == ["pkg-sync"]
    assert schedule["phases"]["phase_4_replan_or_decision"] == ["pkg-decision"]
    assert "planning order" in schedule["warnings"][0].lower()
    assert "protocol" in schedule["warnings"][0].lower()


def _package(
    package_id: str,
    package_type: str,
    *,
    dependencies: list[str] | None = None,
    metadata: dict[str, object] | None = None,
    blocking_reasons: list[str] | None = None,
) -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id=package_id,
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type=package_type,  # type: ignore[arg-type]
        title=f"{package_id} title",
        description="High-level campaign planning unit.",
        linked_candidate_ids=[],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="planning review",
        dependencies=dependencies or [],
        required_approvals=[],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=None,
        estimated_compute_units=None,
        estimated_assay_slots=None,
        status="proposed",
        blocking_reasons=blocking_reasons or [],
        warnings=[],
        metadata=metadata or {},
    )
