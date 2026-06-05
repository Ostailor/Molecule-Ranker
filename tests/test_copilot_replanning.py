from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.replanning import CampaignReplanDraftWorkflow
from molecule_ranker.copilot.schemas import CoPilotTrigger, Priority, TriggerType

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


class SyntheticActionQueue:
    def __init__(self) -> None:
        self.actions: list[Any] = []

    def queue_action(self, action: Any) -> Any:
        self.actions.append(action)
        return action


class SyntheticRuntimeTool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, tool_args))
        return {"summary": "Campaign replan applied.", "artifact_ids": ["plan-artifact"]}


def _trigger(
    trigger_type: TriggerType,
    *,
    priority: Priority = "medium",
    detector_event_type: str,
    rationale: str = "Trigger rationale",
    metadata: dict[str, Any] | None = None,
) -> CoPilotTrigger:
    merged_metadata = {"detector_event_type": detector_event_type, **(metadata or {})}
    return CoPilotTrigger(
        trigger_id=f"trigger-{detector_event_type}",
        campaign_id="camp-1",
        event_ids=["event-1"],
        trigger_signature=f"camp-1:{trigger_type}:{detector_event_type}:assay_result",
        trigger_type=trigger_type,
        priority=priority,
        rationale=rationale,
        recommended_action_types=["create_replan_draft"],
        requires_human_attention=priority in {"high", "critical"},
        metadata=merged_metadata,
    )


def _campaign_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "plan_id": "plan-1",
        "hypotheses": [
            {"hypothesis_id": "hyp-1", "candidate_ids": ["cand-1"]},
            {"hypothesis_id": "hyp-2", "candidate_ids": ["cand-2"]},
        ],
        "work_packages": [
            {"work_package_id": "wp-1", "hypothesis_id": "hyp-1", "candidate_ids": ["cand-1"]},
            {"work_package_id": "wp-2", "hypothesis_id": "hyp-2", "candidate_ids": ["cand-2"]},
        ],
        "candidates": [
            {"candidate_id": "cand-1", "hypothesis_id": "hyp-1"},
            {"candidate_id": "cand-2", "hypothesis_id": "hyp-2"},
        ],
        "budget": {"remaining": 1000},
    }
    state.update(overrides)
    return state


def test_positive_result_replan_draft_updates_affected_work_package_without_approval():
    workflow = CampaignReplanDraftWorkflow(now=lambda: NOW)
    trigger = _trigger(
        "result_followup_needed",
        detector_event_type="positive_qc_passed_exact_assay_result",
        metadata={"candidate_ids": ["cand-1"], "hypothesis_ids": ["hyp-1"]},
    )

    draft = workflow.create_draft(trigger, campaign_state=_campaign_state())

    assert draft.trigger_rationale == trigger.rationale
    assert draft.affected_hypotheses == ["hyp-1"]
    assert draft.affected_work_packages == ["wp-1"]
    assert draft.affected_candidates == ["cand-1"]
    assert draft.change_level == "minor"
    assert draft.approval_requirements == []
    assert "record source-grounded follow-up" in draft.proposed_changes
    assert draft.limitations == ["Recommendations are planning aids and require source review."]


def test_safety_concern_replan_draft_pauses_and_requires_review_approval():
    queue = SyntheticActionQueue()
    workflow = CampaignReplanDraftWorkflow(action_queue=queue, now=lambda: NOW)
    trigger = _trigger(
        "safety_review_needed",
        priority="critical",
        detector_event_type="safety_developability_concern",
        metadata={"candidate_ids": ["cand-2"], "hypothesis_ids": ["hyp-2"]},
    )

    draft = workflow.create_draft(trigger, campaign_state=_campaign_state())

    assert draft.affected_work_packages == ["wp-2"]
    assert draft.change_level == "approval_required"
    assert "pause affected work package for safety/developability review" in draft.proposed_changes
    assert (
        "human approval required before changing active campaign plan"
        in draft.approval_requirements
    )
    assert queue.actions[0].action_type == "request_approval"
    assert queue.actions[0].requires_approval is True


def test_failed_qc_replan_draft_does_not_reject_candidate_without_review():
    workflow = CampaignReplanDraftWorkflow(now=lambda: NOW)
    trigger = _trigger(
        "approval_needed",
        priority="high",
        detector_event_type="failed_qc_result",
        metadata={"candidate_ids": ["cand-1"], "hypothesis_ids": ["hyp-1"]},
    )

    draft = workflow.create_draft(trigger, campaign_state=_campaign_state())

    assert draft.change_level == "informational"
    assert "request QC review before interpreting result" in draft.proposed_changes
    assert all("reject" not in change.lower() for change in draft.proposed_changes)
    assert "failed QC cannot support candidate rejection without review" in draft.limitations


def test_major_plan_change_requires_approval_before_campaign_replan_tool_executes():
    queue = SyntheticActionQueue()
    runtime = SyntheticRuntimeTool()
    workflow = CampaignReplanDraftWorkflow(
        action_queue=queue,
        runtime_tool_registry=runtime,
        now=lambda: NOW,
    )
    trigger = _trigger(
        "replan_needed",
        priority="high",
        detector_event_type="negative_qc_passed_exact_assay_result",
        metadata={
            "candidate_ids": ["cand-1"],
            "hypothesis_ids": ["hyp-1"],
            "proposed_change_scope": "major",
        },
    )

    draft = workflow.create_draft(trigger, campaign_state=_campaign_state())
    blocked_result = workflow.apply_if_approved(draft, approved=False)
    applied_result = workflow.apply_if_approved(draft, approved=True)

    assert draft.change_level == "approval_required"
    assert draft.approval_requirements == [
        "human approval required before changing active campaign plan"
    ]
    assert queue.actions[0].action_type == "request_approval"
    assert blocked_result.status == "approval_required"
    assert runtime.calls == [
        ("run_campaign_replan", {"draft_id": draft.draft_id, "campaign_id": "camp-1"})
    ]
    assert applied_result.status == "succeeded"
