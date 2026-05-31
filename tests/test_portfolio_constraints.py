from __future__ import annotations

from collections import Counter

from molecule_ranker.portfolio.constraints import (
    collect_constraint_violations,
    constraint_allows_candidate,
    default_constraints,
    group_constraints,
)
from molecule_ranker.portfolio.objectives import default_objectives
from molecule_ranker.portfolio.optimizer import PortfolioOptimizer
from molecule_ranker.portfolio.schemas import (
    Portfolio,
    PortfolioCandidate,
    PortfolioConstraint,
    Program,
    ResourceBudget,
)


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    target: str = "T1",
    series: str = "series-a",
    score: float = 0.8,
    review_status: str | None = None,
    generated_without_direct_evidence: bool | None = None,
    risk_flags: list[str] | None = None,
    blocking_risks: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> PortfolioCandidate:
    generated = origin == "generated"
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        target_symbols=[target],
        chemical_series_id=series,
        scaffold_id=series,
        evidence_score=score if origin != "generated" else None,
        generation_score=score if origin == "generated" else None,
        developability_score=0.75,
        experiment_readiness_score=score,
        uncertainty_score=0.5,
        diversity_features={},
        risk_flags=list(risk_flags or []),
        blocking_risks=list(blocking_risks or []),
        review_status=review_status,
        direct_experimental_evidence=False,
        generated_without_direct_evidence=(
            generated
            if generated_without_direct_evidence is None
            else generated_without_direct_evidence
        ),
        metadata=dict(metadata or {}),
    )


def _constraint(
    constraint_type: str,
    value: object,
    *,
    hard: bool = True,
    action: str = "reject",
) -> PortfolioConstraint:
    return PortfolioConstraint(
        constraint_id=constraint_type,
        name=constraint_type,
        constraint_type=constraint_type,
        value=value,
        hard=hard,
        violation_action=action,  # type: ignore[arg-type]
        description="Research portfolio constraint for deterministic prioritization.",
    )


def _portfolio(
    candidates: list[PortfolioCandidate],
    *,
    constraints: list[PortfolioConstraint],
    budget: ResourceBudget | None = None,
) -> Portfolio:
    return Portfolio(
        portfolio_id="portfolio-constraints",
        program=Program(program_id="program-constraints", name="Program"),
        candidates=candidates,
        objectives=default_objectives(),
        constraints=constraints,
        budget=budget or ResourceBudget(max_candidates=len(candidates)),
    )


def test_max_candidates_enforced() -> None:
    portfolio = _portfolio(
        [
            _candidate("a", score=0.9),
            _candidate("b", score=0.8, target="T2", series="series-b"),
            _candidate("c", score=0.7, target="T3", series="series-c"),
        ],
        constraints=[_constraint("max_candidates", 2)],
        budget=ResourceBudget(max_candidates=5),
    )

    run = PortfolioOptimizer().optimize(portfolio)

    assert len(run.selections[0].selected_candidate_ids) == 2
    violations = collect_constraint_violations(
        portfolio.candidates,
        portfolio.constraints,
        portfolio.budget,
    )
    assert violations[0]["constraint_type"] == "max_candidates"
    assert violations[0]["hard"] is True


def test_generated_fraction_enforced() -> None:
    portfolio = _portfolio(
        [
            _candidate("existing", origin="existing", score=0.85),
            _candidate(
                "generated-a", origin="generated", target="T2", series="series-b", score=0.9
            ),
            _candidate(
                "generated-b", origin="generated", target="T3", series="series-c", score=0.88
            ),
        ],
        constraints=[_constraint("max_generated_fraction", 0.5)],
        budget=ResourceBudget(max_candidates=3),
    )

    selection = PortfolioOptimizer().optimize(portfolio).selections[0]
    selected = [
        candidate
        for candidate in portfolio.candidates
        if candidate.portfolio_candidate_id in selection.selected_candidate_ids
    ]
    generated_fraction = sum(
        candidate.generated_without_direct_evidence for candidate in selected
    ) / len(selected)

    assert generated_fraction <= 0.5


def test_critical_developability_risk_excluded_by_default() -> None:
    constraints = group_constraints(default_constraints())
    candidate = _candidate(
        "critical",
        risk_flags=["critical_developability_risk"],
        blocking_risks=["critical_developability_risk"],
    )

    allowed = constraint_allows_candidate(
        candidate,
        selected_count=0,
        generated_count=0,
        target_counts=Counter(),
        series_counts=Counter(),
        constraints=constraints,
        max_count=3,
    )

    assert allowed is False


def test_review_approval_required_for_generated_when_hard_constraint_enabled() -> None:
    constraints = group_constraints([_constraint("require_review_approval_for_generated", True)])
    unreviewed = _candidate("generated", origin="generated")
    reviewed = _candidate("reviewed", origin="generated", review_status="approved")

    assert (
        constraint_allows_candidate(
            unreviewed,
            selected_count=0,
            generated_count=0,
            target_counts=Counter(),
            series_counts=Counter(),
            constraints=constraints,
            max_count=3,
        )
        is False
    )
    assert (
        constraint_allows_candidate(
            reviewed,
            selected_count=0,
            generated_count=0,
            target_counts=Counter(),
            series_counts=Counter(),
            constraints=constraints,
            max_count=3,
        )
        is True
    )


def test_budget_constraints_enforced() -> None:
    portfolio = _portfolio(
        [
            _candidate(
                "expensive-a", score=0.95, metadata={"estimated_cost": 6.0, "assay_slots": 1}
            ),
            _candidate(
                "expensive-b",
                score=0.9,
                target="T2",
                series="series-b",
                metadata={"estimated_cost": 6.0, "assay_slots": 1},
            ),
            _candidate(
                "cheap",
                score=0.7,
                target="T3",
                series="series-c",
                metadata={"estimated_cost": 2.0, "assay_slots": 0},
            ),
        ],
        constraints=[],
        budget=ResourceBudget(max_candidates=3, max_total_cost=8.0, max_assay_slots=1),
    )

    selection = PortfolioOptimizer().optimize(portfolio).selections[0]
    selected = [
        candidate
        for candidate in portfolio.candidates
        if candidate.portfolio_candidate_id in selection.selected_candidate_ids
    ]

    assert (
        sum(float(candidate.metadata.get("estimated_cost", 0.0)) for candidate in selected) <= 8.0
    )
    assert sum(int(candidate.metadata.get("assay_slots", 0)) for candidate in selected) <= 1
