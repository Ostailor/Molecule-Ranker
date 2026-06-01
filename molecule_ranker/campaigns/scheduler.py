from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from molecule_ranker.campaigns.dependencies import (
    build_dependency_graph,
    identify_blocked_work_packages,
    identify_parallelizable_packages,
    topological_sort_work_packages,
)
from molecule_ranker.campaigns.schemas import CampaignWorkPackage

CAMPAIGN_PHASES = (
    "phase_0_setup",
    "phase_1_review",
    "phase_2_computational_followup",
    "phase_3_external_sync_or_result_import",
    "phase_4_replan_or_decision",
)


def schedule_campaign_work(
    work_packages: Sequence[CampaignWorkPackage],
) -> dict[str, Any]:
    """Create a planning-order schedule, not an experimental protocol sequence."""

    recommended_sequence = topological_sort_work_packages(work_packages)
    package_index = {package.work_package_id: package for package in work_packages}
    phases = {phase: [] for phase in CAMPAIGN_PHASES}
    for package_id in recommended_sequence:
        package = package_index[package_id]
        phases[_phase_for_package(package)].append(package_id)
    return {
        "recommended_sequence": recommended_sequence,
        "phases": phases,
        "dependency_graph": build_dependency_graph(work_packages),
        "blocked_work_packages": identify_blocked_work_packages(work_packages),
        "parallel_groups": identify_parallelizable_packages(work_packages),
        "warnings": [
            "This is planning order, not a lab protocol sequence; no procedural "
            "experimental steps are provided."
        ],
        "planning_order_only": True,
        "not_lab_protocol": True,
    }


def _phase_for_package(package: CampaignWorkPackage) -> str:
    if package.package_type in {"expert_review", "hypothesis_review", "developability_review"}:
        return "phase_1_review"
    if package.package_type in {
        "computational_rerun",
        "structure_review",
        "active_learning_batch",
    }:
        return "phase_2_computational_followup"
    if package.package_type in {"external_sync", "assay_triage_request", "literature_update"}:
        return "phase_3_external_sync_or_result_import"
    if package.package_type == "portfolio_reoptimization":
        return "phase_4_replan_or_decision"
    return "phase_0_setup"


__all__ = ["CAMPAIGN_PHASES", "schedule_campaign_work"]
