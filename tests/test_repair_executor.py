from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agent_repair.executor import RepairExecutor
from molecule_ranker.agent_repair.schemas import (
    RegressionCheck,
    RepairAction,
    RepairPlan,
)


def test_dry_run_does_not_execute_actions() -> None:
    calls: list[str] = []
    plan = _plan(actions=[_action(tool_name="run_release_check")])
    executor = RepairExecutor(
        tool_handlers={"run_release_check": lambda action, spec: calls.append(action.tool_name)}
    )

    execution = executor.execute(plan, mode="dry_run")

    assert execution.status == "queued"
    assert execution.executed_actions == []
    assert calls == []


def test_safe_repair_executes_registered_tool_and_updates_memory() -> None:
    memory: list[dict[str, Any]] = []
    plan = _plan(actions=[_action(tool_name="run_release_check")])
    executor = RepairExecutor(
        tool_handlers={
            "run_release_check": lambda _action, _spec: {
                "artifact_ids": ["repair-artifact-1"],
                "status": "succeeded",
            }
        },
        repair_memory=memory,
    )

    execution = executor.execute(plan, mode="execute_safe_repairs")

    assert execution.status == "succeeded"
    assert execution.executed_actions[0]["tool_name"] == "run_release_check"
    assert execution.artifacts_created == ["repair-artifact-1"]
    assert execution.regression_check_ids
    assert memory[0]["succeeded"] is True


def test_safe_internal_retry_executes_without_registered_tool() -> None:
    plan = _plan(
        actions=[
            _action(action_type="retry_external_read", side_effect_level="external_read")
        ]
    )

    execution = RepairExecutor().execute(plan, mode="execute_safe_repairs")

    assert execution.status == "succeeded"
    assert execution.executed_actions[0]["action_type"] == "retry_external_read"
    assert execution.executed_actions[0]["status"] == "succeeded"
    assert execution.regression_check_ids


def test_approval_required_for_risky_repair() -> None:
    plan = _plan(
        actions=[
            _action(
                action_type="create_issue_report",
                side_effect_level="external_write",
                requires_approval=True,
                risk_level="high",
            )
        ]
    )

    execution = RepairExecutor().execute(plan, mode="execute_safe_repairs")

    assert execution.status == "approval_required"
    assert execution.executed_actions == []
    assert execution.approvals_requested


def test_guardrail_failure_blocks_repair_without_review_approval() -> None:
    plan = _plan(
        actions=[
            _action(
                action_type="quarantine_artifact",
                side_effect_level="artifact_write",
                requires_approval=True,
                risk_level="high",
            )
        ],
        metadata={"failure_category": "guardrail_failed"},
    )

    execution = RepairExecutor().execute(plan, mode="execute_with_approval")

    assert execution.status == "guardrail_blocked"
    assert execution.executed_actions == []
    assert execution.approvals_requested


def test_regression_check_runs_after_repair() -> None:
    checks_run: list[str] = []
    plan = _plan(actions=[_action(tool_name="run_release_check")])

    def regression_runner(_plan: RepairPlan, execution_id: str) -> RegressionCheck:
        checks_run.append(execution_id)
        return RegressionCheck(
            regression_check_id="regression-1",
            repair_execution_id=execution_id,
            check_type="workflow_smoke",
            passed=True,
            findings=[],
            artifacts_checked=[],
            created_at=_aware(),
            metadata={},
        )

    execution = RepairExecutor(
        tool_handlers={"run_release_check": lambda _action, _spec: {"status": "succeeded"}},
        regression_runner=regression_runner,
    ).execute(plan, mode="execute_safe_repairs")

    assert execution.status == "succeeded"
    assert execution.regression_check_ids == ["regression-1"]
    assert checks_run == [execution.repair_execution_id]


def test_rollback_plan_recorded_on_failure() -> None:
    plan = _plan(
        actions=[_action(tool_name="run_release_check")],
        rollback_plan=[
            _action(
                action_type="rollback_artifact",
                side_effect_level="artifact_write",
                requires_approval=False,
            )
        ],
    )

    execution = RepairExecutor(
        tool_handlers={
            "run_release_check": lambda _action, _spec: {
                "status": "failed",
                "error": "validation command failed",
            }
        }
    ).execute(plan, mode="execute_safe_repairs")

    assert execution.status == "failed"
    assert execution.metadata["rollback_plan_recorded"] is True
    assert execution.metadata["rollback_plan"]
    assert any(action.get("rollback_recorded") for action in execution.executed_actions)


def _plan(
    *,
    actions: list[RepairAction],
    rollback_plan: list[RepairAction] | None = None,
    metadata: dict[str, object] | None = None,
) -> RepairPlan:
    return RepairPlan(
        repair_plan_id="repair-plan-1",
        diagnosis_id="diagnosis-1",
        session_id="session-1",
        plan_summary="Repair operational workflow failure.",
        actions=actions,
        expected_artifacts=["repair-artifact-1"],
        rollback_plan=rollback_plan or [],
        requires_human_approval=any(action.requires_approval for action in actions),
        scientific_guardrails=["Do not create scientific evidence."],
        validated=True,
        validation_errors=[],
        created_by="deterministic",
        created_at=_aware(),
        metadata=metadata or {"failure_category": "invalid_schema"},
    )


def _action(
    *,
    action_type: str = "revalidate_artifact",
    tool_name: str | None = None,
    side_effect_level: str = "none",
    requires_approval: bool = False,
    risk_level: str = "low",
) -> RepairAction:
    return RepairAction(
        repair_action_id=f"repair-action-{action_type}",
        action_type=action_type,  # type: ignore[arg-type]
        target_object_type="workflow",
        target_object_id="workflow-1",
        tool_name=tool_name,
        tool_args={"target_id": "workflow-1"},
        expected_effect="Run deterministic repair action.",
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval=requires_approval,
        approval_reason="Approval required." if requires_approval else None,
        risk_level=risk_level,  # type: ignore[arg-type]
        metadata={},
    )


def _aware() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)
