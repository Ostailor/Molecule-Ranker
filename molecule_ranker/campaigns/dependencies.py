from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from molecule_ranker.campaigns.schemas import CampaignWorkPackage

CampaignDependencyType = Literal[
    "requires_review_before",
    "requires_result_before",
    "requires_sync_before",
    "requires_model_update_before",
    "requires_portfolio_update_before",
    "blocks_due_to_safety",
    "blocks_due_to_budget",
    "optional_parallel",
]

DEPENDENCY_TYPES = {
    "requires_review_before",
    "requires_result_before",
    "requires_sync_before",
    "requires_model_update_before",
    "requires_portfolio_update_before",
    "blocks_due_to_safety",
    "blocks_due_to_budget",
    "optional_parallel",
}
BLOCKING_DEPENDENCY_TYPES = {"blocks_due_to_safety", "blocks_due_to_budget"}
ORDERING_DEPENDENCY_TYPES = DEPENDENCY_TYPES - {"optional_parallel"} - BLOCKING_DEPENDENCY_TYPES


class CampaignDependencyCycleError(ValueError):
    """Raised when campaign planning dependencies contain a cycle."""


def build_dependency_graph(
    work_packages: Sequence[CampaignWorkPackage],
) -> dict[str, Any]:
    package_ids = {package.work_package_id for package in work_packages}
    nodes = sorted(package_ids)
    edges: list[dict[str, str]] = []
    for package in work_packages:
        dependency_types = _dependency_types(package)
        for dependency in package.dependencies:
            dependency_type = dependency_types.get(dependency, "requires_review_before")
            if dependency not in package_ids and dependency_type not in BLOCKING_DEPENDENCY_TYPES:
                continue
            edges.append(
                {
                    "from": dependency,
                    "to": package.work_package_id,
                    "dependency_type": dependency_type,
                }
            )
    return {
        "nodes": nodes,
        "edges": edges,
        "planning_order_only": True,
        "not_lab_protocol": True,
    }


def topological_sort_work_packages(
    work_packages: Sequence[CampaignWorkPackage],
) -> list[str]:
    graph = build_dependency_graph(work_packages)
    package_ids = set(graph["nodes"])
    predecessors: dict[str, set[str]] = {package_id: set() for package_id in package_ids}
    for edge in graph["edges"]:
        dependency_type = edge["dependency_type"]
        if dependency_type not in ORDERING_DEPENDENCY_TYPES:
            continue
        source = edge["from"]
        target = edge["to"]
        if source in package_ids and target in package_ids:
            predecessors[target].add(source)

    ordered: list[str] = []
    pending = set(package_ids)
    while pending:
        ready = sorted(
            package_id
            for package_id in pending
            if predecessors[package_id].issubset(set(ordered))
        )
        if not ready:
            cycle_nodes = ", ".join(sorted(pending))
            raise CampaignDependencyCycleError(
                f"Campaign dependency cycle detected among: {cycle_nodes}"
            )
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def identify_blocked_work_packages(
    work_packages: Sequence[CampaignWorkPackage],
) -> dict[str, list[str]]:
    blocked: dict[str, list[str]] = {}
    for package in work_packages:
        reasons = [
            dependency_type
            for dependency_type in _dependency_types(package).values()
            if dependency_type in BLOCKING_DEPENDENCY_TYPES
        ]
        text = " ".join(package.blocking_reasons).lower()
        if "safety" in text and "blocks_due_to_safety" not in reasons:
            reasons.append("blocks_due_to_safety")
        if "budget" in text and "blocks_due_to_budget" not in reasons:
            reasons.append("blocks_due_to_budget")
        if reasons:
            blocked[package.work_package_id] = sorted(set(reasons))
    return blocked


def identify_parallelizable_packages(
    work_packages: Sequence[CampaignWorkPackage],
) -> list[list[str]]:
    graph = build_dependency_graph(work_packages)
    package_ids = set(graph["nodes"])
    predecessors: dict[str, set[str]] = {package_id: set() for package_id in package_ids}
    for edge in graph["edges"]:
        dependency_type = edge["dependency_type"]
        if dependency_type not in ORDERING_DEPENDENCY_TYPES:
            continue
        source = edge["from"]
        target = edge["to"]
        if source in package_ids and target in package_ids:
            predecessors[target].add(source)

    completed: set[str] = set()
    pending = set(package_ids)
    groups: list[list[str]] = []
    while pending:
        ready = sorted(
            package_id
            for package_id in pending
            if predecessors[package_id].issubset(completed)
        )
        if not ready:
            raise CampaignDependencyCycleError(
                "Campaign dependency cycle prevents parallel group identification."
            )
        groups.append(ready)
        completed.update(ready)
        pending.difference_update(ready)
    return groups


def _dependency_types(package: CampaignWorkPackage) -> dict[str, str]:
    raw = package.metadata.get("dependency_types")
    if not isinstance(raw, Mapping):
        return {}
    output: dict[str, str] = {}
    for dependency, dependency_type in raw.items():
        dep_type = str(dependency_type)
        if dep_type in DEPENDENCY_TYPES:
            output[str(dependency)] = dep_type
    return output


__all__ = [
    "BLOCKING_DEPENDENCY_TYPES",
    "DEPENDENCY_TYPES",
    "ORDERING_DEPENDENCY_TYPES",
    "CampaignDependencyCycleError",
    "CampaignDependencyType",
    "build_dependency_graph",
    "identify_blocked_work_packages",
    "identify_parallelizable_packages",
    "topological_sort_work_packages",
]
