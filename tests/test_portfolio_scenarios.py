from __future__ import annotations

from molecule_ranker.portfolio.objectives import default_objectives
from molecule_ranker.portfolio.scenarios import (
    build_decision_scenario,
    compare_decision_scenarios,
)
from molecule_ranker.portfolio.schemas import (
    Portfolio,
    PortfolioCandidate,
    Program,
    ResourceBudget,
)


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    target: str = "T1",
    series: str = "series-a",
    evidence_score: float | None = 0.7,
    generation_score: float | None = None,
    readiness: float = 0.7,
    uncertainty: float = 0.4,
    structure_score: float | None = 0.7,
    predictive_model_score: float | None = None,
    risk_flags: list[str] | None = None,
    review_status: str | None = None,
) -> PortfolioCandidate:
    generated = origin == "generated"
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        target_symbols=[target],
        mechanism_label=f"{target} modulation",
        chemical_series_id=series,
        scaffold_id=series,
        evidence_score=None if generated else evidence_score,
        generation_score=generation_score,
        developability_score=0.74,
        experimental_support_score=0.4 if evidence_score and evidence_score > 0.85 else None,
        predictive_model_score=predictive_model_score,
        structure_score=structure_score,
        experiment_readiness_score=readiness,
        uncertainty_score=uncertainty,
        diversity_features={},
        risk_flags=list(risk_flags or []),
        blocking_risks=[],
        review_status=review_status,
        direct_experimental_evidence=False,
        generated_without_direct_evidence=generated,
        metadata={},
    )


def _portfolio(candidates: list[PortfolioCandidate], max_candidates: int = 1) -> Portfolio:
    return Portfolio(
        portfolio_id="portfolio-scenarios",
        program=Program(program_id="program-scenarios", name="Program"),
        candidates=candidates,
        objectives=default_objectives(),
        constraints=[],
        budget=ResourceBudget(max_candidates=max_candidates),
        metadata={"algorithm": "greedy"},
    )


def test_conservative_scenario_penalizes_generated_only() -> None:
    portfolio = _portfolio(
        [
            _candidate("existing", evidence_score=0.72, readiness=0.7),
            _candidate(
                "generated",
                origin="generated",
                target="T2",
                series="series-b",
                generation_score=0.98,
                readiness=0.98,
                uncertainty=0.95,
                structure_score=0.2,
                predictive_model_score=0.9,
            ),
        ]
    )
    scenario = build_decision_scenario(
        "conservative",
        "Conservative",
        description="Conservative scenario.",
        assumptions=[],
    )

    analysis = compare_decision_scenarios(portfolio, [scenario])

    assert analysis.metadata["scenario_specific_selected_candidates"]["conservative"] == [
        "existing"
    ]


def test_exploration_scenario_selects_uncertain_diverse_candidates() -> None:
    portfolio = _portfolio(
        [
            _candidate("known-a", target="T1", series="series-a", evidence_score=0.9),
            _candidate(
                "uncertain-diverse",
                origin="generated",
                target="T2",
                series="series-b",
                generation_score=0.78,
                readiness=0.76,
                uncertainty=0.98,
                structure_score=0.5,
            ),
            _candidate("known-b", target="T1", series="series-a", evidence_score=0.82),
        ],
        max_candidates=2,
    )
    scenario = build_decision_scenario(
        "exploration",
        "Exploration",
        description="Exploration scenario.",
        assumptions=[],
    )

    analysis = compare_decision_scenarios(portfolio, [scenario])
    selected = analysis.metadata["scenario_specific_selected_candidates"]["exploration"]

    assert "uncertain-diverse" in selected


def test_safety_first_removes_high_risk_candidates() -> None:
    portfolio = _portfolio(
        [
            _candidate("high-risk", evidence_score=0.96, risk_flags=["toxicity_alert"]),
            _candidate("lower-risk", target="T2", series="series-b", evidence_score=0.72),
        ]
    )
    scenario = build_decision_scenario(
        "safety_first",
        "Safety-first",
        description="Risk-first scenario.",
        assumptions=[],
    )

    analysis = compare_decision_scenarios(portfolio, [scenario])
    selected = analysis.metadata["scenario_specific_selected_candidates"]["safety_first"]

    assert selected == ["lower-risk"]
    assert analysis.scenarios[0].selection is not None
    explanations = analysis.scenarios[0].selection.metadata["candidate_explanations"]
    assert explanations["high-risk"]["decision"] == "rejected"


def test_robust_candidates_computed_across_scenarios() -> None:
    portfolio = _portfolio(
        [
            _candidate("robust", evidence_score=0.95, readiness=0.9),
            _candidate(
                "generated",
                origin="generated",
                target="T2",
                series="series-b",
                generation_score=0.85,
                uncertainty=0.95,
            ),
            _candidate(
                "risk", target="T3", series="series-c", evidence_score=0.9, risk_flags=["alert"]
            ),
        ],
        max_candidates=2,
    )
    scenarios = [
        build_decision_scenario(
            "conservative",
            "Conservative",
            description="Conservative scenario.",
            assumptions=[],
        ),
        build_decision_scenario(
            "exploit",
            "Exploit",
            description="Exploit scenario.",
            assumptions=[],
        ),
        build_decision_scenario(
            "safety_first",
            "Safety-first",
            description="Risk-first scenario.",
            assumptions=[],
        ),
    ]

    analysis = compare_decision_scenarios(portfolio, scenarios)

    assert "robust" in analysis.robust_candidate_ids
    assert analysis.metadata["scenario_comparison_table"]
    assert set(analysis.metadata["scenario_specific_selected_candidates"]) == {
        "conservative",
        "exploit",
        "safety_first",
    }
    assert "evidence_strength" in analysis.objective_sensitivities
