from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.copilot.event_detector import EventDetector

PLAN_TIME = datetime(2026, 6, 4, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _base_state(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "campaign_id": "camp-1",
        "campaign_plan_updated_at": PLAN_TIME,
        "generated_molecules": {
            "gen-1": {"canonical_structure": "CCO"},
        },
    }
    state.update(records)
    return state


@pytest.mark.parametrize(
    ("records", "detector_event_type", "event_type", "severity"),
    [
        (
            {
                "assay_results": [
                    {
                        "result_id": "assay-positive",
                        "imported": True,
                        "result_kind": "assay_result",
                        "qc_status": "passed",
                        "assay_match": "exact",
                        "direction": "positive",
                        "molecule_id": "gen-1",
                        "canonical_structure": "CCO",
                        "created_at": NOW,
                    }
                ]
            },
            "positive_qc_passed_exact_assay_result",
            "assay_result_imported",
            "medium",
        ),
        (
            {
                "assay_results": [
                    {
                        "result_id": "assay-negative",
                        "imported": True,
                        "result_kind": "assay_result",
                        "qc_status": "passed",
                        "assay_match": "exact",
                        "direction": "negative",
                        "molecule_id": "existing-1",
                        "created_at": NOW,
                    }
                ]
            },
            "negative_qc_passed_exact_assay_result",
            "assay_result_imported",
            "medium",
        ),
        (
            {
                "assay_results": [
                    {
                        "result_id": "assay-failed-qc",
                        "imported": True,
                        "result_kind": "assay_result",
                        "qc_status": "failed",
                        "assay_match": "exact",
                        "direction": "positive",
                        "created_at": NOW,
                    }
                ]
            },
            "failed_qc_result",
            "guardrail_failure",
            "high",
        ),
        (
            {
                "developability_findings": [
                    {
                        "finding_id": "dev-risk",
                        "concern": "developability risk changed.",
                        "risk_level": "high",
                        "created_at": NOW,
                    }
                ]
            },
            "safety_developability_concern",
            "developability_risk_changed",
            "high",
        ),
        (
            {
                "review_decisions": [
                    {
                        "decision_id": "review-reject",
                        "decision": "rejected",
                        "summary": "Reviewer rejected the planning item.",
                        "created_at": NOW,
                    }
                ]
            },
            "review_rejection",
            "review_decision_added",
            "high",
        ),
        (
            {
                "review_decisions": [
                    {
                        "decision_id": "review-approve",
                        "decision": "approved",
                        "summary": "Reviewer approved the planning item.",
                        "created_at": NOW,
                    }
                ]
            },
            "review_approval",
            "review_decision_added",
            "medium",
        ),
        (
            {
                "graph_reports": [
                    {
                        "report_id": "graph-contradiction",
                        "report_type": "contradiction",
                        "summary": "Graph contradiction found.",
                        "created_at": NOW,
                    }
                ]
            },
            "graph_contradiction",
            "graph_contradiction_detected",
            "high",
        ),
        (
            {
                "graph_reports": [
                    {
                        "report_id": "stale-decision",
                        "report_type": "stale_decision",
                        "summary": "Decision is stale.",
                        "created_at": NOW,
                    }
                ]
            },
            "stale_decision",
            "stale_decision_detected",
            "medium",
        ),
        (
            {
                "model_runs": [
                    {
                        "model_run_id": "model-1",
                        "status": "retrained",
                        "completed_at": NOW,
                    }
                ]
            },
            "model_retrained_after_campaign_plan",
            "model_retrained",
            "medium",
        ),
        (
            {
                "portfolio_changes": [
                    {
                        "change_id": "portfolio-1",
                        "changed_at": NOW,
                        "summary": "Portfolio changed.",
                    }
                ]
            },
            "portfolio_changed_after_campaign_plan",
            "portfolio_updated",
            "medium",
        ),
        (
            {
                "integration_syncs": [
                    {
                        "sync_id": "sync-1",
                        "status": "completed",
                        "completed_at": NOW,
                    }
                ]
            },
            "external_integration_sync_completed",
            "integration_sync_completed",
            "low",
        ),
        (
            {
                "jobs": [
                    {
                        "job_id": "job-failed",
                        "status": "failed",
                        "updated_at": NOW,
                    }
                ]
            },
            "failed_job",
            "job_failed",
            "high",
        ),
        (
            {
                "jobs": [
                    {
                        "job_id": "job-repaired",
                        "status": "repaired",
                        "updated_at": NOW,
                    }
                ]
            },
            "repaired_job",
            "job_repaired",
            "medium",
        ),
        (
            {
                "budgets": [
                    {
                        "budget_id": "budget-1",
                        "spent_fraction": 0.91,
                        "threshold_fraction": 0.9,
                        "updated_at": NOW,
                    }
                ]
            },
            "budget_threshold_exceeded",
            "budget_changed",
            "high",
        ),
        (
            {
                "work_packages": [
                    {
                        "work_package_id": "wp-1",
                        "status": "blocked",
                        "updated_at": NOW,
                    }
                ]
            },
            "campaign_work_package_blocked",
            "external_status_update",
            "high",
        ),
        (
            {
                "guardrail_failures": [
                    {
                        "failure_id": "guardrail-1",
                        "summary": "Policy guardrail failed.",
                        "created_at": NOW,
                    }
                ]
            },
            "guardrail_failure",
            "guardrail_failure",
            "critical",
        ),
        (
            {
                "evaluation_reports": [
                    {
                        "evaluation_id": "eval-1",
                        "performance_delta": -0.2,
                        "degradation_threshold": -0.1,
                        "created_at": NOW,
                    }
                ]
            },
            "evaluation_performance_degradation",
            "evaluation_report_created",
            "high",
        ),
    ],
)
def test_event_detector_detects_each_required_event_type(
    records,
    detector_event_type,
    event_type,
    severity,
):
    events = EventDetector(now=lambda: NOW).detect_campaign_events(_base_state(records))

    assert len(events) == 1
    assert events[0].event_type == event_type
    assert events[0].severity == severity
    assert events[0].metadata["detector_event_type"] == detector_event_type


def test_event_detector_requires_qc_pass_for_positive_or_negative_assay_result():
    events = EventDetector(now=lambda: NOW).detect_campaign_events(
        _base_state(
            {
                "assay_results": [
                    {
                        "result_id": "assay-failed-qc",
                        "imported": True,
                        "result_kind": "assay_result",
                        "qc_status": "failed",
                        "assay_match": "exact",
                        "direction": "positive",
                        "created_at": NOW,
                    }
                ]
            }
        )
    )

    assert events[0].metadata["detector_event_type"] == "failed_qc_result"
    assert "positive_qc_passed_exact_assay_result" not in {
        event.metadata["detector_event_type"] for event in events
    }


def test_event_detector_requires_exact_structure_match_for_generated_molecules():
    events = EventDetector(now=lambda: NOW).detect_campaign_events(
        _base_state(
            {
                "assay_results": [
                    {
                        "result_id": "generated-mismatch",
                        "imported": True,
                        "result_kind": "assay_result",
                        "qc_status": "passed",
                        "assay_match": "exact",
                        "direction": "positive",
                        "molecule_id": "gen-1",
                        "canonical_structure": "CCN",
                        "created_at": NOW,
                    }
                ]
            }
        )
    )

    assert events == []


def test_event_detector_ignores_model_predictions_as_assay_outcomes():
    events = EventDetector(now=lambda: NOW).detect_campaign_events(
        _base_state(
            {
                "assay_results": [
                    {
                        "result_id": "prediction-1",
                        "imported": True,
                        "result_kind": "model_prediction",
                        "qc_status": "passed",
                        "assay_match": "exact",
                        "direction": "positive",
                        "created_at": NOW,
                    }
                ]
            }
        )
    )

    assert events == []
