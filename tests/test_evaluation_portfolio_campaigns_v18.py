from __future__ import annotations

import pytest

from molecule_ranker.evaluation.evaluators.campaigns import evaluate_campaign
from molecule_ranker.evaluation.evaluators.portfolio import evaluate_portfolio


def _metric(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"missing metric {name}")


def _portfolio_artifact() -> dict[str, object]:
    return {
        "artifact_id": "portfolio_optimization.json",
        "policy": {"generated_fraction_min": 0.25, "generated_fraction_max": 0.75},
        "selected_candidates": [
            {
                "candidate_id": "P1",
                "selection_status": "selected",
                "target_symbol": "T1",
                "scaffold": "S1",
                "risk_score": 0.2,
                "candidate_origin": "existing",
                "review_gate_status": "approved",
                "scenario_scores": [0.8, 0.7, 0.75],
                "evidence_score": 0.9,
            },
            {
                "candidate_id": "P2",
                "selection_status": "selected",
                "target_symbol": "T2",
                "scaffold": "S2",
                "risk_score": 0.8,
                "candidate_origin": "generated",
                "review_gate_status": "approved",
                "scenario_scores": [0.6, 0.55, 0.65],
                "evidence_score": 0.2,
            },
        ],
        "portfolio_candidates": [
            {
                "candidate_id": "P3",
                "selection_status": "not_selected",
                "target_symbol": "T1",
                "scaffold": "S1",
                "risk_score": 0.1,
                "candidate_origin": "generated",
                "review_gate_status": "rejected",
                "scenario_scores": [0.5, 0.4],
                "evidence_score": 0.8,
            }
        ],
    }


def _portfolio_labels() -> dict[str, object]:
    return {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {"candidate_id": "P1", "outcome_label": "positive", "qc_status": "passed"},
            {"candidate_id": "P2", "outcome_label": "negative", "qc_status": "passed"},
            {"candidate_id": "P3", "outcome_label": "positive", "qc_status": "passed"},
        ],
    }


def test_portfolio_evaluator_scores_selection_quality_and_baseline_improvement() -> None:
    report = evaluate_portfolio(
        portfolio_artifacts={"portfolio_optimization": _portfolio_artifact()},
        imported_outcome_labels={"imported_assay_results": _portfolio_labels()},
        evaluation_id="portfolio-eval",
    )

    assert _metric(report, "selected_hit_rate").value == pytest.approx(0.5)
    assert _metric(report, "target_coverage").value == pytest.approx(1.0)
    assert _metric(report, "scaffold_diversity").value == pytest.approx(1.0)
    assert _metric(report, "risk_concentration").value == pytest.approx(0.5)
    assert _metric(report, "generated_fraction_policy_compliance").value is True
    assert _metric(report, "review_gate_compliance").value == pytest.approx(1.0)
    assert _metric(report, "scenario_robustness").value == pytest.approx(0.625)
    assert _metric(report, "baseline_improvement_over_random").value == pytest.approx(0.0)
    assert _metric(report, "baseline_improvement_over_evidence_only").value == pytest.approx(-0.5)
    assert report.metadata["rules"]["imported_outcomes_required_for_hit_metrics"] is True


def test_portfolio_hit_metrics_require_imported_outcomes() -> None:
    report = evaluate_portfolio(
        portfolio_artifacts={"portfolio_optimization": _portfolio_artifact()},
        imported_outcome_labels={},
    )

    assert _metric(report, "selected_hit_rate").value is None
    assert "no_imported_or_fixture_outcome_labels" in report.warnings


def _campaign_artifact() -> dict[str, object]:
    return {
        "artifact_id": "campaign_plan.json",
        "budget_spent": 60,
        "budget_allocated": 100,
        "expected_learning_value": 4,
        "realized_learning_value": 2,
        "assay_slots": 4,
        "work_packages": [
            {
                "work_package_id": "WP1",
                "candidate_id": "C1",
                "status": "completed",
                "replan_triggered": True,
                "replan_was_useful": True,
                "predicted_stop": False,
                "actual_should_stop": False,
                "review_gate_approved": True,
            },
            {
                "work_package_id": "WP2",
                "candidate_id": "C2",
                "status": "completed",
                "replan_triggered": True,
                "replan_was_useful": False,
                "predicted_stop": True,
                "actual_should_stop": True,
                "review_gate_approved": True,
            },
            {
                "work_package_id": "WP3",
                "candidate_id": "C3",
                "status": "planned",
                "replan_triggered": False,
                "replan_was_useful": False,
                "predicted_stop": False,
                "actual_should_stop": True,
                "review_gate_approved": False,
            },
        ],
    }


def _campaign_labels() -> dict[str, object]:
    return {
        "artifact_id": "campaign_outcomes.json",
        "assay_results": [
            {"candidate_id": "C1", "outcome_label": "positive", "qc_status": "passed"},
            {"candidate_id": "C2", "outcome_label": "negative", "qc_status": "passed"},
            {"candidate_id": "C3", "outcome_label": "positive", "qc_status": "failed"},
        ],
    }


def test_campaign_evaluator_scores_operational_learning_without_completion_overclaim() -> None:
    report = evaluate_campaign(
        campaign_artifacts={"campaign_plan": _campaign_artifact()},
        imported_outcome_labels={"imported_assay_results": _campaign_labels()},
        evaluation_id="campaign-eval",
    )

    assert _metric(report, "work_package_completion_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "replan_trigger_precision").value == pytest.approx(0.5)
    assert _metric(report, "budget_utilization").value == pytest.approx(0.6)
    assert _metric(report, "assay_slot_efficiency").value == pytest.approx(0.25)
    assert _metric(report, "learning_value_realized").value == pytest.approx(0.5)
    assert _metric(report, "failed_QC_handling").value is True
    assert _metric(report, "stop_continue_decision_quality").value == pytest.approx(2 / 3)
    assert _metric(report, "review_gate_outcome_alignment").value == pytest.approx(0.5)
    assert report.metadata["rules"]["completion_is_scientific_success"] is False
    assert "campaign_completion_is_not_scientific_success" in report.limitations


def test_campaign_hit_metrics_require_imported_outcomes() -> None:
    report = evaluate_campaign(
        campaign_artifacts={"campaign_plan": _campaign_artifact()},
        imported_outcome_labels={},
    )

    assert _metric(report, "assay_slot_efficiency").value is None
    assert "no_imported_or_fixture_outcome_labels" in report.warnings
