from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.reports import CoPilotStatusReporter
from molecule_ranker.copilot.schemas import (
    ActionStatus,
    ActionType,
    CampaignEvent,
    CoPilotAction,
    CoPilotActionResult,
    Severity,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _event(
    event_id: str = "event-1",
    *,
    severity: Severity = "high",
    artifact_ids: list[str] | None = None,
) -> CampaignEvent:
    return CampaignEvent(
        event_id=event_id,
        campaign_id="camp-1",
        event_type="graph_contradiction_detected",
        source_object_type="knowledge_graph",
        source_object_id="kg-1",
        severity=severity,
        summary="Graph contradiction needs source review.",
        artifact_ids=artifact_ids or ["artifact-event-1"],
        detected_at=NOW,
        metadata={"detector_event_type": "graph_contradiction"},
    )


def _action(
    action_id: str = "action-1",
    *,
    action_type: ActionType = "create_review_request",
    status: ActionStatus = "succeeded",
    requires_approval: bool = False,
) -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id=action_id,
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type=action_type,
        tool_name=None,
        tool_args={},
        side_effect_level="db_write",
        risk_level="medium",
        requires_approval=requires_approval,
        approval_reason=(
            "Human approval required before campaign advancement."
            if requires_approval
            else None
        ),
        status=status,
        created_at=NOW,
        completed_at=NOW if status == "succeeded" else None,
        metadata={},
    )


def _result(action_id: str = "action-1") -> CoPilotActionResult:
    return CoPilotActionResult(
        result_id="result-1",
        copilot_action_id=action_id,
        status="succeeded",
        artifact_ids=["artifact-result-1"],
        job_ids=[],
        summary="Review request created.",
        warnings=[],
        created_at=NOW,
        metadata={},
    )


def test_deterministic_status_update_writes_json_and_markdown_artifacts(tmp_path):
    reporter = CoPilotStatusReporter(artifact_dir=tmp_path, now=lambda: NOW)

    bundle = reporter.build_status_update(
        campaign_id="camp-1",
        period_start=NOW,
        period_end=NOW,
        cadence="daily",
        events=[_event()],
        actions=[_action()],
        action_results=[_result()],
    )

    assert bundle.update.executive_summary == (
        "Daily co-pilot status for camp-1: 1 key event, 1 action taken, "
        "0 approvals needed, 0 blockers."
    )
    assert bundle.update.key_events == ["event-1"]
    assert bundle.update.actions_taken == ["action-1"]
    assert bundle.update.metadata["artifact_refs"] == [
        "artifact-event-1",
        "artifact-result-1",
    ]
    assert set(bundle.artifacts) == {
        "copilot_status_update.json",
        "copilot_status_update.md",
    }
    assert (tmp_path / "copilot_status_update.json").exists()
    assert (tmp_path / "copilot_status_update.md").exists()
    json_payload = json.loads(bundle.artifacts["copilot_status_update.json"])
    assert json_payload["key_events"] == ["event-1"]
    assert "event-1" in bundle.artifacts["copilot_status_update.md"]
    assert "action-1" in bundle.artifacts["copilot_status_update.md"]


def test_codex_fake_event_is_rejected_and_deterministic_summary_is_used():
    def fake_codex_drafter(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "executive_summary": "Fake event event-fake proves candidate is active.",
            "key_events": ["event-1", "event-fake"],
            "actions_taken": ["action-1"],
        }

    reporter = CoPilotStatusReporter(codex_drafter=fake_codex_drafter, now=lambda: NOW)

    bundle = reporter.build_status_update(
        campaign_id="camp-1",
        period_start=NOW,
        period_end=NOW,
        cadence="manual",
        events=[_event()],
        actions=[_action()],
        action_results=[_result()],
        use_codex=True,
    )

    assert bundle.update.key_events == ["event-1"]
    assert "event-fake" not in bundle.artifacts["copilot_status_update.md"]
    assert bundle.update.metadata["codex_draft_rejected"] is True
    assert "candidate is active" not in bundle.update.executive_summary.lower()


def test_approvals_needed_are_listed_from_actions():
    reporter = CoPilotStatusReporter(now=lambda: NOW)
    approval_action = _action(
        "action-approval",
        action_type="run_campaign_replan",
        status="queued",
        requires_approval=True,
    )

    bundle = reporter.build_status_update(
        campaign_id="camp-1",
        period_start=NOW,
        period_end=NOW,
        cadence="after campaign replan",
        events=[_event()],
        actions=[approval_action],
    )

    assert bundle.update.approvals_needed == ["action-approval"]
    assert "action-approval" in bundle.artifacts["copilot_status_update.md"]
    assert "Human approval required" in bundle.artifacts["copilot_status_update.md"]


def test_status_update_removes_forbidden_scientific_claims_from_codex_output():
    def claim_drafter(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "executive_summary": "Candidate is active, safe, binding, and therapeutic.",
            "key_events": ["event-1"],
            "actions_taken": ["action-1"],
        }

    reporter = CoPilotStatusReporter(codex_drafter=claim_drafter, now=lambda: NOW)

    bundle = reporter.build_status_update(
        campaign_id="camp-1",
        period_start=NOW,
        period_end=NOW,
        cadence="weekly",
        events=[_event()],
        actions=[_action()],
        action_results=[_result()],
        use_codex=True,
    )
    rendered = (
        bundle.update.executive_summary
        + bundle.artifacts["copilot_status_update.md"]
        + bundle.artifacts["copilot_status_update.json"]
    ).lower()

    assert "candidate is active" not in rendered
    assert "therapeutic" not in rendered
    assert "binding" not in rendered
    assert "planning aid" in rendered
