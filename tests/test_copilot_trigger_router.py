from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.copilot.schemas import CampaignEvent, CampaignEventType, Severity
from molecule_ranker.copilot.trigger_router import TriggerRouter

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _event(
    event_id: str,
    event_type: CampaignEventType,
    detector_event_type: str,
    *,
    severity: Severity = "medium",
) -> CampaignEvent:
    return CampaignEvent(
        event_id=event_id,
        campaign_id="camp-1",
        event_type=event_type,
        source_object_type="synthetic",
        source_object_id=event_id,
        severity=severity,
        summary=f"{detector_event_type} detected.",
        artifact_ids=[],
        detected_at=NOW,
        metadata={"detector_event_type": detector_event_type},
    )


@pytest.mark.parametrize(
    ("event", "trigger_type", "priority", "requires_human"),
    [
        (
            _event(
                "positive-1",
                "assay_result_imported",
                "positive_qc_passed_exact_assay_result",
            ),
            "result_followup_needed",
            "medium",
            False,
        ),
        (
            _event(
                "negative-1",
                "assay_result_imported",
                "negative_qc_passed_exact_assay_result",
            ),
            "replan_needed",
            "medium",
            True,
        ),
        (
            _event("failed-qc-1", "guardrail_failure", "failed_qc_result", severity="high"),
            "approval_needed",
            "high",
            True,
        ),
        (
            _event(
                "safety-1",
                "developability_risk_changed",
                "safety_developability_concern",
                severity="high",
            ),
            "safety_review_needed",
            "high",
            True,
        ),
        (
            _event("graph-1", "graph_contradiction_detected", "graph_contradiction"),
            "contradiction_resolution_needed",
            "high",
            True,
        ),
        (
            _event("stale-1", "stale_decision_detected", "stale_decision"),
            "replan_needed",
            "medium",
            True,
        ),
        (
            _event("job-failed-1", "job_failed", "failed_job", severity="high"),
            "repair_needed",
            "high",
            True,
        ),
        (
            _event("job-repaired-1", "job_repaired", "repaired_job"),
            "campaign_update_needed",
            "medium",
            False,
        ),
        (
            _event("portfolio-1", "portfolio_updated", "portfolio_changed_after_campaign_plan"),
            "campaign_update_needed",
            "medium",
            False,
        ),
        (
            _event("budget-1", "budget_changed", "budget_threshold_exceeded", severity="high"),
            "budget_review_needed",
            "high",
            True,
        ),
        (
            _event("guardrail-1", "guardrail_failure", "guardrail_failure", severity="critical"),
            "blocker_detected",
            "critical",
            True,
        ),
        (
            _event("scheduled-1", "scheduled_check", "scheduled_check", severity="info"),
            "no_action",
            "low",
            False,
        ),
    ],
)
def test_trigger_router_maps_each_event_to_expected_trigger(
    event,
    trigger_type,
    priority,
    requires_human,
):
    trigger = TriggerRouter().route([event])[0]

    assert trigger.trigger_type == trigger_type
    assert trigger.priority == priority
    assert trigger.requires_human_attention is requires_human
    assert trigger.trigger_signature == trigger.metadata["trigger_signature"]


def test_failed_qc_routes_to_human_review_not_false_negative():
    event = _event("failed-qc-1", "guardrail_failure", "failed_qc_result", severity="high")

    trigger = TriggerRouter().route([event])[0]

    assert trigger.trigger_type == "approval_needed"
    assert "create_review_request" in trigger.recommended_action_types
    assert trigger.trigger_type != "replan_needed"
    assert trigger.metadata["detector_event_type"] == "failed_qc_result"


def test_trigger_router_deduplicates_equivalent_events_into_one_window():
    events = [
        _event("positive-1", "assay_result_imported", "positive_qc_passed_exact_assay_result"),
        _event("positive-2", "assay_result_imported", "positive_qc_passed_exact_assay_result"),
    ]

    triggers = TriggerRouter().route(events)

    assert len(triggers) == 1
    assert triggers[0].event_ids == ["positive-1", "positive-2"]
    assert triggers[0].metadata["deduplicated_event_count"] == 2


def test_critical_safety_event_assigns_critical_priority():
    event = _event(
        "safety-critical-1",
        "developability_risk_changed",
        "safety_developability_concern",
        severity="critical",
    )

    trigger = TriggerRouter().route([event])[0]

    assert trigger.trigger_type == "safety_review_needed"
    assert trigger.priority == "critical"
    assert trigger.requires_human_attention is True
