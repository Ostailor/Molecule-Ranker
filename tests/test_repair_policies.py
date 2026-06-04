from __future__ import annotations

from molecule_ranker.agent_repair.policies import (
    RepairPolicyContext,
    RepairPolicyEngine,
)
from molecule_ranker.agent_repair.schemas import RepairAction


def test_safe_retry_allowed() -> None:
    decision = RepairPolicyEngine().evaluate_action(
        _action(
            action_type="retry_external_read",
            side_effect_level="external_read",
            risk_level="low",
        ),
        context=RepairPolicyContext(
            failure_category="external_unavailable",
            autonomy_level="execute_safe_repairs",
            artifact_type="derived_report",
        ),
    )

    assert decision.status == "allowed"
    assert decision.allowed is True


def test_external_write_without_approval_is_blocked() -> None:
    decision = RepairPolicyEngine().evaluate_action(
        _action(
            action_type="rerun_tool",
            side_effect_level="external_write",
            risk_level="medium",
            tool_name="run_sync_write_enabled",
        ),
        context=RepairPolicyContext(
            failure_category="external_unavailable",
            autonomy_level="execute_safe_repairs",
            external_system_mode="write_enabled",
            user_role="scientist",
        ),
    )

    assert decision.status == "blocked"
    assert any("External write" in reason for reason in decision.blocked_reasons)


def test_assay_result_edit_blocked() -> None:
    decision = RepairPolicyEngine().evaluate_action(
        _action(
            action_type="rerun_tool",
            tool_name="import_assay_results",
            side_effect_level="db_write",
            tool_args={"assay_result": {"redacted": True}},
        ),
        context=RepairPolicyContext(
            failure_category="invalid_schema",
            autonomy_level="supervised_auto",
            artifact_type="source_artifact",
            user_role="admin",
        ),
    )

    assert decision.status == "blocked"
    assert any("assay results" in reason for reason in decision.blocked_reasons)


def test_expensive_rerun_requires_approval() -> None:
    decision = RepairPolicyEngine().evaluate_action(
        _action(
            action_type="rerun_job",
            side_effect_level="artifact_write",
            risk_level="medium",
        ),
        context=RepairPolicyContext(
            job_type="expensive_generation",
            failure_category="resource_exhausted",
            autonomy_level="execute_safe_repairs",
            artifact_type="derived_artifact",
            user_role="scientist",
        ),
    )

    assert decision.status == "approval_required"
    assert "expensive_or_sensitive_job_approval" in decision.required_approvals


def _action(
    *,
    action_type: str,
    side_effect_level: str = "none",
    risk_level: str = "low",
    tool_name: str | None = None,
    tool_args: dict[str, object] | None = None,
) -> RepairAction:
    return RepairAction(
        repair_action_id=f"repair-action-{action_type}",
        action_type=action_type,  # type: ignore[arg-type]
        target_object_type="workflow",
        target_object_id="workflow-1",
        tool_name=tool_name,
        tool_args=tool_args or {"target_id": "workflow-1"},
        expected_effect="Run operational repair.",
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval=False,
        approval_reason=None,
        risk_level=risk_level,  # type: ignore[arg-type]
        metadata={},
    )
