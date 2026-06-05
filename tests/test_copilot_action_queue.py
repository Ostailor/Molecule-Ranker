from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.copilot.action_queue import CoPilotActionQueue
from molecule_ranker.copilot.schemas import (
    ActionStatus,
    ActionType,
    CampaignCoPilotSession,
    CoPilotAction,
    CoPilotActionResult,
    RiskLevel,
    SideEffectLevel,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _action(
    action_id: str = "action-1",
    *,
    status: ActionStatus = "proposed",
    requires_approval: bool = False,
    action_type: ActionType = "summarize_status",
    risk_level: RiskLevel = "low",
    side_effect_level: SideEffectLevel = "none",
    metadata: dict[str, object] | None = None,
) -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id=action_id,
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type=action_type,
        tool_name=None,
        tool_args={},
        side_effect_level=side_effect_level,
        risk_level=risk_level,
        requires_approval=requires_approval,
        approval_reason="Needs approval." if requires_approval else None,
        status=status,
        created_at=NOW,
        completed_at=None,
        metadata=metadata or {},
    )


def _session() -> CampaignCoPilotSession:
    return CampaignCoPilotSession(
        copilot_session_id="session-1",
        campaign_id="camp-1",
        project_id="project-1",
        program_id=None,
        status="active",
        autonomy_level="execute_safe_actions",
        started_at=NOW,
        stopped_at=None,
        last_check_at=None,
        metadata={},
    )


def test_action_queue_queues_action_and_deduplicates_spam():
    queue = CoPilotActionQueue(now=lambda: NOW)
    action = _action(metadata={"dedupe_key": "status:camp-1"})

    queued = queue.queue_action(action)
    duplicate = queue.queue_action(_action("action-2", metadata={"dedupe_key": "status:camp-1"}))

    assert queued.status == "queued"
    assert duplicate.copilot_action_id == "action-1"
    assert queue.get_action("action-1").status == "queued"
    assert [record["transition"] for record in queue.audit_log] == ["queued", "deduplicated"]


def test_action_queue_approves_action_from_human_not_codex():
    queue = CoPilotActionQueue(now=lambda: NOW)
    action = queue.queue_action(_action(requires_approval=True))

    denied = queue.approve_action(action.copilot_action_id, approver_id="codex")
    approved = queue.approve_action(action.copilot_action_id, approver_id="user-1")

    assert denied.status == "approval_required"
    assert queue.get_action(action.copilot_action_id).status == "approved"
    assert approved.status == "approved"
    assert queue.audit_log[-1]["actor_id"] == "user-1"


def test_action_queue_rejects_action_from_authorized_service_account():
    queue = CoPilotActionQueue(
        now=lambda: NOW,
        authorized_service_accounts={"svc-reviewer"},
    )
    action = queue.queue_action(_action(requires_approval=True))

    rejected = queue.reject_action(
        action.copilot_action_id,
        approver_id="svc-reviewer",
        approver_type="service_account",
        reason="Policy owner rejected.",
    )

    assert rejected.status == "rejected"
    assert queue.results[action.copilot_action_id].status == "skipped"
    assert "Policy owner rejected" in queue.results[action.copilot_action_id].summary


def test_action_queue_auto_executes_safe_action_and_tracks_result():
    queue = CoPilotActionQueue(now=lambda: NOW)
    queue.queue_action(_action())

    results = queue.execute_eligible_safe_actions(session=_session())

    assert len(results) == 1
    assert results[0].status == "succeeded"
    assert queue.get_action("action-1").status == "succeeded"
    assert queue.results["action-1"].summary == "Safe planning action executed."
    assert "succeeded" in [record["transition"] for record in queue.audit_log]


def test_action_queue_repeated_failure_pauses_session_and_notifies_role():
    attempts = {"count": 0}

    def failing_executor(action: CoPilotAction) -> CoPilotActionResult:
        attempts["count"] += 1
        return CoPilotActionResult(
            result_id=f"result-{attempts['count']}",
            copilot_action_id=action.copilot_action_id,
            status="failed",
            artifact_ids=[],
            job_ids=[],
            summary="Transient executor failure.",
            warnings=[],
            created_at=NOW,
            metadata={"transient": True},
        )

    session = _session()
    queue = CoPilotActionQueue(
        now=lambda: NOW,
        executor=failing_executor,
        max_retries=2,
        repeated_failure_limit=2,
    )
    queue.queue_action(
        _action(
            action_type="run_repair_workflow",
            metadata={
                "safe_repair": True,
                "assigned_role": "campaign_owner",
                "priority": "high",
            },
        )
    )

    results = queue.execute_eligible_safe_actions(session=session)

    assert attempts["count"] == 2
    assert results[-1].status == "failed"
    assert session.status == "paused"
    assert queue.notifications == [
        {
            "assigned_role": "campaign_owner",
            "action_id": "action-1",
            "reason": "high-priority action failed repeatedly",
        }
    ]
