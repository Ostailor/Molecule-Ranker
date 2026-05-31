from __future__ import annotations

from molecule_ranker.portfolio.objectives import default_objectives
from molecule_ranker.portfolio.optimizer import PortfolioOptimizer
from molecule_ranker.portfolio.schemas import (
    Portfolio,
    PortfolioCandidate,
    PortfolioObjective,
    Program,
    ResourceBudget,
)


def _candidate(
    candidate_id: str,
    *,
    target: str,
    series: str,
    evidence: float,
    readiness: float,
    developability: float = 0.8,
    uncertainty: float = 0.4,
    blocking: bool = False,
) -> PortfolioCandidate:
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin="existing",
        target_symbols=[target],
        mechanism_label=f"{target} modulation",
        chemical_series_id=series,
        scaffold_id=series,
        evidence_score=evidence,
        developability_score=developability,
        experiment_readiness_score=readiness,
        uncertainty_score=uncertainty,
        diversity_features={},
        risk_flags=["critical"] if blocking else [],
        blocking_risks=["critical_or_rejected"] if blocking else [],
    )


def _portfolio(
    candidates: list[PortfolioCandidate],
    *,
    algorithm: str,
    objectives: list[PortfolioObjective] | None = None,
    max_candidates: int = 2,
) -> Portfolio:
    return Portfolio(
        portfolio_id=f"portfolio-{algorithm}",
        program=Program(program_id=f"program-{algorithm}", name="Program"),
        candidates=candidates,
        objectives=objectives or default_objectives(),
        constraints=[],
        budget=ResourceBudget(max_candidates=max_candidates),
        metadata={"algorithm": algorithm, "random_seed": 123},
    )


def test_greedy_selects_by_marginal_portfolio_value_under_constraints() -> None:
    portfolio = _portfolio(
        [
            _candidate("a", target="T1", series="series-a", evidence=0.92, readiness=0.9),
            _candidate("b", target="T1", series="series-a", evidence=0.9, readiness=0.88),
            _candidate("c", target="T2", series="series-b", evidence=0.84, readiness=0.84),
            _candidate(
                "blocked",
                target="T3",
                series="series-c",
                evidence=0.99,
                readiness=0.99,
                blocking=True,
            ),
        ],
        algorithm="greedy",
    )

    run = PortfolioOptimizer(random_seed=123).optimize(portfolio)
    selection = run.selections[0]

    assert run.algorithm == "greedy"
    assert selection.selected_candidate_ids == ["a", "c"]
    explanations = selection.metadata["candidate_explanations"]
    assert explanations["a"]["decision"] == "selected"
    assert explanations["blocked"]["decision"] == "rejected"
    assert "deterministic greedy" in explanations["a"]["rationale"]
    assert run.metadata["codex_generated_outputs"] is False


def test_weighted_sum_ranks_by_weighted_objective_score_then_applies_constraints() -> None:
    portfolio = _portfolio(
        [
            _candidate("a", target="T1", series="series-a", evidence=0.92, readiness=0.9),
            _candidate("b", target="T1", series="series-a", evidence=0.9, readiness=0.88),
            _candidate("c", target="T2", series="series-b", evidence=0.84, readiness=0.84),
        ],
        algorithm="weighted_sum",
    )

    selection = PortfolioOptimizer().optimize(portfolio).selections[0]

    assert selection.selected_candidate_ids == ["a", "b"]
    assert selection.metadata["algorithm"] == "weighted_sum"
    assert selection.metadata["algorithm_details"]["strategy"] == "weighted_objective_ranking"


def test_pareto_computes_non_dominated_front_summary() -> None:
    objectives = [
        PortfolioObjective(
            objective_id="evidence",
            name="Evidence",
            objective_type="maximize",
            metric_name="evidence_score",
            weight=0.5,
            direction="higher_is_better",
            hard=False,
            description="Evidence support score.",
        ),
        PortfolioObjective(
            objective_id="readiness",
            name="Readiness",
            objective_type="maximize",
            metric_name="experiment_readiness_score",
            weight=0.5,
            direction="higher_is_better",
            hard=False,
            description="Readiness score.",
        ),
    ]
    portfolio = _portfolio(
        [
            _candidate(
                "high-evidence", target="T1", series="series-a", evidence=0.95, readiness=0.55
            ),
            _candidate(
                "high-readiness", target="T2", series="series-b", evidence=0.55, readiness=0.95
            ),
            _candidate("dominated", target="T3", series="series-c", evidence=0.4, readiness=0.4),
        ],
        algorithm="pareto",
        objectives=objectives,
    )

    run = PortfolioOptimizer().optimize(portfolio)
    details = run.metadata["algorithm_details"]

    assert run.algorithm == "pareto"
    assert details["strategy"] == "pareto_non_dominated_front"
    assert details["pareto_front_candidate_ids"] == ["high-evidence", "high-readiness"]
    assert details["pareto_front_selections"]
    assert "dominated" not in run.selections[0].selected_candidate_ids


def test_integer_programming_optional_falls_back_to_greedy_without_solver(monkeypatch) -> None:
    import molecule_ranker.portfolio.optimizer as optimizer_module

    monkeypatch.setattr(optimizer_module, "_optional_solver_backend", lambda: None)
    portfolio = _portfolio(
        [
            _candidate("a", target="T1", series="series-a", evidence=0.9, readiness=0.9),
            _candidate("b", target="T2", series="series-b", evidence=0.8, readiness=0.8),
        ],
        algorithm="integer_programming_optional",
    )

    run = PortfolioOptimizer(algorithm="integer_programming_optional").optimize(portfolio)

    assert run.algorithm == "greedy"
    assert run.metadata["requested_algorithm"] == "integer_programming_optional"
    assert run.metadata["algorithm_details"]["fallback"] == "greedy"
    assert "fell back to deterministic greedy" in run.warnings[-1]
