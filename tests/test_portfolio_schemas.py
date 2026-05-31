from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.portfolio.schemas import (
    DecisionScenario,
    PortfolioCandidate,
    PortfolioConstraint,
    PortfolioObjective,
    PortfolioOptimizationRun,
    PortfolioSelection,
    Program,
    ProgramDecisionMemo,
    ResourceBudget,
    StageGate,
)


def _selection() -> PortfolioSelection:
    return PortfolioSelection(
        selection_id="selection-1",
        selected_candidate_ids=["pc-1"],
        rejected_candidate_ids=[],
        deferred_candidate_ids=["pc-2"],
        objective_scores={"evidence": 0.7},
        constraint_violations=[],
        portfolio_score=0.7,
        diversity_summary={},
        risk_summary={},
        uncertainty_summary={},
        target_coverage={},
        rationale="Deterministic portfolio selection.",
        warnings=[],
    )


def test_portfolio_candidate_schema_bounds_scores_and_generated_evidence_state() -> None:
    candidate = PortfolioCandidate(
        portfolio_candidate_id="pc-1",
        source_candidate_id="gen-1",
        candidate_name="Generated 1",
        origin="generated",
        canonical_smiles="CCO",
        inchi_key=None,
        disease_name="Disease A",
        target_symbols=["T1"],
        mechanism_label="T1 modulation",
        chemical_series_id="series-a",
        scaffold_id="scaffold-a",
        evidence_score=0.1,
        generation_score=0.7,
        developability_score=0.6,
        experimental_support_score=0.0,
        predictive_model_score=0.5,
        structure_score=0.4,
        experiment_readiness_score=0.8,
        uncertainty_score=0.65,
        novelty_score=0.72,
        diversity_features={"cluster": "a"},
        risk_flags=[],
        blocking_risks=[],
        review_status=None,
        direct_experimental_evidence=False,
        generated_without_direct_evidence=False,
        metadata={},
    )

    assert candidate.generated_without_direct_evidence is True

    with pytest.raises(ValidationError):
        PortfolioCandidate(
            portfolio_candidate_id="pc-2",
            candidate_name="Bad score",
            origin="existing",
            evidence_score=1.2,
        )

    with pytest.raises(ValidationError, match="objective_scores"):
        PortfolioSelection(
            selection_id="selection-bad-score",
            objective_scores={"evidence": 1.2},
            portfolio_score=0.5,
            rationale="Invalid objective score.",
        )


def test_program_and_run_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Program(
            program_id="program-1",
            name="Program",
            disease_focus=[],
            target_focus=[],
            created_at=datetime(2026, 1, 1),
            updated_at=datetime.now(UTC),
        )

    run = PortfolioOptimizationRun(
        optimization_run_id="run-1",
        program_id="program-1",
        project_id=None,
        disease_name="Disease A",
        input_candidate_count=1,
        objectives=[],
        constraints=[],
        budget=ResourceBudget(budget_id="budget-1", name="Budget"),
        algorithm="greedy",
        status="succeeded",
        selections=[_selection()],
        recommended_selection_id="selection-1",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        warnings=[],
        metadata={},
    )

    assert run.started_at.tzinfo is not None


def test_objective_constraint_budget_scenario_stage_gate_and_memo_schemas() -> None:
    objective = PortfolioObjective(
        objective_id="obj-1",
        name="Evidence",
        objective_type="maximize",
        metric_name="evidence_score",
        weight=1.0,
        direction="higher_is_better",
        hard=False,
        description="Prefer higher evidence.",
    )
    constraint = PortfolioConstraint(
        constraint_id="constraint-1",
        name="Max candidates",
        constraint_type="max_candidates",
        value=3,
        hard=True,
        violation_action="reject",
        description="Limit selected candidates.",
    )
    budget = ResourceBudget(
        budget_id="budget-1",
        name="Batch budget",
        max_candidates=3,
        max_existing_candidates=2,
        max_generated_candidates=1,
        max_total_cost=10.0,
        cost_units="slots",
        max_docking_jobs=0,
        max_assay_slots=2,
        max_review_hours=4.0,
        max_codex_tasks=0,
    )
    scenario = DecisionScenario(
        scenario_id="scenario-1",
        name="Risk averse",
        description="Risk-averse scenario.",
        objective_overrides={"obj-1": 0.8},
        constraint_overrides={"max_high_risk": 0},
        budget_overrides={"max_candidates": 2},
        assumptions=["No new evidence is imported."],
        selection=_selection(),
    )
    gate = StageGate(
        stage_gate_id="gate-1",
        name="Review gate",
        from_stage="triage",
        to_stage="portfolio",
        criteria=[{"name": "reviewed", "passed": True}],
        required_approvals=["program_lead"],
        decision="needs_more_data",
        rationale="Needs review.",
    )
    memo = ProgramDecisionMemo(
        memo_id="memo-1",
        program_id="program-1",
        optimization_run_id="run-1",
        title="Decision memo",
        executive_summary="Summary",
        selected_portfolio_summary="Selected pc-1.",
        key_tradeoffs=["Evidence versus uncertainty."],
        key_risks=["Generated hypotheses are unvalidated."],
        uncertainty_notes=["Uncertainty is computational."],
        recommended_next_actions=["Human review."],
        human_approval_required=True,
        limitations=["Research prioritization aid only."],
        created_at=datetime.now(UTC),
    )

    assert objective.direction == "higher_is_better"
    assert constraint.violation_action == "reject"
    assert budget.max_assay_slots == 2
    assert scenario.selection is not None
    assert gate.decision == "needs_more_data"
    assert memo.human_approval_required is True
