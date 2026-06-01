from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from molecule_ranker.campaigns.schemas import (
    CampaignBudget,
    CampaignPlan,
    CampaignWorkPackage,
)

RESOURCE_DIMENSIONS = (
    "assay_slots",
    "review_hours",
    "compute_units",
    "codex_tasks",
    "external_sync_jobs",
    "docking_jobs",
    "model_training_jobs",
    "budget_cost",
)

DEFAULT_PLACEHOLDER_ESTIMATES: dict[str, dict[str, float]] = {
    "expert_review": {"review_hours": 1.0},
    "computational_rerun": {"compute_units": 1.0},
    "literature_update": {"review_hours": 1.0},
    "developability_review": {"review_hours": 1.5},
    "structure_review": {"review_hours": 1.0, "docking_jobs": 1.0},
    "assay_triage_request": {"assay_slots": 1.0, "review_hours": 0.5},
    "external_sync": {"external_sync_jobs": 1.0},
    "active_learning_batch": {"compute_units": 1.0},
    "portfolio_reoptimization": {"compute_units": 1.0},
    "hypothesis_review": {"review_hours": 1.0},
}

_BUDGET_LIMIT_FIELD = {
    "assay_slots": "max_assay_slots",
    "review_hours": "max_review_hours",
    "compute_units": "max_compute_units",
    "codex_tasks": "max_codex_tasks",
    "external_sync_jobs": "max_external_sync_jobs",
    "budget_cost": "max_total_cost",
}


def estimate_work_package_resources(
    work_package: CampaignWorkPackage,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Estimate resources for one high-level work package.

    Defaults are configurable placeholders for planning only. Cost remains unknown unless
    provided on the work package or through explicitly imported external cost data.
    """

    active_config = dict(config or {})
    package_type = str(work_package.package_type)
    placeholder_estimates = _placeholder_estimates(active_config).get(package_type, {})
    imported_costs = _imported_costs(active_config)
    generated_review_required = bool(active_config.get("require_generated_molecule_review", True))
    generated_package = _is_generated_molecule_package(work_package)

    review_hours = _estimated_dimension(
        explicit=work_package.estimated_review_hours,
        configured=(
            active_config.get("generated_molecule_review_hours")
            if generated_package and generated_review_required
            else placeholder_estimates.get("review_hours")
        ),
    )
    assay_slots = _estimated_dimension(
        explicit=work_package.estimated_assay_slots,
        configured=placeholder_estimates.get("assay_slots"),
    )
    compute_units = _estimated_dimension(
        explicit=work_package.estimated_compute_units,
        configured=placeholder_estimates.get("compute_units"),
    )
    codex_tasks = _estimated_dimension(
        explicit=_metadata_number(work_package, "codex_tasks"),
        configured=placeholder_estimates.get("codex_tasks"),
    )
    external_sync_jobs = _estimated_dimension(
        explicit=_metadata_number(work_package, "external_sync_jobs"),
        configured=placeholder_estimates.get("external_sync_jobs"),
    )
    docking_jobs = _estimated_dimension(
        explicit=_metadata_number(work_package, "docking_jobs"),
        configured=placeholder_estimates.get("docking_jobs"),
    )
    model_training_jobs = _estimated_dimension(
        explicit=_metadata_number(work_package, "model_training_jobs"),
        configured=placeholder_estimates.get("model_training_jobs"),
    )
    budget_cost = _budget_cost_estimate(work_package, imported_costs)
    required_approvals = list(work_package.required_approvals)
    if (
        generated_package
        and generated_review_required
        and "generated_molecule_review_gate" not in required_approvals
    ):
        required_approvals.append("generated_molecule_review_gate")

    return {
        "work_package_id": work_package.work_package_id,
        "assay_slots": assay_slots,
        "review_hours": review_hours,
        "compute_units": compute_units,
        "codex_tasks": codex_tasks,
        "external_sync_jobs": external_sync_jobs,
        "docking_jobs": docking_jobs,
        "model_training_jobs": model_training_jobs,
        "budget_cost": budget_cost,
        "review_required": bool(required_approvals),
        "required_approvals": sorted(required_approvals),
        "cost_estimate_notice": (
            "Cost estimates are planning estimates only. Default estimates are "
            "configurable placeholders. No real vendor or lab pricing is inferred."
        ),
    }


def compute_campaign_budget_summary(plan: CampaignPlan) -> dict[str, Any]:
    estimates = [
        estimate_work_package_resources(package, plan.budget.metadata)
        for package in plan.work_packages
    ]
    totals = _resource_totals(estimates)
    limits = _budget_limits(plan.budget)
    check = _check_totals_against_limits(totals, limits)
    return {
        "campaign_id": plan.campaign_id,
        "budget_id": plan.budget.budget_id,
        "totals": totals,
        "limits": limits,
        "within_limits": check["within_budget"],
        "exceeded_dimensions": check["exceeded_dimensions"],
        "unknown_dimensions": check["unknown_dimensions"],
        "cost_basis": _cost_basis(estimates),
        "work_package_estimates": estimates,
        "planning_estimates_only": True,
    }


def check_budget_constraints(
    plan: CampaignPlan,
    budget: CampaignBudget,
) -> dict[str, Any]:
    estimates = [
        estimate_work_package_resources(package, budget.metadata)
        for package in plan.work_packages
    ]
    totals = _resource_totals(estimates)
    limits = _budget_limits(budget)
    check = _check_totals_against_limits(totals, limits)
    warnings = []
    for dimension in check["exceeded_dimensions"]:
        warnings.append(f"{dimension} exceeds configured campaign budget limit.")
    if totals["budget_cost"] is None and limits.get("budget_cost") is not None:
        warnings.append("budget_cost is unknown; no real external cost data was supplied.")
    return {
        "within_budget": check["within_budget"],
        "totals": totals,
        "limits": limits,
        "exceeded_dimensions": check["exceeded_dimensions"],
        "unknown_dimensions": check["unknown_dimensions"],
        "warnings": warnings,
        "planning_estimates_only": True,
    }


def compute_resource_utilization(plan: CampaignPlan) -> dict[str, dict[str, Any]]:
    summary = compute_campaign_budget_summary(plan)
    utilization: dict[str, dict[str, Any]] = {}
    totals = summary["totals"]
    limits = summary["limits"]
    for dimension in RESOURCE_DIMENSIONS:
        used = totals.get(dimension)
        limit = limits.get(dimension)
        fraction = None
        if isinstance(used, int | float) and isinstance(limit, int | float) and limit > 0:
            fraction = used / limit
        utilization[dimension] = {
            "used": used,
            "limit": limit,
            "fraction": fraction,
            "status": _utilization_status(used, limit, fraction),
        }
    return utilization


def identify_budget_bottlenecks(plan: CampaignPlan) -> list[dict[str, Any]]:
    utilization = compute_resource_utilization(plan)
    bottlenecks: list[dict[str, Any]] = []
    for dimension, item in utilization.items():
        fraction = item["fraction"]
        if item["status"] == "exceeded" or (
            isinstance(fraction, int | float) and fraction >= 0.85
        ):
            bottlenecks.append(
                {
                    "dimension": dimension,
                    "used": item["used"],
                    "limit": item["limit"],
                    "fraction": fraction,
                    "status": item["status"],
                }
            )
    return sorted(
        bottlenecks,
        key=lambda item: (
            0.0 if item["fraction"] is None else -float(item["fraction"]),
            item["dimension"],
        ),
    )


def suggest_budget_adjustments(plan: CampaignPlan) -> list[str]:
    suggestions: list[str] = []
    for bottleneck in identify_budget_bottlenecks(plan):
        dimension = bottleneck["dimension"]
        suggestions.append(
            f"Review {dimension} demand, defer lower-priority work packages, or update "
            "configured planning limits with approved resource data."
        )
    summary = compute_campaign_budget_summary(plan)
    if summary["totals"]["budget_cost"] is None:
        suggestions.append(
            "Keep budget_cost marked unknown or import approved external cost data; do "
            "not infer real vendor or lab pricing."
        )
    if not suggestions:
        suggestions.append(
            "No budget adjustment is required by the current configurable planning estimates."
        )
    return suggestions


def _placeholder_estimates(config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    configured = config.get("default_estimates")
    merged = {key: dict(value) for key, value in DEFAULT_PLACEHOLDER_ESTIMATES.items()}
    if isinstance(configured, Mapping):
        for package_type, values in configured.items():
            if isinstance(values, Mapping):
                merged[str(package_type)] = {
                    key: float(value)
                    for key, value in values.items()
                    if isinstance(value, int | float)
                }
    return merged


def _imported_costs(config: Mapping[str, Any]) -> dict[str, float]:
    imported = config.get("imported_external_costs")
    if not isinstance(imported, Mapping):
        return {}
    return {
        str(key): float(value)
        for key, value in imported.items()
        if isinstance(value, int | float)
    }


def _estimated_dimension(
    *,
    explicit: int | float | None,
    configured: Any,
) -> dict[str, Any]:
    if explicit is not None:
        return {"value": explicit, "basis": "work_package_estimate"}
    if isinstance(configured, int | float):
        return {
            "value": float(configured),
            "basis": "configured_placeholder",
            "placeholder": True,
        }
    return {"value": 0.0, "basis": "not_required"}


def _budget_cost_estimate(
    work_package: CampaignWorkPackage,
    imported_costs: Mapping[str, float],
) -> dict[str, Any]:
    if work_package.estimated_cost is not None:
        return {
            "value": work_package.estimated_cost,
            "basis": "work_package_planning_estimate",
            "planning_estimate_only": True,
        }
    if work_package.work_package_id in imported_costs:
        return {
            "value": imported_costs[work_package.work_package_id],
            "basis": "imported_external_cost_data",
            "planning_estimate_only": True,
        }
    return {
        "value": None,
        "status": "unknown",
        "basis": "no_cost_data",
        "planning_estimate_only": True,
    }


def _resource_totals(estimates: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for dimension in RESOURCE_DIMENSIONS:
        if dimension == "budget_cost":
            values = [
                estimate[dimension]["value"]
                for estimate in estimates
                if estimate[dimension]["value"] is not None
            ]
            unknown_count = sum(1 for estimate in estimates if estimate[dimension]["value"] is None)
            totals[dimension] = None if unknown_count and not values else sum(values)
            continue
        totals[dimension] = sum(float(estimate[dimension]["value"]) for estimate in estimates)
    return totals


def _budget_limits(budget: CampaignBudget) -> dict[str, Any]:
    return {
        dimension: getattr(budget, field_name)
        for dimension, field_name in _BUDGET_LIMIT_FIELD.items()
    } | {
        "docking_jobs": budget.metadata.get("max_docking_jobs"),
        "model_training_jobs": budget.metadata.get("max_model_training_jobs"),
    }


def _check_totals_against_limits(
    totals: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    exceeded: list[str] = []
    unknown: list[str] = []
    for dimension, limit in limits.items():
        if limit is None:
            continue
        total = totals.get(dimension)
        if total is None:
            unknown.append(dimension)
            continue
        if isinstance(total, int | float) and isinstance(limit, int | float) and total > limit:
            exceeded.append(dimension)
    return {
        "within_budget": not exceeded,
        "exceeded_dimensions": exceeded,
        "unknown_dimensions": unknown,
    }


def _cost_basis(estimates: list[dict[str, Any]]) -> str:
    bases = {estimate["budget_cost"]["basis"] for estimate in estimates}
    if bases == {"no_cost_data"} or "no_cost_data" in bases:
        return "unknown cost; use relative units unless approved external cost data is imported"
    if "imported_external_cost_data" in bases:
        return "planning estimate from imported external cost data"
    return "planning estimate from work package values"


def _utilization_status(
    used: Any,
    limit: Any,
    fraction: float | None,
) -> str:
    if used is None:
        return "unknown"
    if limit is None:
        return "unbounded"
    if isinstance(fraction, int | float) and fraction > 1.0:
        return "exceeded"
    if isinstance(fraction, int | float) and fraction >= 0.85:
        return "near_limit"
    return "within_limit"


def _metadata_number(work_package: CampaignWorkPackage, key: str) -> float | None:
    value = work_package.metadata.get(key)
    return float(value) if isinstance(value, int | float) else None


def _is_generated_molecule_package(work_package: CampaignWorkPackage) -> bool:
    text = " ".join(
        [
            work_package.package_type,
            work_package.title,
            work_package.description,
            *(str(key) for key in work_package.metadata),
            *(str(value) for value in work_package.metadata.values()),
        ]
    ).lower()
    return "generated_molecule" in text or "generated molecule" in text


__all__ = [
    "RESOURCE_DIMENSIONS",
    "check_budget_constraints",
    "compute_campaign_budget_summary",
    "compute_resource_utilization",
    "estimate_work_package_resources",
    "identify_budget_bottlenecks",
    "suggest_budget_adjustments",
]
