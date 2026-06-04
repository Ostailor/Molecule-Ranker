from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor
from molecule_ranker.runtime_agents.schemas import RuntimeActionStep, RuntimeToolSpec
from molecule_ranker.subagents.executor import MultiAgentRuntimeExecutor
from molecule_ranker.subagents.schemas import SubagentTask


class MockCodexRuntimeAgent:
    def __init__(self, *, status: str = "succeeded", warnings: list[str] | None = None) -> None:
        self.status = status
        self.warnings = warnings or []
        self.calls: list[dict[str, Any]] = []

    def run(self, objective, context, *, requested_actions=None, action_parameters=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "objective": objective,
                "context": context,
                "requested_actions": requested_actions or [],
                "action_parameters": action_parameters or {},
            }
        )
        return SimpleNamespace(status=self.status, guardrail_warnings=self.warnings)


def test_executor_executes_mocked_subagents_and_writes_artifacts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    codex = MockCodexRuntimeAgent()
    executor = MultiAgentRuntimeExecutor(
        runtime_agent=codex,
        action_executor=RuntimeActionExecutor(
            tool_handlers={"summarize_literature": _successful_handler("evidence-output-1")}
        ),
    )

    execution = executor.execute(
        user_goal="Review evidence.",
        tasks=[_task("task-1", "evidence-reviewer", "summarize_literature")],
        output_dir=tmp_path,
        visible_artifact_ids=["artifact-1"],
        scoped_artifact_ids=["artifact-1"],
    )

    assert execution.session.status == "succeeded"
    assert execution.results[0].status == "succeeded"
    assert execution.results[0].artifact_ids == ["evidence-output-1"]
    assert codex.calls
    assert codex.calls[0]["context"].metadata["subagent_context"]["subagent_id"] == (
        "evidence-reviewer"
    )
    assert (tmp_path / "multi_agent_session.json").exists()
    assert (tmp_path / "subagent_results.json").exists()
    assert (tmp_path / "subagent_messages.json").exists()
    assert (tmp_path / "subagent_critiques.json").exists()
    assert (tmp_path / "multi_agent_summary.md").exists()


def test_failed_optional_task_handled() -> None:
    executor = MultiAgentRuntimeExecutor(
        runtime_agent=MockCodexRuntimeAgent(),
        action_executor=RuntimeActionExecutor(
            tool_handlers={
                "run_readiness": _failing_handler("readiness unavailable"),
                "summarize_literature": _successful_handler("evidence-output-1"),
            }
        ),
    )

    execution = executor.execute(
        user_goal="Check platform readiness and evidence.",
        tasks=[
            _task("task-optional", "platform-operator", "run_readiness", optional=True),
            _task("task-required", "evidence-reviewer", "summarize_literature"),
        ],
        optional_task_ids={"task-optional"},
    )

    assert execution.session.status == "succeeded_with_optional_failures"
    assert [result.status for result in execution.results] == ["failed", "succeeded"]
    assert execution.results[0].metadata["scientific_output_created"] is False
    assert execution.results[1].artifact_ids == ["evidence-output-1"]


def test_failed_required_task_stops() -> None:
    calls: list[str] = []
    executor = MultiAgentRuntimeExecutor(
        runtime_agent=MockCodexRuntimeAgent(),
        action_executor=RuntimeActionExecutor(
            tool_handlers={
                "summarize_literature": _failing_handler("required evidence failure"),
                "run_ranking": _recording_success_handler(calls, "ranking-output-1"),
            }
        ),
    )

    execution = executor.execute(
        user_goal="Review evidence then rank.",
        tasks=[
            _task("task-required", "evidence-reviewer", "summarize_literature"),
            _task("task-next", "program-manager", "run_ranking"),
        ],
    )

    assert execution.session.status == "failed"
    assert len(execution.results) == 1
    assert execution.results[0].status == "failed"
    assert calls == []


def test_guardrail_failure_blocks_final_output() -> None:
    executor = MultiAgentRuntimeExecutor(
        runtime_agent=MockCodexRuntimeAgent(
            status="guardrail_failed",
            warnings=["unsafe final output"],
        ),
        action_executor=RuntimeActionExecutor(
            tool_handlers={"run_guardrail_benchmark": _successful_handler("guardrail-1")}
        ),
    )

    execution = executor.execute(
        user_goal="Guardrail review final output.",
        tasks=[_task("task-guardrail", "guardrail-sentinel", "run_guardrail_benchmark")],
    )

    assert execution.session.status == "blocked_guardrail_failed"
    assert execution.results[0].status == "guardrail_failed"
    assert execution.results[0].output_json is None
    assert "unsafe final output" in execution.results[0].warnings
    assert execution.consensus.human_review_required is True


def _successful_handler(artifact_id: str):  # type: ignore[no-untyped-def]
    def handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
        return {
            "status": "succeeded",
            "output": {"summary": f"{step.tool_name} ok", "tool": spec.tool_name},
            "artifact_ids": [artifact_id],
            "metadata": {"artifact_provenance": {artifact_id: "test"}},
        }

    return handler


def _recording_success_handler(calls: list[str], artifact_id: str):  # type: ignore[no-untyped-def]
    def handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
        calls.append(step.tool_name)
        return _successful_handler(artifact_id)(step, spec)

    return handler


def _failing_handler(message: str):  # type: ignore[no-untyped-def]
    def handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
        del step, spec
        raise RuntimeError(message)

    return handler


def _task(
    task_id: str,
    subagent_id: str,
    tool_name: str,
    *,
    optional: bool = False,
) -> SubagentTask:
    return SubagentTask(
        task_id=task_id,
        parent_session_id="session-placeholder",
        assigned_subagent_id=subagent_id,
        task_type=subagent_id.replace("-", "_"),
        objective=f"Execute {subagent_id}.",
        input_artifact_ids=["artifact-1"],
        allowed_tool_names=[tool_name],
        forbidden_tool_names=[],
        expected_output_schema={
            "type": "object",
            "required": ["summary", "findings", "recommended_next_actions"],
            "properties": {
                "summary": {"type": "string"},
                "findings": {"type": "array"},
                "recommended_next_actions": {"type": "array"},
            },
        },
        required_outputs=["summary", "findings", "recommended_next_actions"],
        risk_level="high" if subagent_id == "guardrail-sentinel" else "low",
        requires_human_approval=False,
        status="queued",
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        metadata={"optional": optional},
    )
