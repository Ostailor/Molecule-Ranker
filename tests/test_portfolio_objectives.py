from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from molecule_ranker.portfolio.objectives import (
    ObjectiveEvaluation,
    aggregate_objective_scores,
    default_objectives,
    explain_objectives,
    maximize_developability,
    maximize_evidence_strength,
    maximize_experiment_readiness,
    maximize_experimental_followup_value,
    maximize_learning_value,
    maximize_mechanism_diversity,
    maximize_scaffold_diversity,
    maximize_target_coverage,
    minimize_correlated_risk,
    minimize_generated_overexposure,
)
from molecule_ranker.portfolio.schemas import PortfolioCandidate

ObjectiveFunction = Callable[[Sequence[PortfolioCandidate]], ObjectiveEvaluation]


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    target: str = "T1",
    scaffold: str = "scaffold-a",
    series: str = "series-a",
    mechanism: str | None = "Target modulation",
    evidence_score: float | None = 0.7,
    experimental_support_score: float | None = None,
    experiment_readiness_score: float | None = 0.6,
    developability_score: float | None = 0.7,
    uncertainty_score: float | None = 0.4,
    review_status: str | None = None,
    direct_experimental_evidence: bool = False,
    risk_flags: list[str] | None = None,
    blocking_risks: list[str] | None = None,
    active_learning_signal: float | None = None,
) -> PortfolioCandidate:
    metadata = {}
    if active_learning_signal is not None:
        metadata["active_learning_suggestions"] = [
            {"expected_information_gain": active_learning_signal}
        ]
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        target_symbols=[target],
        mechanism_label=mechanism,
        chemical_series_id=series,
        scaffold_id=scaffold,
        evidence_score=evidence_score,
        experimental_support_score=experimental_support_score,
        developability_score=developability_score,
        experiment_readiness_score=experiment_readiness_score,
        uncertainty_score=uncertainty_score,
        diversity_features={},
        risk_flags=list(risk_flags or []),
        blocking_risks=list(blocking_risks or []),
        review_status=review_status,
        direct_experimental_evidence=direct_experimental_evidence,
        generated_without_direct_evidence=origin == "generated"
        and not direct_experimental_evidence,
        metadata=metadata,
    )


def _balanced_portfolio() -> list[PortfolioCandidate]:
    return [
        _candidate(
            "existing-a",
            target="T1",
            scaffold="scaffold-a",
            series="series-a",
            mechanism="Mechanism A",
            evidence_score=0.86,
            experimental_support_score=0.5,
            experiment_readiness_score=0.82,
            developability_score=0.88,
            uncertainty_score=0.35,
            review_status="approved",
            direct_experimental_evidence=True,
        ),
        _candidate(
            "existing-b",
            target="T2",
            scaffold="scaffold-b",
            series="series-b",
            mechanism="Mechanism B",
            evidence_score=0.72,
            experiment_readiness_score=0.7,
            developability_score=0.76,
            uncertainty_score=0.62,
            review_status="needs_review",
        ),
        _candidate(
            "generated-c",
            origin="generated",
            target="T3",
            scaffold="scaffold-c",
            series="series-c",
            mechanism=None,
            evidence_score=None,
            experimental_support_score=None,
            experiment_readiness_score=0.74,
            developability_score=0.7,
            uncertainty_score=0.92,
            review_status="needs_review",
            active_learning_signal=0.96,
        ),
    ]


@pytest.mark.parametrize(
    "objective_function",
    [
        maximize_evidence_strength,
        maximize_experiment_readiness,
        maximize_learning_value,
        maximize_developability,
        maximize_target_coverage,
        maximize_scaffold_diversity,
        maximize_mechanism_diversity,
        minimize_correlated_risk,
        minimize_generated_overexposure,
        maximize_experimental_followup_value,
    ],
)
def test_objective_returns_bounded_inspectable_evaluation(
    objective_function: ObjectiveFunction,
) -> None:
    evaluation = objective_function(_balanced_portfolio())

    assert 0.0 <= evaluation.score <= 1.0
    assert evaluation.explanation
    assert evaluation.components
    assert " is safe" not in evaluation.explanation.lower()
    assert " is active" not in evaluation.explanation.lower()
    assert " is effective" not in evaluation.explanation.lower()
    assert " is synthesizable" not in evaluation.explanation.lower()


def test_maximize_evidence_strength_uses_evidence_and_experimental_support() -> None:
    strong = [_candidate("strong", evidence_score=0.9, experimental_support_score=0.7)]
    weak = [_candidate("weak", evidence_score=None, experimental_support_score=None)]

    assert maximize_evidence_strength(strong).score > maximize_evidence_strength(weak).score
    assert maximize_evidence_strength(strong).components["evidence_coverage"] == 1.0


def test_maximize_experiment_readiness_uses_review_and_blocking_risks() -> None:
    ready = [_candidate("ready", experiment_readiness_score=0.9, review_status="approved")]
    blocked = [
        _candidate(
            "blocked",
            experiment_readiness_score=0.9,
            review_status="approved",
            blocking_risks=["critical_developability_risk"],
        )
    ]

    assert maximize_experiment_readiness(ready).score > maximize_experiment_readiness(blocked).score
    assert maximize_experiment_readiness(blocked).components["no_blocking_risk_fraction"] == 0.0


def test_maximize_learning_value_uses_uncertainty_gaps_active_learning_and_diversity() -> None:
    informative = _balanced_portfolio()
    low_information = [
        _candidate(
            "low-1",
            target="T1",
            scaffold="scaffold-a",
            evidence_score=0.9,
            experimental_support_score=0.5,
            uncertainty_score=0.1,
        ),
        _candidate(
            "low-2",
            target="T1",
            scaffold="scaffold-a",
            evidence_score=0.8,
            experimental_support_score=0.4,
            uncertainty_score=0.1,
        ),
    ]

    evaluation = maximize_learning_value(informative)

    assert evaluation.score > maximize_learning_value(low_information).score
    assert evaluation.components["active_learning_signal"] > 0.0
    assert evaluation.components["diversity_signal"] > 0.0


def test_maximize_developability_penalizes_risk_flags() -> None:
    low_risk = [_candidate("low-risk", developability_score=0.8)]
    flagged = [
        _candidate(
            "flagged",
            developability_score=0.8,
            risk_flags=["hERG_liability", "reactive_group"],
            blocking_risks=["critical_developability_risk"],
        )
    ]

    assert maximize_developability(low_risk).score > maximize_developability(flagged).score
    assert maximize_developability(flagged).components["risk_annotation_rate"] == 1.0


def test_maximize_target_coverage_rewards_distinct_targets() -> None:
    covered = _balanced_portfolio()
    concentrated = [
        _candidate("a", target="T1"),
        _candidate("b", target="T1", scaffold="scaffold-b", series="series-b"),
        _candidate("c", target="T1", scaffold="scaffold-c", series="series-c"),
    ]

    assert maximize_target_coverage(covered).score > maximize_target_coverage(concentrated).score
    assert maximize_target_coverage(covered).score == 1.0


def test_maximize_scaffold_diversity_rewards_distinct_scaffolds_and_series() -> None:
    diverse = _balanced_portfolio()
    concentrated = [
        _candidate("a", scaffold="scaffold-a", series="series-a"),
        _candidate("b", scaffold="scaffold-a", series="series-a", target="T2"),
    ]

    assert (
        maximize_scaffold_diversity(diverse).score > maximize_scaffold_diversity(concentrated).score
    )


def test_maximize_mechanism_diversity_requires_source_backed_mechanisms() -> None:
    source_backed = [
        _candidate("a", mechanism="Mechanism A", evidence_score=0.8),
        _candidate("b", mechanism="Mechanism B", evidence_score=0.7, target="T2"),
    ]
    unsupported = [
        _candidate("c", mechanism="Mechanism A", evidence_score=None),
        _candidate("d", mechanism="Mechanism B", evidence_score=None, target="T2"),
    ]

    assert maximize_mechanism_diversity(source_backed).score == 1.0
    assert maximize_mechanism_diversity(unsupported).score == 0.0


def test_minimize_correlated_risk_penalizes_shared_risk_modes() -> None:
    diversified = [
        _candidate("a", risk_flags=["hERG_liability"]),
        _candidate("b", risk_flags=["reactive_group"], scaffold="scaffold-b", target="T2"),
    ]
    correlated = [
        _candidate("a", risk_flags=["hERG_liability"]),
        _candidate("b", risk_flags=["hERG_liability"], scaffold="scaffold-b", target="T2"),
    ]

    assert minimize_correlated_risk(diversified).score > minimize_correlated_risk(correlated).score
    assert minimize_correlated_risk(correlated).components["repeated_risk_mode_fraction"] > 0.0


def test_minimize_generated_overexposure_penalizes_generated_only_concentration() -> None:
    balanced = _balanced_portfolio()
    generated_only = [
        _candidate("g1", origin="generated", evidence_score=None),
        _candidate("g2", origin="generated", evidence_score=None, target="T2"),
        _candidate("g3", origin="generated", evidence_score=None, target="T3"),
    ]

    assert minimize_generated_overexposure(balanced).score == 1.0
    assert minimize_generated_overexposure(generated_only).score < 0.1


def test_maximize_experimental_followup_value_balances_readiness_uncertainty_and_review() -> None:
    high_value = [
        _candidate(
            "high",
            experiment_readiness_score=0.9,
            uncertainty_score=0.9,
            review_status="needs_review",
            evidence_score=None,
        )
    ]
    low_value = [
        _candidate(
            "low",
            experiment_readiness_score=0.2,
            uncertainty_score=0.1,
            review_status="hold",
            evidence_score=0.9,
            experimental_support_score=0.4,
        )
    ]

    assert (
        maximize_experimental_followup_value(high_value).score
        > maximize_experimental_followup_value(low_value).score
    )


def test_aggregate_scores_and_explanations_use_registered_objective_functions() -> None:
    objectives = default_objectives()
    candidates = _balanced_portfolio()

    scores = aggregate_objective_scores(candidates, objectives)
    explanations = explain_objectives(candidates, objectives)

    assert set(scores) == {objective.objective_id for objective in objectives}
    assert all(0.0 <= score <= 1.0 for score in scores.values())
    assert explanations["learning_value"]["components"]["active_learning_signal"] > 0.0
