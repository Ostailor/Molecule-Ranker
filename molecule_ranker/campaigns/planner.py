from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.campaigns.budget import (
    check_budget_constraints,
    compute_campaign_budget_summary,
    estimate_work_package_resources,
)
from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignBudget,
    CampaignObjective,
    CampaignPlan,
    CampaignWorkPackage,
)


def plan_campaign(
    *,
    campaign: Campaign,
    objectives: Sequence[CampaignObjective],
    work_packages: Sequence[CampaignWorkPackage],
    budget: CampaignBudget,
    portfolio_outputs: Mapping[str, Any] | None = None,
    hypothesis_ranking: Mapping[str, float] | None = None,
    active_learning_suggestions: Mapping[str, Any] | None = None,
    review_status: Mapping[str, str] | None = None,
    experimental_evidence: Mapping[str, Any] | None = None,
    model_uncertainty: Mapping[str, float] | None = None,
    graph_contradictions: Mapping[str, float] | None = None,
    config: Mapping[str, Any] | None = None,
) -> CampaignPlan:
    """Select and sequence advisory campaign work packages under constraints."""

    active_config = dict(config or {})
    objective_index = {objective.objective_id: objective for objective in objectives}
    ranked = sorted(
        [
            (
                _work_package_value(
                    package,
                    objective_index=objective_index,
                    budget=budget,
                    portfolio_outputs=portfolio_outputs or {},
                    hypothesis_ranking=hypothesis_ranking or {},
                    active_learning_suggestions=active_learning_suggestions or {},
                    model_uncertainty=model_uncertainty or {},
                    graph_contradictions=graph_contradictions or {},
                    config=active_config,
                ),
                package,
            )
            for package in work_packages
        ],
        key=lambda item: (-item[0], item[1].work_package_id),
    )

    selected: list[CampaignWorkPackage] = []
    excluded: list[str] = []
    blocked: dict[str, str] = {}
    review_lookup = review_status or {}
    for _, package in ranked:
        block_reason = _block_reason(package, review_lookup, active_config)
        if block_reason is not None:
            blocked[package.work_package_id] = block_reason
            continue
        candidate = [*selected, package]
        if not _within_budget(candidate, budget):
            excluded.append(package.work_package_id)
            continue
        selected.append(package)

    ordered_ids = _dependency_order(selected)
    package_index = {package.work_package_id: package for package in selected}
    ordered_packages = [package_index[item] for item in ordered_ids if item in package_index]
    budget_summary = compute_campaign_budget_summary(
        _plan_shell(campaign, objectives, ordered_packages, budget)
    )
    replan_triggers = _replan_triggers(
        ordered_packages,
        graph_contradictions=graph_contradictions or {},
        experimental_evidence=experimental_evidence or {},
        model_uncertainty=model_uncertainty or {},
        budget_summary=budget_summary,
    )
    warnings = [
        "Campaign planner computes advisory plans only.",
        "Human approval is required before marking a campaign active.",
        "Codex may summarize deterministic campaign plans but must not compute them.",
        "Work packages are high-level planning units, not experimental protocols.",
    ]
    if blocked:
        warnings.append("Some work packages were blocked by review or gate requirements.")
    if excluded:
        warnings.append("Some work packages were excluded by configured budget constraints.")
    return CampaignPlan(
        campaign_plan_id=_stable_id("campaign-plan", campaign.campaign_id, *ordered_ids),
        campaign_id=campaign.campaign_id,
        objectives=list(objectives),
        work_packages=ordered_packages,
        budget=budget,
        stage_gates=_stage_gates(ordered_packages),
        dependency_graph=_dependency_graph(ordered_packages),
        expected_learning_value=_average(
            [
                _learning_value(
                    package,
                    active_learning_suggestions or {},
                    model_uncertainty or {},
                )
                for package in ordered_packages
            ]
        ),
        risk_summary={
            "critical_safety_or_contradiction_packages": [
                package.work_package_id
                for package in ordered_packages
                if _is_safety_or_contradiction(package, graph_contradictions or {})
            ],
            "blocked_work_packages": blocked,
        },
        uncertainty_summary=_uncertainty_summary(ordered_packages, model_uncertainty or {}),
        budget_summary=budget_summary,
        recommended_sequence=ordered_ids,
        replan_triggers=replan_triggers,
        human_approval_required=bool(active_config.get("human_approval_required", True)),
        warnings=warnings,
        metadata={
            "advisory_plan": True,
            "codex_computed_plan": False,
            "excluded_work_package_ids": excluded,
            "blocked_work_packages": blocked,
            "score_by_work_package": {
                package.work_package_id: score for score, package in ranked
            },
        },
    )


def _work_package_value(
    package: CampaignWorkPackage,
    *,
    objective_index: Mapping[str, CampaignObjective],
    budget: CampaignBudget,
    portfolio_outputs: Mapping[str, Any],
    hypothesis_ranking: Mapping[str, float],
    active_learning_suggestions: Mapping[str, Any],
    model_uncertainty: Mapping[str, float],
    graph_contradictions: Mapping[str, float],
    config: Mapping[str, Any],
) -> float:
    hypothesis_priority = _hypothesis_priority(package, objective_index, hypothesis_ranking)
    learning_value = _learning_value(package, active_learning_suggestions, model_uncertainty)
    portfolio_relevance = _portfolio_relevance(package, portfolio_outputs)
    contradiction_importance = _contradiction_importance(package, graph_contradictions)
    uncertainty_resolution_value = _uncertainty_resolution_value(package, model_uncertainty)
    risk_penalty = _risk_penalty(package)
    resource_cost_penalty = _resource_cost_penalty(package, budget, config)
    dependency_penalty = 0.05 * len(package.dependencies)
    return (
        hypothesis_priority
        + learning_value
        + portfolio_relevance
        + contradiction_importance
        + uncertainty_resolution_value
        - risk_penalty
        - resource_cost_penalty
        - dependency_penalty
    )


def _block_reason(
    package: CampaignWorkPackage,
    review_status: Mapping[str, str],
    config: Mapping[str, Any],
) -> str | None:
    if (
        bool(config.get("require_generated_molecule_review", True))
        and _is_generated_package(package)
        and "generated_molecule_review_gate" not in package.required_approvals
    ):
        statuses = {
            review_status.get(item, "")
            for item in [*package.linked_candidate_ids, *package.linked_hypothesis_ids]
        }
        if not statuses & {"approved", "reviewed", "expert_approved"}:
            return "generated_review_required"
    return None


def _within_budget(packages: Sequence[CampaignWorkPackage], budget: CampaignBudget) -> bool:
    shell = CampaignPlan(
        campaign_plan_id="budget-check",
        campaign_id=budget.campaign_id,
        objectives=[],
        work_packages=list(packages),
        budget=budget,
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.0,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={},
        recommended_sequence=[],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={},
    )
    return bool(check_budget_constraints(shell, budget)["within_budget"])


def _dependency_order(packages: Sequence[CampaignWorkPackage]) -> list[str]:
    package_ids = {package.work_package_id for package in packages}
    deps = {
        package.work_package_id: [
            dependency for dependency in package.dependencies if dependency in package_ids
        ]
        for package in packages
    }
    ordered: list[str] = []
    pending = set(package_ids)
    while pending:
        ready = sorted(
            package_id
            for package_id in pending
            if all(dependency in ordered for dependency in deps[package_id])
        )
        if not ready:
            ordered.extend(sorted(pending))
            break
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def _plan_shell(
    campaign: Campaign,
    objectives: Sequence[CampaignObjective],
    packages: Sequence[CampaignWorkPackage],
    budget: CampaignBudget,
) -> CampaignPlan:
    return CampaignPlan(
        campaign_plan_id="budget-summary",
        campaign_id=campaign.campaign_id,
        objectives=list(objectives),
        work_packages=list(packages),
        budget=budget,
        stage_gates=[],
        dependency_graph={},
        expected_learning_value=0.0,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={},
        recommended_sequence=[package.work_package_id for package in packages],
        replan_triggers=[],
        human_approval_required=True,
        warnings=[],
        metadata={},
    )


def _hypothesis_priority(
    package: CampaignWorkPackage,
    objective_index: Mapping[str, CampaignObjective],
    hypothesis_ranking: Mapping[str, float],
) -> float:
    values: list[float] = []
    for hypothesis_id in package.linked_hypothesis_ids:
        value = hypothesis_ranking.get(hypothesis_id)
        if isinstance(value, int | float):
            values.append(float(value))
    for objective_id in package.objective_ids:
        objective = objective_index.get(objective_id)
        if objective is not None:
            values.append(objective.priority_weight)
    return _average(values)


def _learning_value(
    package: CampaignWorkPackage,
    active_learning_suggestions: Mapping[str, Any],
    model_uncertainty: Mapping[str, float],
) -> float:
    values: list[float] = []
    if package.package_type == "active_learning_batch":
        values.append(0.7)
    for candidate_id in package.linked_candidate_ids:
        suggestion = active_learning_suggestions.get(candidate_id)
        if isinstance(suggestion, Mapping):
            score = suggestion.get("expected_learning_value") or suggestion.get("learning_value")
            if isinstance(score, int | float):
                values.append(float(score))
        uncertainty = model_uncertainty.get(candidate_id)
        if isinstance(uncertainty, int | float):
            values.append(float(uncertainty))
    return _average(values)


def _portfolio_relevance(
    package: CampaignWorkPackage,
    portfolio_outputs: Mapping[str, Any],
) -> float:
    selected_ids = set()
    raw = portfolio_outputs.get("selected_candidate_ids")
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        selected_ids.update(str(item) for item in raw)
    raw_selections = portfolio_outputs.get("selections", [])
    selections = raw_selections if isinstance(raw_selections, list) else []
    for selection in selections:
        if isinstance(selection, Mapping):
            selected = selection.get("selected_candidate_ids")
            if isinstance(selected, Sequence) and not isinstance(selected, str):
                selected_ids.update(str(item) for item in selected)
    return 0.5 if selected_ids & set(package.linked_candidate_ids) else 0.0


def _contradiction_importance(
    package: CampaignWorkPackage,
    graph_contradictions: Mapping[str, float],
) -> float:
    if package.work_package_id in graph_contradictions:
        return float(graph_contradictions[package.work_package_id])
    return 0.8 if _is_safety_or_contradiction(package, graph_contradictions) else 0.0


def _uncertainty_resolution_value(
    package: CampaignWorkPackage,
    model_uncertainty: Mapping[str, float],
) -> float:
    values = [
        float(model_uncertainty[item])
        for item in package.linked_candidate_ids
        if isinstance(model_uncertainty.get(item), int | float)
    ]
    return _average(values)


def _risk_penalty(package: CampaignWorkPackage) -> float:
    if _is_safety_or_contradiction(package, {}):
        return -0.5
    text = _package_text(package)
    if "critical" in text or "safety" in text:
        return 0.2
    return 0.0


def _resource_cost_penalty(
    package: CampaignWorkPackage,
    budget: CampaignBudget,
    config: Mapping[str, Any],
) -> float:
    estimate = estimate_work_package_resources(package, {**budget.metadata, **config})
    penalty = 0.0
    for dimension, limit in (
        ("assay_slots", budget.max_assay_slots),
        ("review_hours", budget.max_review_hours),
        ("compute_units", budget.max_compute_units),
        ("budget_cost", budget.max_total_cost),
    ):
        value = estimate[dimension]["value"]
        if isinstance(value, int | float) and isinstance(limit, int | float) and limit > 0:
            penalty += min(0.5, 0.2 * (value / limit))
    return penalty


def _replan_triggers(
    packages: Sequence[CampaignWorkPackage],
    *,
    graph_contradictions: Mapping[str, float],
    experimental_evidence: Mapping[str, Any],
    model_uncertainty: Mapping[str, float],
    budget_summary: Mapping[str, Any],
) -> list[str]:
    triggers: set[str] = set()
    package_has_contradiction = any(
        _is_safety_or_contradiction(package, {}) for package in packages
    )
    if graph_contradictions or package_has_contradiction:
        triggers.add("contradiction_detected")
    if experimental_evidence:
        triggers.add("result_imported")
    if model_uncertainty:
        triggers.add("model_retrained")
    if budget_summary.get("exceeded_dimensions"):
        triggers.add("budget_exceeded")
    return sorted(triggers)


def _stage_gates(packages: Sequence[CampaignWorkPackage]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for package in packages:
        if package.required_approvals:
            gates.append(
                {
                    "work_package_id": package.work_package_id,
                    "required_approvals": package.required_approvals,
                    "decision": "pending",
                }
            )
    return gates


def _dependency_graph(packages: Sequence[CampaignWorkPackage]) -> dict[str, Any]:
    return {package.work_package_id: list(package.dependencies) for package in packages}


def _uncertainty_summary(
    packages: Sequence[CampaignWorkPackage],
    model_uncertainty: Mapping[str, float],
) -> dict[str, Any]:
    values = [
        float(model_uncertainty[candidate_id])
        for package in packages
        for candidate_id in package.linked_candidate_ids
        if isinstance(model_uncertainty.get(candidate_id), int | float)
    ]
    return {
        "candidate_uncertainty_count": len(values),
        "mean_candidate_uncertainty": _average(values) if values else None,
    }


def _is_generated_package(package: CampaignWorkPackage) -> bool:
    text = _package_text(package)
    return "generated_molecule" in text or "generated molecule" in text


def _is_safety_or_contradiction(
    package: CampaignWorkPackage,
    graph_contradictions: Mapping[str, float],
) -> bool:
    text = _package_text(package)
    return (
        package.work_package_id in graph_contradictions
        or "contradiction" in text
        or "critical safety" in text
        or "safety contradiction" in text
        or "stop pending expert risk review" in text
    )


def _package_text(package: CampaignWorkPackage) -> str:
    return " ".join(
        [
            package.work_package_id,
            package.package_type,
            package.title,
            package.description,
            package.high_level_activity_category,
            *package.blocking_reasons,
            *package.warnings,
            *(str(key) for key in package.metadata),
            *(str(value) for value in package.metadata.values()),
        ]
    ).lower()


def _average(values: Sequence[float]) -> float:
    usable = [float(value) for value in values]
    if not usable:
        return 0.0
    return sum(usable) / len(usable)


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts if part is not None) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"


__all__ = ["plan_campaign"]
