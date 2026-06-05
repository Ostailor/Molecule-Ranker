from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.memory import CoPilotMemory
from molecule_ranker.copilot.schemas import (
    ActionResultStatus,
    ActionType,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotTrigger,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _trigger(**metadata: Any) -> CoPilotTrigger:
    return CoPilotTrigger(
        trigger_id="trigger-1",
        campaign_id="camp-1",
        event_ids=["event-1"],
        trigger_signature=None,
        trigger_type="replan_needed",
        priority="high",
        rationale="Operational trigger.",
        recommended_action_types=["create_replan_draft"],
        requires_human_attention=True,
        metadata={
            "detector_event_type": "negative_qc_passed_exact_assay_result",
            "source_object_type": "assay_result",
            **metadata,
        },
    )


def _action(action_type: ActionType = "create_replan_draft") -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id=f"action-{action_type}",
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type=action_type,
        tool_name=None,
        tool_args={},
        side_effect_level="artifact_write",
        risk_level="medium",
        requires_approval=False,
        approval_reason=None,
        status="succeeded",
        created_at=NOW,
        completed_at=NOW,
        metadata={},
    )


def _result(status: ActionResultStatus = "succeeded") -> CoPilotActionResult:
    return CoPilotActionResult(
        result_id=f"result-{status}",
        copilot_action_id="action-create_replan_draft",
        status=status,
        artifact_ids=[],
        job_ids=[],
        summary="Operational action completed.",
        warnings=[],
        created_at=NOW,
        metadata={},
    )


def test_recurring_trigger_retrieves_prior_action_recommendation():
    memory = CoPilotMemory(now=lambda: NOW)
    trigger = _trigger()
    signature = memory.compute_trigger_signature(trigger)
    memory.record_action_outcome(
        trigger=trigger,
        action=_action("create_replan_draft"),
        result=_result("succeeded"),
        human_feedback="Useful planning follow-up.",
        time_to_resolution_seconds=240,
        campaign_context={"phase": "lead-review"},
    )

    matches = memory.retrieve_similar_trigger_memories(_trigger())

    assert matches[0].trigger_signature == signature
    assert matches[0].recommended_action_type == "create_replan_draft"
    assert matches[0].metadata["is_scientific_evidence"] is False
    assert matches[0].metadata["outcome_status"] == "succeeded"


def test_failed_action_lowers_success_rate_for_recurring_memory():
    memory = CoPilotMemory(now=lambda: NOW)
    trigger = _trigger()
    memory.record_action_outcome(
        trigger=trigger,
        action=_action("create_replan_draft"),
        result=_result("succeeded"),
    )
    updated = memory.record_action_outcome(
        trigger=trigger,
        action=_action("create_replan_draft"),
        result=_result("failed"),
        repeated_blocker=True,
    )

    assert updated.success_rate == 0.5
    assert updated.occurrence_count == 2
    assert updated.metadata["failure_count"] == 1
    assert updated.metadata["repeated_blocker"] is True


def test_secrets_and_raw_assay_data_are_redacted_from_memory_export():
    memory = CoPilotMemory(now=lambda: NOW)
    memory.record_action_outcome(
        trigger=_trigger(api_token="secret-token"),
        action=_action("create_review_request"),
        result=_result("succeeded"),
        human_feedback="Used token secret-token during triage.",
        campaign_context={
            "api_key": "abc123",
            "raw_assay_data": {"value": 12.3, "unit": "nM"},
            "nested": {"authorization": "Bearer secret-token"},
        },
    )

    exported = memory.export_memory()
    exported_text = str(exported).lower()

    assert "secret-token" not in exported_text
    assert "abc123" not in exported_text
    assert "12.3" not in exported_text
    assert "[redacted]" in exported_text
    assert "[omitted_raw_assay_data]" in exported_text


def test_memory_does_not_become_scientific_evidence_or_claim_candidate_activity():
    memory = CoPilotMemory(now=lambda: NOW)
    record = memory.record_action_outcome(
        trigger=_trigger(),
        action=_action("create_replan_draft"),
        result=_result("succeeded"),
        human_feedback="Candidate is active and safe.",
        campaign_context={"claim": "candidate is therapeutic and binding"},
    )

    exported = memory.export_memory()
    exported_text = str(exported).lower()

    assert record.metadata["is_scientific_evidence"] is False
    assert record.metadata["evidence_role"] == "operational_memory_only"
    assert "candidate is active" not in exported_text
    assert "therapeutic and binding" not in exported_text
    assert "recommendation_kind" in record.metadata
