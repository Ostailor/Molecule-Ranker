from __future__ import annotations

import pytest

from molecule_ranker.copilot.policy import CoPilotPolicyEngine


@pytest.mark.parametrize(
    ("action_type", "metadata"),
    [
        ("summarize_status", {}),
        ("create_replan_draft", {}),
        ("run_graph_refresh", {}),
        ("run_contradiction_scan", {}),
        ("create_review_request", {}),
        ("notify_user", {}),
        ("run_repair_workflow", {"safe_repair": True}),
        ("create_support_bundle", {"include_logs": False, "include_transcripts": False}),
    ],
)
def test_policy_allows_default_safe_actions(action_type, metadata):
    decision = CoPilotPolicyEngine().evaluate_action(
        action_type=action_type,
        autonomy_level="execute_safe_actions",
        metadata=metadata,
    )

    assert decision.status == "allowed"
    assert decision.requires_approval is False
    assert decision.blocked is False


@pytest.mark.parametrize(
    ("action_type", "metadata", "reason_fragment"),
    [
        (
            "run_campaign_replan",
            {"changes_active_plan": True},
            "active campaign plan",
        ),
        (
            "run_portfolio_reoptimization",
            {"changes_selected_candidates": True},
            "selected candidates",
        ),
        (
            "update_campaign_status",
            {"new_status": "completed"},
            "campaign status",
        ),
        ("notify_user", {"side_effect_level": "external_write"}, "external write"),
        ("request_approval", {"decision_type": "stage_gate"}, "stage gate"),
        (
            "create_followup_request",
            {"generated_molecule_assay_advancement": True},
            "generated molecule",
        ),
        ("run_evaluation_update", {"high_cost_job": True}, "high-cost"),
        ("create_support_bundle", {"include_logs": True}, "logs/transcripts"),
        ("pause_campaign", {"destructive": True}, "destructive"),
    ],
)
def test_policy_marks_risky_actions_approval_required(
    action_type,
    metadata,
    reason_fragment,
):
    decision = CoPilotPolicyEngine().evaluate_action(
        action_type=action_type,
        autonomy_level="execute_with_approval",
        metadata=metadata,
    )

    assert decision.status == "approval_required"
    assert decision.requires_approval is True
    assert decision.blocked is False
    assert reason_fragment in decision.reason


@pytest.mark.parametrize(
    ("action_type", "metadata", "reason_fragment"),
    [
        ("approve_stage_gate", {}, "stage gate"),
        ("approve_own_action", {"actor_id": "copilot", "approver_id": "copilot"}, "own actions"),
        ("fabricate_result", {}, "fabricate"),
        ("edit_assay_result", {}, "assay result"),
        ("edit_source_artifact", {}, "source artifact"),
        ("bypass_guardrail", {}, "guardrail"),
        (
            "notify_user",
            {"side_effect_level": "external_write", "execution_requested": True},
            "external write without approval",
        ),
    ],
)
def test_policy_blocks_disallowed_actions(action_type, metadata, reason_fragment):
    decision = CoPilotPolicyEngine().evaluate_action(
        action_type=action_type,
        autonomy_level="supervised_auto",
        metadata=metadata,
    )

    assert decision.status == "blocked"
    assert decision.blocked is True
    assert decision.requires_approval is False
    assert reason_fragment in decision.reason


@pytest.mark.parametrize(
    ("autonomy_level", "expected_status"),
    [
        ("observe_only", "blocked"),
        ("suggest_only", "approval_required"),
        ("execute_safe_actions", "allowed"),
        ("execute_with_approval", "allowed"),
        ("supervised_auto", "allowed"),
    ],
)
def test_policy_enforces_autonomy_levels(autonomy_level, expected_status):
    decision = CoPilotPolicyEngine().evaluate_action(
        action_type="summarize_status",
        autonomy_level=autonomy_level,
        metadata={},
    )

    assert decision.status == expected_status


def test_policy_uses_project_org_and_campaign_policy_blocks():
    decision = CoPilotPolicyEngine().evaluate_action(
        action_type="run_graph_refresh",
        autonomy_level="supervised_auto",
        user_policy={"blocked_action_types": ["run_graph_refresh"]},
        project_policy={"allowed_action_types": ["summarize_status"]},
        org_policy={"blocked_side_effect_levels": ["db_write"]},
        campaign_policy={"blocked_action_types": ["run_graph_refresh"]},
        metadata={},
    )

    assert decision.status == "blocked"
    assert decision.blocked is True
    assert "policy" in decision.reason


def test_policy_repeated_failures_require_approval_and_guardrail_blocks():
    repeated_failure = CoPilotPolicyEngine().evaluate_action(
        action_type="run_repair_workflow",
        autonomy_level="supervised_auto",
        metadata={"safe_repair": True, "recent_failure_count": 3},
    )
    guardrail_failure = CoPilotPolicyEngine().evaluate_action(
        action_type="run_repair_workflow",
        autonomy_level="supervised_auto",
        metadata={"safe_repair": True, "guardrail_failure": True},
    )

    assert repeated_failure.status == "approval_required"
    assert "repeated failure" in repeated_failure.reason
    assert guardrail_failure.status == "blocked"
    assert "guardrail" in guardrail_failure.reason
