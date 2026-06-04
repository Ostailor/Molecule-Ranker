from __future__ import annotations

import json
from typing import Any

from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor
from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.subagents.coordinator import MultiAgentCoordinator


def test_failed_safe_tool_repaired() -> None:
    calls: list[str] = []

    def flaky_handler(step, spec):  # type: ignore[no-untyped-def]
        calls.append(step.tool_name)
        if len(calls) == 1:
            raise RuntimeError("transient tool error")
        return {"status": "succeeded", "output": {"ok": True}, "artifact_ids": ["artifact-1"]}

    executor = RuntimeActionExecutor(
        tool_handlers={"run_ranking": flaky_handler},
        repair_config={"enable_auto_repair": True, "auto_repair_mode": "safe_only"},
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
    )

    assert result.status == "succeeded"
    assert calls == ["run_ranking", "run_ranking"]
    assert result.metadata["repair_logs"][0]["status"] == "repaired"
    assert any(event.event_type == "runtime_step_repaired" for event in result.audit_events)


def test_unsafe_repair_only_suggested() -> None:
    executor = RuntimeActionExecutor(
        tool_handlers={
            "run_ranking": lambda _step, _spec: {
                "status": "succeeded",
                "output": {"summary": "Invented IC50 = 12 nM"},
                "artifact_ids": ["unsafe-artifact"],
            }
        },
        repair_config={"enable_auto_repair": True, "auto_repair_mode": "suggest_only"},
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
    )

    assert result.status == "failed"
    assert result.metadata["repair_logs"][0]["status"] == "suggested"
    assert result.metadata["repair_logs"][0]["repair_execution"] is None


def test_max_repair_attempts_enforced() -> None:
    calls: list[str] = []

    def failing_handler(step, spec):  # type: ignore[no-untyped-def]
        calls.append(step.tool_name)
        raise RuntimeError("persistent failure")

    executor = RuntimeActionExecutor(
        tool_handlers={"run_ranking": failing_handler},
        repair_config={
            "enable_auto_repair": True,
            "auto_repair_mode": "safe_only",
            "max_repair_attempts_per_step": 0,
        },
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
    )

    assert result.status == "failed"
    assert calls == ["run_ranking"]
    assert result.metadata["repair_logs"][0]["status"] == "max_attempts_exceeded"


def test_subagent_repair_escalation_works() -> None:
    session = MultiAgentCoordinator().coordinate(
        user_goal="Diagnose repair failure for unsafe scientific output.",
        mode="sequential",
    )

    escalations = session.metadata["repair_escalations"]
    assert {item["diagnostic_subagent_id"] for item in escalations} == {
        "platform-operator",
        "guardrail-sentinel",
    }
    assert session.consensus[0].human_review_required is True
    assert any(
        event["event_type"] == "subagent_repair_escalated"
        for event in session.metadata["audit_events"]
    )


def test_repair_logs_written(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def flaky_handler(step, spec):  # type: ignore[no-untyped-def]
        calls.append(step.tool_name)
        if len(calls) == 1:
            raise RuntimeError("transient tool error")
        return {"status": "succeeded", "output": {"ok": True}, "artifact_ids": ["artifact-1"]}

    executor = RuntimeActionExecutor(
        tool_handlers={"run_ranking": flaky_handler},
        repair_config={
            "enable_auto_repair": True,
            "auto_repair_mode": "safe_only",
            "repair_log_dir": str(tmp_path),
        },
    )

    result = executor.execute(
        _plan([_step("run_ranking")]),
        mode="execute_safe_tools",
        actor="user-1",
    )

    payload = json.loads((tmp_path / "runtime_repair_log.json").read_text())
    assert result.status == "succeeded"
    assert payload["repair_logs"][0]["status"] == "repaired"


def _plan(steps: list[RuntimeActionStep]) -> RuntimeActionPlan:
    registry = RuntimeToolRegistry.default()
    plan = RuntimeActionPlan(
        plan_id="plan-repair-loop",
        session_id="session-repair-loop",
        user_goal="Run repair loop test.",
        plan_summary="Run repair loop test.",
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
                    "required_permissions": registry.require(step.tool_name).required_permissions,
                    "side_effect_level": registry.require(step.tool_name).side_effect_level,
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
        plan_id="plan-repair-loop",
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
