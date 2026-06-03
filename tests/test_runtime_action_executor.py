from __future__ import annotations

from typing import Any

from molecule_ranker.runtime_agents.executor import (
    CancellationToken,
    RuntimeActionExecutor,
)
from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_executor_executes_safe_plan() -> None:
    calls: list[str] = []
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_ranking": lambda step, spec: _handler_output(
                calls, step.tool_name, artifact_ids=["ranking-run-1"]
            )
        }
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
    )

    assert result.status == "succeeded"
    assert calls == ["run_ranking"]
    assert result.results[0].status == "succeeded"
    assert result.artifact_ids == ["ranking-run-1"]
    assert result.plan.steps[0].status == "succeeded"
    assert {event.event_type for event in result.audit_events} >= {
        "runtime_execution_started",
        "runtime_step_succeeded",
        "runtime_execution_completed",
    }


def test_executor_blocks_unapproved_external_write() -> None:
    calls: list[str] = []
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_sync_write_enabled": lambda step, spec: _handler_output(calls, step.tool_name)
        }
    )

    result = executor.execute(
        _plan([_step("run_sync_write_enabled")]),
        mode="execute_with_approval",
        actor="user-1",
        approvals=set(),
    )

    assert result.status == "approval_required"
    assert calls == []
    assert result.results[0].status == "approval_required"
    assert result.plan.steps[0].status == "pending"


def test_executor_blocks_stage_gate_approval_by_codex() -> None:
    registry = RuntimeToolRegistry.default()
    registry.register(
        _custom_tool(
            tool_name="approve_stage_gate",
            category="campaign",
            side_effect_level="db_write",
            required_permissions=["campaign:approve"],
            policy_tags=["stage_gate", "destructive_action"],
            requires_approval_by_default=True,
        )
    )
    executor = RuntimeActionExecutor(
        registry=registry,
        tool_handlers={
            "approve_stage_gate": lambda step, spec: {
                "summary": "Approved",
                "artifact_ids": [],
                "job_ids": [],
            }
        },
    )

    result = executor.execute(
        _plan([_step("approve_stage_gate")], registry=registry),
        mode="execute_with_approval",
        actor="codex",
        approvals={"approve_stage_gate", "stage_gate", "destructive_action"},
    )

    assert result.status == "policy_blocked"
    assert result.results[0].status == "policy_blocked"
    assert "human actor" in (result.results[0].error_summary or "")


def test_executor_stops_on_required_failure() -> None:
    calls: list[str] = []
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_ranking": lambda step, spec: (_ for _ in ()).throw(RuntimeError("ranking failed")),
            "summarize_ranking": lambda step, spec: _handler_output(calls, step.tool_name),
        }
    )

    result = executor.execute(
        _plan([_step("run_ranking"), _step("summarize_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
    )

    assert result.status == "failed"
    assert calls == []
    assert [step.status for step in result.plan.steps] == ["failed", "skipped"]


def test_executor_continues_optional_failure() -> None:
    calls: list[str] = []
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_ranking": lambda step, spec: (_ for _ in ()).throw(RuntimeError("optional fail")),
            "summarize_ranking": lambda step, spec: _handler_output(calls, step.tool_name),
        }
    )

    result = executor.execute(
        _plan(
            [
                _step("run_ranking", metadata={"optional": True}),
                _step("summarize_ranking"),
            ]
        ),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
    )

    assert result.status == "succeeded"
    assert calls == ["summarize_ranking"]
    assert [step.status for step in result.plan.steps] == ["failed", "succeeded"]
    assert result.results[0].status == "failed"
    assert result.results[1].status == "succeeded"


def test_executor_cancellation_works() -> None:
    calls: list[str] = []
    token = CancellationToken()
    token.cancel()
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_ranking": lambda step, spec: _handler_output(calls, step.tool_name)
        }
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
        approvals=set(),
        cancellation_token=token,
    )

    assert result.status == "cancelled"
    assert calls == []
    assert result.plan.steps[0].status == "skipped"


def _handler_output(
    calls: list[str],
    tool_name: str,
    *,
    artifact_ids: list[str] | None = None,
    job_ids: list[str] | None = None,
) -> dict[str, Any]:
    calls.append(tool_name)
    return {
        "summary": f"{tool_name} succeeded",
        "artifact_ids": artifact_ids or [],
        "job_ids": job_ids or [],
        "output": {"ok": True},
    }


def _plan(
    steps: list[RuntimeActionStep],
    *,
    registry: RuntimeToolRegistry | None = None,
) -> RuntimeActionPlan:
    active_registry = registry or RuntimeToolRegistry.default()
    plan = RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Run plan",
        plan_summary="Run plan",
        steps=steps,
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                step.tool_name: {
                    "required_permissions": active_registry.require(
                        step.tool_name
                    ).required_permissions,
                    "side_effect_level": active_registry.require(
                        step.tool_name
                    ).side_effect_level,
                }
                for step in steps
            }
        },
    )
    for index, step in enumerate(plan.steps):
        step.plan_id = plan.plan_id
        step.step_index = index
    return plan


def _step(tool_name: str, metadata: dict[str, Any] | None = None) -> RuntimeActionStep:
    return RuntimeActionStep(
        step_id=f"step-{tool_name}",
        plan_id="plan-1",
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args={"goal": "test"},
        requires_approval=False,
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata=metadata or {},
    )


def _custom_tool(
    *,
    tool_name: str,
    category: str,
    side_effect_level: str,
    required_permissions: list[str],
    policy_tags: list[str],
    requires_approval_by_default: bool,
):
    from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec

    return RuntimeToolSpec.model_validate(
        {
            "tool_name": tool_name,
            "category": category,
            "description": "Custom test tool.",
            "input_schema": {"type": "object", "additionalProperties": True},
            "output_schema": {"type": "object", "additionalProperties": True},
            "required_permissions": required_permissions,
            "policy_tags": policy_tags,
            "side_effect_level": side_effect_level,
            "requires_approval_by_default": requires_approval_by_default,
            "idempotent": False,
        }
    )
