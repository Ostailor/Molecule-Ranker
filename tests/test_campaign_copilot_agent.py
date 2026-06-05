from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.campaign_copilot import CampaignCoPilotAgent
from molecule_ranker.copilot.schemas import (
    ActionResultStatus,
    ActionStatus,
    ActionType,
    CampaignEvent,
    CampaignEventType,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotTrigger,
    Severity,
    TriggerType,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


class SyntheticMonitor:
    def __init__(self, events: list[CampaignEvent]) -> None:
        self.events = events
        self.calls = 0

    def poll_active_campaigns(self, **kwargs: Any) -> list[CampaignEvent]:
        self.calls += 1
        return self.events


class SyntheticExecutor:
    def __init__(self, status: ActionResultStatus = "succeeded") -> None:
        self.status: ActionResultStatus = status
        self.calls: list[str] = []

    def __call__(self, action: CoPilotAction) -> CoPilotActionResult:
        self.calls.append(action.copilot_action_id)
        return CoPilotActionResult(
            result_id=f"result-{action.copilot_action_id}",
            copilot_action_id=action.copilot_action_id,
            status=self.status,
            artifact_ids=[f"artifact-{action.copilot_action_id}"],
            job_ids=[],
            summary=f"{action.action_type} completed.",
            warnings=[],
            created_at=NOW,
            metadata={},
        )


class SyntheticRouter:
    def __init__(self, triggers: list[CoPilotTrigger]) -> None:
        self.triggers = triggers

    def route(self, events: list[CampaignEvent]) -> list[CoPilotTrigger]:
        return self.triggers if events else []


def _event(
    event_id: str = "event-1",
    *,
    event_type: CampaignEventType = "integration_sync_completed",
    detector_event_type: str = "external_integration_sync_completed",
    severity: Severity = "low",
) -> CampaignEvent:
    return CampaignEvent(
        event_id=event_id,
        campaign_id="camp-1",
        event_type=event_type,
        source_object_type="integration_sync",
        source_object_id="sync-1",
        severity=severity,
        summary="Synthetic campaign event.",
        artifact_ids=["artifact-event-1"],
        detected_at=NOW,
        metadata={"detector_event_type": detector_event_type},
    )


def _trigger(
    *,
    trigger_type: TriggerType = "campaign_update_needed",
    action_types: list[str] | None = None,
    requires_human_attention: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CoPilotTrigger:
    return CoPilotTrigger(
        trigger_id="trigger-1",
        campaign_id="camp-1",
        event_ids=["event-1"],
        trigger_signature="camp-1:campaign_update_needed:integration:event",
        trigger_type=trigger_type,
        priority="medium",
        rationale="Synthetic trigger.",
        recommended_action_types=action_types or ["summarize_status"],
        requires_human_attention=requires_human_attention,
        metadata=metadata or {},
    )


def _action(
    action_type: ActionType,
    *,
    status: ActionStatus = "queued",
    requires_approval: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id=f"action-1-{action_type}",
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type=action_type,
        tool_name=None,
        tool_args={},
        side_effect_level="none" if action_type == "summarize_status" else "db_write",
        risk_level="low" if action_type == "summarize_status" else "high",
        requires_approval=requires_approval,
        approval_reason="Human approval required." if requires_approval else None,
        status=status,
        created_at=NOW,
        completed_at=None,
        metadata=metadata or {},
    )


def _context(**config: Any) -> PipelineContext:
    return PipelineContext(
        disease_input="Parkinson disease",
        config={
            "campaign_id": "camp-1",
            "results_dir": str(config.pop("results_dir", "results")),
            **config,
        },
    )


def test_campaign_copilot_disabled_noop_appends_trace():
    monitor = SyntheticMonitor([_event()])
    updated = CampaignCoPilotAgent(monitor=monitor, now=lambda: NOW).run(
        _context(enable_campaign_copilot=False)
    )

    assert monitor.calls == 0
    assert updated.traces[-1].agent_name == "CampaignCoPilotAgent"
    assert updated.traces[-1].metadata["enabled"] is False
    assert updated.traces[-1].metadata["events_detected"] == 0


def test_observe_only_detects_events_but_proposes_no_actions():
    updated = CampaignCoPilotAgent(monitor=SyntheticMonitor([_event()]), now=lambda: NOW).run(
        _context(
            enable_campaign_copilot=True,
            campaign_copilot_autonomy="observe_only",
        )
    )

    trace = updated.traces[-1]
    assert trace.metadata["events_detected"] == 1
    assert trace.metadata["actions_proposed"] == 0
    assert trace.metadata["actions_executed"] == 0


def test_execute_safe_actions_runs_safe_action(tmp_path):
    executor = SyntheticExecutor()
    updated = CampaignCoPilotAgent(
        monitor=SyntheticMonitor([_event()]),
        executor=executor,
        now=lambda: NOW,
    ).run(
        _context(
            results_dir=tmp_path,
            enable_campaign_copilot=True,
            campaign_copilot_autonomy="execute_safe_actions",
        )
    )

    assert len(executor.calls) == 1
    assert executor.calls[0].endswith("-summarize_status")
    assert updated.traces[-1].metadata["actions_executed"] == 1
    assert (tmp_path / "copilot" / "copilot_status_update.json").exists()
    assert (tmp_path / "copilot" / "copilot_status_update.md").exists()


def test_approval_required_for_replan_is_queued_not_executed():
    executor = SyntheticExecutor()
    agent = CampaignCoPilotAgent(
        monitor=SyntheticMonitor([_event(detector_event_type="stale_decision")]),
        trigger_router=SyntheticRouter(
            [
                _trigger(
                    trigger_type="replan_needed",
                    action_types=["run_campaign_replan"],
                    requires_human_attention=True,
                )
            ]
        ),
        executor=executor,
        now=lambda: NOW,
    )
    updated = agent.run(
        _context(
            enable_campaign_copilot=True,
            campaign_copilot_autonomy="execute_with_approval",
            campaign_copilot_require_approval_for_replan=True,
        )
    )

    assert executor.calls == []
    assert updated.traces[-1].metadata["approvals_requested"] == 1
    assert updated.traces[-1].metadata["actions_executed"] == 0


def test_guardrail_failure_pauses_session():
    agent = CampaignCoPilotAgent(
        monitor=SyntheticMonitor(
            [
                _event(
                    event_type="guardrail_failure",
                    detector_event_type="guardrail_failure",
                    severity="critical",
                )
            ]
        ),
        now=lambda: NOW,
    )
    updated = agent.run(
        _context(
            enable_campaign_copilot=True,
            campaign_copilot_autonomy="execute_safe_actions",
            campaign_copilot_pause_on_guardrail_failure=True,
        )
    )

    assert updated.config["campaign_copilot_session"].status == "paused"
    assert updated.traces[-1].metadata["session_status"] == "paused"
    assert updated.traces[-1].metadata["guardrail_failures"] == 1
