from __future__ import annotations

import importlib.util
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.generation.schemas import GeneratedMolecule
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate

from .candidate_builder import build_portfolio_candidates
from .constraints import (
    collect_constraint_violations,
    constraint_allows_candidate,
    default_constraints,
    group_constraints,
)
from .diversity import (
    correlated_risk_penalty,
    diversity_bonus,
    summarize_diversity,
    target_coverage,
)
from .objectives import (
    aggregate_objective_scores,
    candidate_objective_score,
    default_objectives,
    explain_objectives,
)
from .risk import risk_score, summarize_risk
from .schemas import (
    Portfolio,
    PortfolioCandidate,
    PortfolioConstraint,
    PortfolioObjective,
    PortfolioOptimizationRun,
    PortfolioSelection,
    Program,
    ProgramDecisionMemo,
    ResourceBudget,
)
from .stage_gates import build_stage_gate
from .uncertainty import summarize_uncertainty

INTEGRITY_CONSTRAINTS = [
    "Portfolio recommendations are research prioritization aids, not clinical or "
    "experimental instructions.",
    "No lab protocols, synthesis instructions, dosing, or patient treatment guidance "
    "are generated.",
    "Selections are deterministic analytics outputs and do not claim molecules are "
    "safe, active, effective, or synthesizable.",
    "Generated molecules remain computational hypotheses unless exact imported "
    "experimental evidence exists.",
    "Codex may explain tradeoffs only after deterministic selection and score validation.",
]

SUPPORTED_ALGORITHMS = {
    "greedy",
    "weighted_sum",
    "pareto",
    "integer_programming_optional",
}


@dataclass(frozen=True)
class _AlgorithmResult:
    selected_ids: list[str]
    effective_algorithm: str
    metadata: dict[str, object]
    warnings: list[str]


def optimize_portfolio(
    *,
    program: Program,
    existing_candidates: Sequence[MoleculeCandidate] = (),
    generated_molecules: Sequence[GeneratedMolecule | GeneratedMoleculeHypothesis] = (),
    experimental_results: Sequence[AssayResult] = (),
    budget: ResourceBudget | None = None,
    objectives: Sequence[PortfolioObjective] | None = None,
    constraints: Sequence[PortfolioConstraint] | None = None,
    algorithm: str = "greedy",
    random_seed: int = 0,
) -> PortfolioOptimizationRun:
    candidates = build_portfolio_candidates(
        existing_candidates=existing_candidates,
        generated_molecules=generated_molecules,
        experimental_results=experimental_results,
        disease_name=program.disease_focus[0] if program.disease_focus else None,
    )
    portfolio = Portfolio(
        portfolio_id=_stable_id("portfolio", program.program_id, len(candidates)),
        program=program,
        candidates=candidates,
        objectives=list(objectives or default_objectives()),
        constraints=list(constraints or default_constraints()),
        budget=budget or ResourceBudget(),
        metadata={
            "algorithm": algorithm,
            "random_seed": random_seed,
            "deterministic_validation": True,
            "codex_generated_selection": False,
        },
    )
    return PortfolioOptimizer(algorithm=algorithm, random_seed=random_seed).optimize(portfolio)


class PortfolioOptimizer:
    """Deterministic optimizer for V1.4 program-level decisions."""

    def __init__(
        self,
        *,
        algorithm: str = "greedy",
        random_seed: int = 0,
        allow_solver_fallback: bool = True,
    ) -> None:
        self.algorithm = algorithm
        self.random_seed = random_seed
        self.allow_solver_fallback = allow_solver_fallback

    def optimize(
        self,
        portfolio: Portfolio,
        *,
        algorithm: str | None = None,
        random_seed: int | None = None,
    ) -> PortfolioOptimizationRun:
        requested_algorithm = str(
            algorithm or portfolio.metadata.get("algorithm") or self.algorithm
        )
        if requested_algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported portfolio optimization algorithm: {requested_algorithm}")
        active_seed = int(random_seed if random_seed is not None else self.random_seed)
        result = self._run_algorithm(portfolio, requested_algorithm)
        selected_ids = result.selected_ids
        selected = _candidates_by_id(portfolio.candidates, selected_ids)
        rejected = self._rejected_candidate_ids(portfolio.candidates)
        deferred = [
            candidate.portfolio_candidate_id
            for candidate in portfolio.candidates
            if candidate.portfolio_candidate_id not in set(selected_ids + rejected)
        ]
        selection = self._selection(
            portfolio,
            selected=selected,
            rejected_candidate_ids=rejected,
            deferred_candidate_ids=deferred,
            algorithm=result.effective_algorithm,
            algorithm_metadata=result.metadata,
            random_seed=active_seed,
        )
        memo = self._decision_memo(portfolio, selection)
        return PortfolioOptimizationRun(
            optimization_run_id=_stable_id(
                "portfolio-run",
                portfolio.portfolio_id,
                len(portfolio.candidates),
            ),
            program_id=portfolio.program.program_id,
            project_id=portfolio.metadata.get("project_id"),
            disease_name=portfolio.program.disease_focus[0]
            if portfolio.program.disease_focus
            else None,
            input_candidate_count=len(portfolio.candidates),
            objectives=portfolio.objectives,
            constraints=portfolio.constraints,
            budget=portfolio.budget,
            algorithm=result.effective_algorithm,  # type: ignore[arg-type]
            status="succeeded",
            selections=[selection],
            recommended_selection_id=selection.selection_id,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            warnings=[
                "Portfolio optimization is a research prioritization aid only.",
                "No selected molecule is claimed safe, active, effective, or synthesizable.",
                "Optimization outputs were computed deterministically from supplied artifacts.",
                *result.warnings,
            ],
            metadata={
                "deterministic_module_version": "portfolio_optimizer.v1.4.0",
                "deterministic_selection": True,
                "codex_generated_outputs": False,
                "requested_algorithm": requested_algorithm,
                "effective_algorithm": result.effective_algorithm,
                "random_seed": active_seed,
                "algorithm_details": result.metadata,
                "program": portfolio.program.model_dump(mode="json"),
                "input_candidates": [
                    candidate.model_dump(mode="json") for candidate in portfolio.candidates
                ],
                "decision_memo": memo.model_dump(mode="json"),
                "stage_gates": [
                    build_stage_gate(
                        candidate, from_stage="candidate", to_stage="portfolio"
                    ).model_dump(mode="json")
                    for candidate in selected
                ],
                "scientific_integrity_constraints": list(INTEGRITY_CONSTRAINTS),
            },
        )

    def _run_algorithm(self, portfolio: Portfolio, algorithm: str) -> _AlgorithmResult:
        if algorithm == "greedy":
            return _AlgorithmResult(
                selected_ids=self._select_greedy(portfolio),
                effective_algorithm="greedy",
                metadata={"strategy": "marginal_portfolio_value"},
                warnings=[],
            )
        if algorithm == "weighted_sum":
            return _AlgorithmResult(
                selected_ids=self._select_weighted_sum(portfolio),
                effective_algorithm="weighted_sum",
                metadata={"strategy": "weighted_objective_ranking"},
                warnings=[],
            )
        if algorithm == "pareto":
            selected_ids, metadata = self._select_pareto(portfolio)
            return _AlgorithmResult(
                selected_ids=selected_ids,
                effective_algorithm="pareto",
                metadata=metadata,
                warnings=[],
            )
        return self._select_integer_programming_optional(portfolio)

    def _select_greedy(self, portfolio: Portfolio) -> list[str]:
        max_count = _max_count(portfolio)
        selected: list[PortfolioCandidate] = []
        remaining = list(portfolio.candidates)
        while remaining and len(selected) < max_count:
            ranked = sorted(
                remaining,
                key=lambda candidate: (
                    self._marginal_portfolio_value(candidate, selected, portfolio),
                    candidate.uncertainty_score or 0.0,
                    candidate.portfolio_candidate_id,
                ),
                reverse=True,
            )
            added = False
            for candidate in ranked:
                remaining.remove(candidate)
                if not self._can_add_candidate(candidate, selected, portfolio, max_count=max_count):
                    continue
                selected.append(candidate)
                added = True
                break
            if not added:
                break
        return [candidate.portfolio_candidate_id for candidate in selected]

    def _select_weighted_sum(self, portfolio: Portfolio) -> list[str]:
        ranked = sorted(
            portfolio.candidates,
            key=lambda candidate: (
                candidate_objective_score(candidate, portfolio.objectives),
                candidate.uncertainty_score or 0.0,
                candidate.portfolio_candidate_id,
            ),
            reverse=True,
        )
        return [
            candidate.portfolio_candidate_id
            for candidate in self._build_ranked_selection(ranked, portfolio)
        ]

    def _select_pareto(self, portfolio: Portfolio) -> tuple[list[str], dict[str, object]]:
        front = self._pareto_front_candidates(portfolio.candidates, portfolio.objectives)
        selected = self._build_ranked_selection(
            sorted(
                front,
                key=lambda candidate: (
                    self._marginal_portfolio_value(candidate, [], portfolio),
                    candidate.portfolio_candidate_id,
                ),
                reverse=True,
            ),
            portfolio,
        )
        metadata: dict[str, object] = {
            "strategy": "pareto_non_dominated_front",
            "pareto_front_candidate_ids": [candidate.portfolio_candidate_id for candidate in front],
            "candidate_objective_vectors": {
                candidate.portfolio_candidate_id: self._candidate_objective_vector(
                    candidate, portfolio.objectives
                )
                for candidate in portfolio.candidates
            },
            "pareto_front_selections": self._pareto_selection_summaries(front, portfolio),
        }
        return [candidate.portfolio_candidate_id for candidate in selected], metadata

    def _select_integer_programming_optional(self, portfolio: Portfolio) -> _AlgorithmResult:
        solver_backend = _optional_solver_backend()
        if solver_backend is None:
            if not self.allow_solver_fallback:
                raise RuntimeError(
                    "integer_programming_optional requested but no optional solver is installed"
                )
            return _AlgorithmResult(
                selected_ids=self._select_greedy(portfolio),
                effective_algorithm="greedy",
                metadata={
                    "strategy": "integer_programming_optional",
                    "fallback": "greedy",
                    "fallback_reason": "optional_solver_unavailable",
                },
                warnings=[
                    "integer_programming_optional fell back to deterministic greedy because "
                    "no optional solver dependency was installed."
                ],
            )
        selected = self._select_exhaustive_weighted(portfolio)
        return _AlgorithmResult(
            selected_ids=[candidate.portfolio_candidate_id for candidate in selected],
            effective_algorithm="integer_programming_optional",
            metadata={
                "strategy": "integer_programming_optional",
                "solver_backend": solver_backend,
                "deterministic_validation": True,
            },
            warnings=[],
        )

    def _build_ranked_selection(
        self,
        ranked: Sequence[PortfolioCandidate],
        portfolio: Portfolio,
    ) -> list[PortfolioCandidate]:
        max_count = _max_count(portfolio)
        selected: list[PortfolioCandidate] = []
        for candidate in ranked:
            if len(selected) >= max_count:
                break
            if self._can_add_candidate(candidate, selected, portfolio, max_count=max_count):
                selected.append(candidate)
        return selected

    def _select_exhaustive_weighted(self, portfolio: Portfolio) -> list[PortfolioCandidate]:
        candidates = sorted(
            portfolio.candidates, key=lambda candidate: candidate.portfolio_candidate_id
        )
        max_count = min(_max_count(portfolio), len(candidates), 16)
        best: tuple[float, tuple[str, ...], tuple[PortfolioCandidate, ...]] = (
            -1.0,
            (),
            (),
        )
        for size in range(1, max_count + 1):
            for subset in combinations(candidates, size):
                if not self._selection_is_feasible(subset, portfolio):
                    continue
                score = self._portfolio_weighted_score(subset, portfolio.objectives)
                tie_break = tuple(candidate.portfolio_candidate_id for candidate in subset)
                if score > best[0] or (score == best[0] and tie_break < best[1]):
                    best = (score, tie_break, subset)
        return list(best[2])

    def _selection_is_feasible(
        self,
        candidates: Sequence[PortfolioCandidate],
        portfolio: Portfolio,
    ) -> bool:
        selected: list[PortfolioCandidate] = []
        max_count = _max_count(portfolio)
        for candidate in candidates:
            if not self._can_add_candidate(candidate, selected, portfolio, max_count=max_count):
                return False
            selected.append(candidate)
        return True

    def _can_add_candidate(
        self,
        candidate: PortfolioCandidate,
        selected: Sequence[PortfolioCandidate],
        portfolio: Portfolio,
        *,
        max_count: int,
    ) -> bool:
        score = candidate_objective_score(candidate, portfolio.objectives)
        candidate_risk_score = risk_score(
            risk_flags=candidate.risk_flags,
            blocking_risks=candidate.blocking_risks,
            developability_score=candidate.developability_score,
        )
        if score < 0.25 or candidate_risk_score >= 0.85:
            return False
        target_counts: Counter[str] = Counter()
        series_counts: Counter[str] = Counter()
        generated_count = 0
        for selected_candidate in selected:
            for target in selected_candidate.target_symbols or ["unspecified"]:
                target_counts[target] += 1
            series_counts[selected_candidate.chemical_series_id or "unspecified"] += 1
            generated_count += int(selected_candidate.origin == "generated")
        return constraint_allows_candidate(
            candidate,
            selected_count=len(selected),
            generated_count=generated_count,
            target_counts=target_counts,
            series_counts=series_counts,
            constraints=group_constraints(portfolio.constraints),
            max_count=max_count,
            selected_candidates=selected,
            budget=portfolio.budget,
        )

    def _marginal_portfolio_value(
        self,
        candidate: PortfolioCandidate,
        selected: Sequence[PortfolioCandidate],
        portfolio: Portfolio,
    ) -> float:
        return (
            candidate_objective_score(candidate, portfolio.objectives)
            + diversity_bonus(candidate, selected)
            - correlated_risk_penalty(candidate, selected)
        )

    def _candidate_objective_vector(
        self,
        candidate: PortfolioCandidate,
        objectives: Sequence[PortfolioObjective],
    ) -> dict[str, float]:
        return {
            objective.objective_id: candidate_objective_score(candidate, [objective])
            for objective in objectives
        }

    def _pareto_front_candidates(
        self,
        candidates: Sequence[PortfolioCandidate],
        objectives: Sequence[PortfolioObjective],
    ) -> list[PortfolioCandidate]:
        vectors = {
            candidate.portfolio_candidate_id: self._candidate_objective_vector(
                candidate, objectives
            )
            for candidate in candidates
        }
        front: list[PortfolioCandidate] = []
        for candidate in candidates:
            candidate_vector = vectors[candidate.portfolio_candidate_id]
            dominated = False
            for other in candidates:
                if other.portfolio_candidate_id == candidate.portfolio_candidate_id:
                    continue
                other_vector = vectors[other.portfolio_candidate_id]
                if _dominates(other_vector, candidate_vector):
                    dominated = True
                    break
            if not dominated:
                front.append(candidate)
        return sorted(front, key=lambda candidate: candidate.portfolio_candidate_id)

    def _pareto_selection_summaries(
        self,
        front: Sequence[PortfolioCandidate],
        portfolio: Portfolio,
    ) -> list[dict[str, object]]:
        front = list(front)[:10]
        max_count = min(_max_count(portfolio), len(front), 4)
        summaries: list[dict[str, object]] = []
        for size in range(1, max_count + 1):
            for subset in combinations(front, size):
                if not self._selection_is_feasible(subset, portfolio):
                    continue
                objective_scores = aggregate_objective_scores(subset, portfolio.objectives)
                summaries.append(
                    {
                        "candidate_ids": [candidate.portfolio_candidate_id for candidate in subset],
                        "objective_scores": objective_scores,
                        "portfolio_score": self._portfolio_weighted_score(
                            subset, portfolio.objectives
                        ),
                    }
                )
        return _non_dominated_selection_summaries(summaries)[:20]

    def _portfolio_weighted_score(
        self,
        candidates: Sequence[PortfolioCandidate],
        objectives: Sequence[PortfolioObjective],
    ) -> float:
        if not candidates:
            return 0.0
        total_weight = sum(objective.weight for objective in objectives)
        if total_weight <= 0:
            return 0.0
        aggregate = aggregate_objective_scores(candidates, objectives)
        score = (
            sum(
                aggregate.get(objective.objective_id, 0.0) * objective.weight
                for objective in objectives
            )
            / total_weight
        )
        return round(min(1.0, max(0.0, score)), 3)

    def _rejected_candidate_ids(
        self,
        candidates: Sequence[PortfolioCandidate],
    ) -> list[str]:
        return sorted(
            candidate.portfolio_candidate_id for candidate in candidates if candidate.blocking_risks
        )

    def _selection(
        self,
        portfolio: Portfolio,
        *,
        selected: Sequence[PortfolioCandidate],
        rejected_candidate_ids: list[str],
        deferred_candidate_ids: list[str],
        algorithm: str,
        algorithm_metadata: dict[str, object],
        random_seed: int,
    ) -> PortfolioSelection:
        portfolio_score = (
            sum(
                candidate_objective_score(candidate, portfolio.objectives) for candidate in selected
            )
            / len(selected)
            if selected
            else 0.0
        )
        selected_ids = [candidate.portfolio_candidate_id for candidate in selected]
        warnings = ["Research prioritization aid only; not a clinical or experimental instruction."]
        if any(candidate.generated_without_direct_evidence for candidate in selected):
            warnings.append(
                "Selected generated molecules remain computational hypotheses without "
                "direct evidence."
            )
        return PortfolioSelection(
            selection_id=_stable_id("selection", portfolio.portfolio_id, algorithm, "recommended"),
            selected_candidate_ids=selected_ids,
            rejected_candidate_ids=rejected_candidate_ids,
            deferred_candidate_ids=deferred_candidate_ids,
            objective_scores=aggregate_objective_scores(selected, portfolio.objectives),
            constraint_violations=collect_constraint_violations(
                selected, portfolio.constraints, portfolio.budget
            ),
            portfolio_score=round(min(1.0, max(0.0, portfolio_score)), 3),
            diversity_summary=summarize_diversity(selected),
            risk_summary=summarize_risk(selected),
            uncertainty_summary=summarize_uncertainty(selected),
            target_coverage=target_coverage(selected),
            rationale=(
                f"Deterministic {algorithm} selection used validated objective scores, "
                "marginal portfolio contributions, resource limits, and hard constraints."
            ),
            warnings=warnings,
            metadata={
                "deterministic_selection": True,
                "algorithm": algorithm,
                "algorithm_details": algorithm_metadata,
                "random_seed": random_seed,
                "objective_explanations": explain_objectives(selected, portfolio.objectives),
                "candidate_explanations": self._candidate_explanations(
                    portfolio,
                    selected=selected,
                    rejected_candidate_ids=rejected_candidate_ids,
                    deferred_candidate_ids=deferred_candidate_ids,
                    algorithm=algorithm,
                ),
                "human_approval_required": any(
                    candidate.origin == "generated" or candidate.risk_flags
                    for candidate in selected
                ),
            },
        )

    def _decision_memo(
        self,
        portfolio: Portfolio,
        selection: PortfolioSelection,
    ) -> ProgramDecisionMemo:
        return ProgramDecisionMemo(
            memo_id=_stable_id("memo", portfolio.program.program_id, selection.selection_id),
            program_id=portfolio.program.program_id,
            optimization_run_id=_stable_id("portfolio-run", portfolio.portfolio_id),
            title=f"{portfolio.program.name} portfolio decision memo",
            executive_summary=(
                "Deterministic V1.4 portfolio analytics selected candidates for "
                "research prioritization. Outputs are not clinical, lab, synthesis, "
                "dosing, or patient-treatment instructions."
            ),
            selected_portfolio_summary=", ".join(selection.selected_candidate_ids)
            or "No candidates selected.",
            key_tradeoffs=[
                "Balanced evidence support, generated-hypothesis value, developability, "
                "readiness, uncertainty, and novelty.",
                "Constrained over-concentration by target and chemical series where possible.",
            ],
            key_risks=[
                "Generated molecules remain computational hypotheses unless exact imported "
                "experimental evidence exists.",
                "Correlated risk flags require human review before action.",
            ],
            uncertainty_notes=[
                "Uncertainty scores guide learning-value prioritization and are not assay outcomes."
            ],
            recommended_next_actions=[
                "Route candidates requiring approval through human review.",
                "Use deterministic assay-triage and review workflows before any downstream action.",
            ],
            human_approval_required=bool(selection.metadata.get("human_approval_required")),
            limitations=list(INTEGRITY_CONSTRAINTS),
            created_at=datetime.now(UTC),
            metadata={"selection_id": selection.selection_id},
        )

    def _candidate_explanations(
        self,
        portfolio: Portfolio,
        *,
        selected: Sequence[PortfolioCandidate],
        rejected_candidate_ids: Sequence[str],
        deferred_candidate_ids: Sequence[str],
        algorithm: str,
    ) -> dict[str, dict[str, object]]:
        explanations: dict[str, dict[str, object]] = {}
        selected_ids = {candidate.portfolio_candidate_id for candidate in selected}
        rejected = set(rejected_candidate_ids)
        deferred = set(deferred_candidate_ids)
        selected_so_far: list[PortfolioCandidate] = []
        for candidate in selected:
            explanations[candidate.portfolio_candidate_id] = {
                "decision": "selected",
                "rationale": (
                    f"Selected by deterministic {algorithm} after scoring and constraint "
                    "validation."
                ),
                "weighted_objective_score": candidate_objective_score(
                    candidate, portfolio.objectives
                ),
                "marginal_diversity_bonus": round(diversity_bonus(candidate, selected_so_far), 3),
                "marginal_correlated_risk_penalty": round(
                    correlated_risk_penalty(candidate, selected_so_far), 3
                ),
            }
            selected_so_far.append(candidate)
        for candidate in portfolio.candidates:
            if candidate.portfolio_candidate_id in selected_ids:
                continue
            score = candidate_objective_score(candidate, portfolio.objectives)
            candidate_risk_score = risk_score(
                risk_flags=candidate.risk_flags,
                blocking_risks=candidate.blocking_risks,
                developability_score=candidate.developability_score,
            )
            if candidate.portfolio_candidate_id in rejected:
                decision = "rejected"
                rationale = "Rejected by deterministic risk or blocking-risk checks."
            elif candidate.portfolio_candidate_id in deferred:
                decision = "deferred"
                rationale = (
                    "Deferred after deterministic ranking because higher marginal value "
                    "or resource-feasible candidates filled the selected portfolio."
                )
            else:
                decision = "not_selected"
                rationale = "Not selected by deterministic optimization."
            explanations[candidate.portfolio_candidate_id] = {
                "decision": decision,
                "rationale": rationale,
                "weighted_objective_score": score,
                "risk_score": round(candidate_risk_score, 3),
                "blocking_risks": list(candidate.blocking_risks),
            }
        return explanations


def _candidates_by_id(
    candidates: Sequence[PortfolioCandidate],
    candidate_ids: Sequence[str],
) -> list[PortfolioCandidate]:
    by_id = {candidate.portfolio_candidate_id: candidate for candidate in candidates}
    return [by_id[candidate_id] for candidate_id in candidate_ids if candidate_id in by_id]


def _max_count(portfolio: Portfolio) -> int:
    limits = [
        portfolio.budget.max_candidates
        if portfolio.budget.max_candidates is not None
        else len(portfolio.candidates)
    ]
    for constraint in group_constraints(portfolio.constraints).get("max_candidates", []):
        if constraint.hard and constraint.violation_action == "reject":
            limits.append(int(constraint.value))
    return max(0, min(int(limit) for limit in limits if limit is not None))


def _optional_solver_backend() -> str | None:
    for module_name in ("pulp", "ortools"):
        if importlib.util.find_spec(module_name) is not None:
            return module_name
    return None


def _dominates(candidate: dict[str, float], other: dict[str, float]) -> bool:
    common = sorted(set(candidate) & set(other))
    if not common:
        return False
    return all(candidate[key] >= other[key] for key in common) and any(
        candidate[key] > other[key] for key in common
    )


def _non_dominated_selection_summaries(
    summaries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    front: list[dict[str, object]] = []
    for summary in summaries:
        scores = summary.get("objective_scores")
        if not isinstance(scores, dict):
            continue
        normalized_scores = {
            str(key): float(value)
            for key, value in scores.items()
            if isinstance(value, int | float)
        }
        dominated = False
        for other in summaries:
            if other is summary:
                continue
            other_scores = other.get("objective_scores")
            if not isinstance(other_scores, dict):
                continue
            normalized_other = {
                str(key): float(value)
                for key, value in other_scores.items()
                if isinstance(value, int | float)
            }
            if _dominates(normalized_other, normalized_scores):
                dominated = True
                break
        if not dominated:
            front.append(summary)
    return sorted(front, key=_selection_summary_sort_key, reverse=True)


def _selection_summary_sort_key(summary: dict[str, object]) -> tuple[float, tuple[str, ...]]:
    raw_score = summary.get("portfolio_score", 0.0)
    score = float(raw_score) if isinstance(raw_score, int | float) else 0.0
    raw_candidate_ids = summary.get("candidate_ids", [])
    candidate_ids = (
        tuple(str(candidate_id) for candidate_id in raw_candidate_ids)
        if isinstance(raw_candidate_ids, list)
        else ()
    )
    return score, candidate_ids


def _stable_id(*parts: object) -> str:
    raw = "::".join(str(part) for part in parts)
    return str(uuid5(NAMESPACE_URL, f"molecule-ranker:v1.4:{raw}"))
