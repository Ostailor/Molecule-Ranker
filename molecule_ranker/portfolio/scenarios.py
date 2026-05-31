from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .constraints import default_constraints
from .objectives import default_objectives
from .optimizer import PortfolioOptimizer
from .schemas import (
    DecisionScenario,
    Portfolio,
    PortfolioCandidate,
    PortfolioConstraint,
    PortfolioObjective,
    PortfolioSelection,
    ResourceBudget,
    SensitivityAnalysis,
)
from .uncertainty import candidate_uncertainty_sources

SCENARIO_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "conservative": {
        "evidence_strength": 1.5,
        "developability": 1.6,
        "correlated_risk": 2.0,
        "generated_overexposure": 2.8,
        "learning_value": 0.5,
        "scaffold_diversity": 0.8,
    },
    "exploration": {
        "learning_value": 3.0,
        "target_coverage": 1.8,
        "scaffold_diversity": 2.0,
        "mechanism_diversity": 1.5,
        "evidence_strength": 0.55,
        "generated_overexposure": 0.35,
    },
    "exploit": {
        "evidence_strength": 3.0,
        "experiment_readiness": 1.5,
        "experimental_followup_value": 1.4,
        "learning_value": 0.4,
        "generated_overexposure": 1.4,
    },
    "safety_first": {
        "developability": 3.0,
        "correlated_risk": 3.0,
        "generated_overexposure": 1.5,
        "learning_value": 0.4,
    },
    "budget_limited": {
        "experimental_followup_value": 2.0,
        "experiment_readiness": 1.6,
        "evidence_strength": 1.2,
        "learning_value": 0.8,
    },
    "generated_discovery": {
        "generation": 1.4,
        "learning_value": 2.0,
        "scaffold_diversity": 1.6,
        "target_coverage": 1.3,
        "generated_overexposure": 0.25,
    },
}


def build_decision_scenario(
    scenario_id: str,
    name: str,
    *,
    description: str,
    assumptions: list[str],
    objective_overrides: Mapping[str, Any] | None = None,
    constraint_overrides: Mapping[str, Any] | None = None,
    budget_overrides: Mapping[str, Any] | None = None,
    selection: PortfolioSelection | None = None,
) -> DecisionScenario:
    return DecisionScenario(
        scenario_id=scenario_id,
        name=name,
        description=description,
        objective_overrides=dict(objective_overrides or {}),
        constraint_overrides=dict(constraint_overrides or {}),
        budget_overrides=dict(budget_overrides or {}),
        assumptions=list(assumptions),
        selection=selection,
        metadata={"deterministic_scenario": True},
    )


def default_scenarios() -> list[DecisionScenario]:
    return [
        build_decision_scenario(
            "conservative",
            "Conservative",
            description=(
                "Penalize generated-only hypotheses, weak structure context, uncalibrated "
                "models, and high-risk annotations."
            ),
            assumptions=["Prefer robust source-backed candidates under uncertainty."],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["conservative"],
        ),
        build_decision_scenario(
            "exploration",
            "Exploration",
            description="Reward uncertainty and diversity more strongly.",
            assumptions=["Next decisions prioritize information gain under review controls."],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["exploration"],
        ),
        build_decision_scenario(
            "exploit",
            "Exploit",
            description="Prioritize existing evidence and direct experimental support.",
            assumptions=["Known source-backed support is preferred over exploration."],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["exploit"],
        ),
        build_decision_scenario(
            "safety_first",
            "Safety-first risk triage",
            description="Strongly penalize safety and developability risk annotations.",
            assumptions=["Risk concentration should dominate ranking decisions."],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["safety_first"],
            constraint_overrides={"exclude_high_risk": True},
        ),
        build_decision_scenario(
            "budget_limited",
            "Budget-limited",
            description="Maximize portfolio value under limited assay and review slots.",
            assumptions=["Assay and review slots are constrained resources."],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["budget_limited"],
            budget_overrides={"max_assay_slots": 2, "max_review_hours": 2.0},
        ),
        build_decision_scenario(
            "generated_discovery",
            "Generated discovery",
            description="Allow more generated hypotheses while requiring review gates.",
            assumptions=[
                "Generated molecules remain computational hypotheses until linked evidence exists."
            ],
            objective_overrides=SCENARIO_WEIGHT_MULTIPLIERS["generated_discovery"],
            constraint_overrides={
                "max_generated_fraction": 0.8,
                "require_review_approval_for_generated": True,
            },
        ),
    ]


def compare_decision_scenarios(
    portfolio: Portfolio,
    scenarios: Sequence[DecisionScenario] | None = None,
    *,
    algorithm: str = "greedy",
    random_seed: int = 0,
) -> SensitivityAnalysis:
    scenario_inputs = list(scenarios or default_scenarios())
    completed: list[DecisionScenario] = []
    comparison_table: list[dict[str, Any]] = []
    selected_by_scenario: dict[str, list[str]] = {}
    objective_sensitivities: dict[str, dict[str, float]] = {}

    for scenario in scenario_inputs:
        scenario_portfolio = apply_scenario_to_portfolio(portfolio, scenario)
        run = PortfolioOptimizer(algorithm=algorithm, random_seed=random_seed).optimize(
            scenario_portfolio
        )
        selection = run.selections[0]
        selected_ids = list(selection.selected_candidate_ids)
        selected_by_scenario[scenario.scenario_id] = selected_ids
        completed_scenario = scenario.model_copy(
            update={
                "selection": selection,
                "metadata": {
                    **scenario.metadata,
                    "effective_algorithm": run.algorithm,
                    "deterministic_validation": run.metadata.get("deterministic_selection", False),
                },
            },
            deep=True,
        )
        completed.append(completed_scenario)
        comparison_table.append(
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "selected_candidate_ids": selected_ids,
                "portfolio_score": selection.portfolio_score,
                "constraint_violation_count": len(selection.constraint_violations),
                "warnings": list(selection.warnings),
            }
        )
        for objective in scenario_portfolio.objectives:
            objective_sensitivities.setdefault(objective.objective_id, {})[scenario.scenario_id] = (
                round(objective.weight, 3)
            )

    robust_candidate_ids = _robust_candidates(selected_by_scenario)
    selected_counter = Counter(
        candidate_id for ids in selected_by_scenario.values() for candidate_id in ids
    )
    fragile_candidate_ids = sorted(
        candidate_id for candidate_id, count in selected_counter.items() if count == 1
    )
    return SensitivityAnalysis(
        baseline_selection_id=completed[0].selection.selection_id
        if completed and completed[0].selection
        else None,
        scenarios=completed,
        robust_candidate_ids=robust_candidate_ids,
        fragile_candidate_ids=fragile_candidate_ids,
        objective_sensitivities=objective_sensitivities,
        metadata={
            "scenario_comparison_table": comparison_table,
            "scenario_specific_selected_candidates": selected_by_scenario,
            "deterministic_scenario_analysis": True,
            "random_seed": random_seed,
        },
    )


def apply_scenario_to_portfolio(
    portfolio: Portfolio,
    scenario: DecisionScenario,
) -> Portfolio:
    objectives = _scenario_objectives(portfolio.objectives or default_objectives(), scenario)
    constraints = _scenario_constraints(portfolio.constraints or default_constraints(), scenario)
    budget = _scenario_budget(portfolio.budget, scenario)
    candidates = [_scenario_candidate(candidate, scenario) for candidate in portfolio.candidates]
    return portfolio.model_copy(
        update={
            "portfolio_id": f"{portfolio.portfolio_id}-{scenario.scenario_id}",
            "candidates": candidates,
            "objectives": objectives,
            "constraints": constraints,
            "budget": budget,
            "metadata": {
                **portfolio.metadata,
                "scenario_id": scenario.scenario_id,
                "algorithm": portfolio.metadata.get("algorithm", "greedy"),
            },
        },
        deep=True,
    )


def _scenario_objectives(
    objectives: Sequence[PortfolioObjective],
    scenario: DecisionScenario,
) -> list[PortfolioObjective]:
    overrides = {
        **SCENARIO_WEIGHT_MULTIPLIERS.get(scenario.scenario_id, {}),
        **{
            str(key): float(value)
            for key, value in scenario.objective_overrides.items()
            if isinstance(value, int | float)
        },
    }
    adjusted = []
    for objective in objectives:
        multiplier = overrides.get(
            objective.objective_id, overrides.get(objective.metric_name, 1.0)
        )
        adjusted.append(
            objective.model_copy(
                update={
                    "weight": round(objective.weight * multiplier, 4),
                    "metadata": {
                        **objective.metadata,
                        "scenario_weight_multiplier": multiplier,
                    },
                },
                deep=True,
            )
        )
    return adjusted


def _scenario_constraints(
    constraints: Sequence[PortfolioConstraint],
    scenario: DecisionScenario,
) -> list[PortfolioConstraint]:
    adjusted = [constraint.model_copy(deep=True) for constraint in constraints]
    overrides = scenario.constraint_overrides
    if "max_generated_fraction" in overrides:
        adjusted.append(
            _constraint(
                "scenario-max-generated-fraction",
                "max_generated_fraction",
                overrides["max_generated_fraction"],
                hard=False,
                action="penalize",
            )
        )
    if overrides.get("require_review_approval_for_generated"):
        adjusted.append(
            _constraint(
                "scenario-require-generated-review",
                "require_review_approval_for_generated",
                True,
                hard=True,
                action="reject",
            )
        )
    return adjusted


def _scenario_budget(budget: ResourceBudget, scenario: DecisionScenario) -> ResourceBudget:
    updates = {
        key: value
        for key, value in scenario.budget_overrides.items()
        if key in ResourceBudget.model_fields
    }
    if not updates:
        return budget.model_copy(deep=True)
    return budget.model_copy(update=updates, deep=True)


def _scenario_candidate(
    candidate: PortfolioCandidate,
    scenario: DecisionScenario,
) -> PortfolioCandidate:
    if scenario.scenario_id == "conservative":
        return _conservative_candidate(candidate)
    if scenario.scenario_id == "safety_first":
        return _safety_first_candidate(candidate)
    if scenario.scenario_id == "exploration":
        return _exploration_candidate(candidate)
    return candidate.model_copy(deep=True)


def _conservative_candidate(candidate: PortfolioCandidate) -> PortfolioCandidate:
    sources = candidate_uncertainty_sources(candidate)
    penalty = 1.0
    if candidate.generated_without_direct_evidence:
        penalty *= 0.55
    if sources.get("weak_structure_context", 0.0) >= 0.5:
        penalty *= 0.75
    if sources.get("uncalibrated_model_prediction", 0.0) > 0:
        penalty *= 0.8
    if candidate.risk_flags or candidate.blocking_risks:
        penalty *= 0.65
    return candidate.model_copy(
        update={
            "generation_score": _scale(candidate.generation_score, penalty),
            "predictive_model_score": _scale(candidate.predictive_model_score, penalty),
            "experiment_readiness_score": _scale(candidate.experiment_readiness_score, penalty),
            "metadata": {
                **candidate.metadata,
                "scenario_adjustment": "conservative_uncertainty_penalty",
                "scenario_uncertainty_sources": sources,
            },
        },
        deep=True,
    )


def _safety_first_candidate(candidate: PortfolioCandidate) -> PortfolioCandidate:
    risk_text = " ".join([*candidate.risk_flags, *candidate.blocking_risks]).lower()
    high_risk = any(
        token in risk_text
        for token in ("toxicity", "toxic", "developability", "critical", "liability", "alert")
    )
    if not high_risk:
        return candidate.model_copy(deep=True)
    return candidate.model_copy(
        update={
            "blocking_risks": sorted({*candidate.blocking_risks, "scenario_risk_review"}),
            "experiment_readiness_score": _scale(candidate.experiment_readiness_score, 0.25),
            "metadata": {
                **candidate.metadata,
                "scenario_adjustment": "safety_first_risk_rejection",
            },
        },
        deep=True,
    )


def _exploration_candidate(candidate: PortfolioCandidate) -> PortfolioCandidate:
    sources = candidate_uncertainty_sources(candidate)
    uncertainty = min(
        1.0, max(candidate.uncertainty_score or 0.0, sum(sources.values()) / len(sources))
    )
    return candidate.model_copy(
        update={
            "uncertainty_score": round(uncertainty, 3),
            "metadata": {
                **candidate.metadata,
                "scenario_adjustment": "exploration_uncertainty_reward",
                "scenario_uncertainty_sources": sources,
            },
        },
        deep=True,
    )


def _constraint(
    constraint_id: str,
    constraint_type: str,
    value: Any,
    *,
    hard: bool,
    action: str,
) -> PortfolioConstraint:
    return PortfolioConstraint(
        constraint_id=constraint_id,
        name=constraint_id.replace("-", " ").title(),
        constraint_type=constraint_type,
        value=value,
        hard=hard,
        violation_action=action,  # type: ignore[arg-type]
        description="Deterministic scenario constraint for portfolio prioritization.",
    )


def _robust_candidates(selected_by_scenario: Mapping[str, list[str]]) -> list[str]:
    if not selected_by_scenario:
        return []
    selected_sets = [set(ids) for ids in selected_by_scenario.values()]
    return sorted(set.intersection(*selected_sets)) if selected_sets else []


def _scale(value: float | None, multiplier: float) -> float | None:
    if value is None:
        return None
    return round(min(1.0, max(0.0, value * multiplier)), 3)
