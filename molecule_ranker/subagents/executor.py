from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.codex_runtime import CodexRuntimeAgent, RuntimeContext
from molecule_ranker.runtime_agents.executor import (
    ExecutionMode,
    RuntimeActionExecutor,
    RuntimeExecutionResult,
)
from molecule_ranker.runtime_agents.schemas import (
    AutonomyLevel,
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentSession,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.subagents.consensus import synthesize_critique_consensus
from molecule_ranker.subagents.context import SubagentContextBuilder, SubagentContextPolicy
from molecule_ranker.subagents.coordinator import CoordinationMode, MultiAgentCoordinator
from molecule_ranker.subagents.critique import review_result
from molecule_ranker.subagents.messaging import InterAgentMessageBus
from molecule_ranker.subagents.prompts import get_prompt_template
from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentMessage,
    SubagentResult,
    SubagentTask,
    SubagentTaskStatus,
)


class MultiAgentRuntimeExecution(BaseModel):
    session: MultiAgentSession
    runtime_sessions: list[RuntimeAgentSession]
    results: list[SubagentResult]
    messages: list[SubagentMessage]
    critiques: list[SubagentCritique]
    consensus: SubagentConsensus
    artifact_paths: dict[str, str] = Field(default_factory=dict)


class MultiAgentRuntimeExecutor:
    def __init__(
        self,
        *,
        coordinator: MultiAgentCoordinator | None = None,
        registry: SubagentRegistry | None = None,
        context_builder: SubagentContextBuilder | None = None,
        runtime_agent: Any | None = None,
        action_executor: RuntimeActionExecutor | None = None,
        runtime_tool_registry: RuntimeToolRegistry | None = None,
    ) -> None:
        self.registry = registry or SubagentRegistry()
        self.runtime_tool_registry = runtime_tool_registry or RuntimeToolRegistry.default()
        self.coordinator = coordinator or MultiAgentCoordinator(registry=self.registry)
        self.context_builder = context_builder or SubagentContextBuilder(registry=self.registry)
        self.runtime_agent = runtime_agent or CodexRuntimeAgent()
        self.action_executor = action_executor or RuntimeActionExecutor(
            registry=self.runtime_tool_registry
        )

    def execute(
        self,
        *,
        user_goal: str,
        tasks: list[SubagentTask] | None = None,
        mode: CoordinationMode = "sequential",
        artifacts: list[dict[str, Any]] | None = None,
        visible_artifact_ids: list[str] | None = None,
        scoped_artifact_ids: list[str] | None = None,
        optional_task_ids: set[str] | list[str] | None = None,
        output_dir: Path | None = None,
        runtime_session_id: str | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        approvals: set[str] | list[str] | None = None,
    ) -> MultiAgentRuntimeExecution:
        started_at = _now()
        session_id = runtime_session_id or f"multi-agent-session-{uuid4().hex[:12]}"
        visible_ids = list(visible_artifact_ids or scoped_artifact_ids or ["user-goal"])
        scoped_ids = list(scoped_artifact_ids or visible_ids[:1])
        active_tasks = (
            [_copy_task_for_session(task, session_id) for task in tasks]
            if tasks is not None
            else self.coordinator.decompose_goal(
                user_goal=user_goal,
                parent_session_id=session_id,
                mode=mode,
                scoped_artifact_ids=scoped_ids,
            )
        )
        self.coordinator._add_dependencies(active_tasks, mode)  # noqa: SLF001

        supervisor = self.coordinator.choose_supervisor(user_goal)
        optional_ids = set(optional_task_ids or [])
        message_bus = InterAgentMessageBus()
        runtime_sessions: list[RuntimeAgentSession] = []
        results: list[SubagentResult] = []
        critiques: list[SubagentCritique] = []
        stopped_for_required_failure = False

        artifact_payloads = artifacts or _default_artifacts_for_tasks(active_tasks)
        for task in active_tasks:
            is_optional = task.task_id in optional_ids or task.metadata.get("optional") is True
            message_bus.send_message(
                parent_session_id=session_id,
                from_subagent_id=supervisor.subagent_id,
                to_subagent_id=task.assigned_subagent_id,
                message_type="task_request",
                content=f"Task delegated: {task.task_type}.",
                referenced_artifact_ids=task.input_artifact_ids,
                referenced_tool_names=task.allowed_tool_names,
                metadata={"task_id": task.task_id, "optional": is_optional},
            )
            runtime_session = self._runtime_session_for_task(
                task=task,
                user_goal=user_goal,
                artifacts=artifact_payloads,
                project_id=project_id,
                org_id=org_id,
                user_id=user_id,
            )
            runtime_sessions.append(runtime_session)
            result = self._execute_task(
                task=task,
                runtime_session=runtime_session,
                approvals=set(approvals or []),
                optional=is_optional,
            )
            results.append(result)
            task.status = _task_status_for_result(result, optional=is_optional)
            task.started_at = runtime_session.started_at
            task.completed_at = result.created_at
            if not (is_optional and result.status == "failed"):
                critiques.extend(
                    review_result(
                        result,
                        expected_output_schema=task.expected_output_schema,
                        known_artifact_ids=set(task.input_artifact_ids + result.artifact_ids),
                    )
                )
            if result.status == "guardrail_failed":
                stopped_for_required_failure = True
                break
            if result.status == "failed" and not is_optional:
                stopped_for_required_failure = True
                break

        consensus = synthesize_critique_consensus(
            parent_session_id=session_id,
            task_ids=[task.task_id for task in active_tasks],
            results=results,
            critiques=critiques,
            high_risk=any(task.risk_level in {"high", "critical"} for task in active_tasks),
        )
        session_status = _session_status(
            results,
            consensus,
            stopped_for_required_failure=stopped_for_required_failure,
            optional_task_ids=optional_ids,
        )
        completed_at = _now()
        session = MultiAgentSession(
            multi_agent_session_id=session_id,
            runtime_session_id=runtime_session_id,
            user_goal=user_goal,
            supervisor_subagent_id=supervisor.subagent_id,
            subagent_ids=sorted(
                {task.assigned_subagent_id for task in active_tasks} | {supervisor.subagent_id}
            ),
            tasks=active_tasks,
            messages=message_bus.messages,
            results=results,
            critiques=critiques,
            consensus=[consensus],
            status=session_status,
            started_at=started_at,
            completed_at=completed_at,
            metadata={
                "coordination_mode": mode,
                "optional_task_ids": sorted(optional_ids),
                "stopped_for_required_failure": stopped_for_required_failure,
                "runtime_session_ids": [session.session_id for session in runtime_sessions],
                "message_audit_events": [
                    event.model_dump(mode="json") for event in message_bus.audit_events
                ],
            },
        )
        artifact_paths = (
            write_multi_agent_runtime_artifacts(output_dir, session)
            if output_dir is not None
            else {}
        )
        return MultiAgentRuntimeExecution(
            session=session,
            runtime_sessions=runtime_sessions,
            results=results,
            messages=message_bus.messages,
            critiques=critiques,
            consensus=consensus,
            artifact_paths={name: str(path) for name, path in artifact_paths.items()},
        )

    def _runtime_session_for_task(
        self,
        *,
        task: SubagentTask,
        user_goal: str,
        artifacts: list[dict[str, Any]],
        project_id: str | None,
        org_id: str | None,
        user_id: str | None,
    ) -> RuntimeAgentSession:
        profile = self.registry.require(task.assigned_subagent_id)
        subagent_context = self.context_builder.build(
            subagent_id=profile.subagent_id,
            artifacts=artifacts,
            policy=SubagentContextPolicy(
                visible_artifact_ids=task.input_artifact_ids,
                allowed_tool_names=task.allowed_tool_names,
                autonomy_level=profile.default_autonomy_level,
            ),
            output_schema=task.expected_output_schema,
        )
        prompt = get_prompt_template(profile.subagent_id, registry=self.registry)
        return RuntimeAgentSession(
            session_id=f"runtime-session-{task.task_id}-{uuid4().hex[:8]}",
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            user_goal=task.objective or user_goal,
            autonomy_level=_runtime_autonomy(profile.default_autonomy_level),
            status="created",
            started_at=_now(),
            completed_at=None,
            metadata={
                "subagent_id": profile.subagent_id,
                "subagent_role": profile.role,
                "subagent_context": subagent_context.model_dump(mode="json"),
                "system_prompt": prompt.system_prompt,
                "task_prompt": prompt.render_task_prompt(
                    objective=task.objective,
                    artifact_ids=task.input_artifact_ids,
                    allowed_tool_names=task.allowed_tool_names,
                    output_schema=task.expected_output_schema,
                    artifact_summaries=subagent_context.relevant_summaries,
                ),
            },
        )

    def _execute_task(
        self,
        *,
        task: SubagentTask,
        runtime_session: RuntimeAgentSession,
        approvals: set[str],
        optional: bool,
    ) -> SubagentResult:
        runtime_session.status = "planning"
        codex_result = self._call_codex_runtime(task, runtime_session)
        if _codex_status(codex_result) == "guardrail_failed":
            runtime_session.status = "failed"
            runtime_session.completed_at = _now()
            return _subagent_result_from_guardrail_failure(task, codex_result)

        plan = _runtime_plan_for_task(task, runtime_session, self.runtime_tool_registry, optional)
        if not plan.steps:
            runtime_session.status = "failed"
            runtime_session.completed_at = _now()
            return _failed_result(
                task,
                "No registered runtime tools were available for this subagent task.",
                metadata={"codex_runtime_status": _codex_status(codex_result)},
            )

        runtime_session.status = "executing"
        execution = self.action_executor.execute(
            plan,
            mode=_execution_mode(runtime_session.autonomy_level),
            actor=task.assigned_subagent_id,
            approvals=approvals,
        )
        runtime_session.completed_at = execution.completed_at or _now()
        runtime_session.status = "succeeded" if execution.status == "succeeded" else "failed"
        return _subagent_result_from_execution(
            task,
            execution,
            codex_result=codex_result,
            optional=optional,
        )

    def _call_codex_runtime(
        self,
        task: SubagentTask,
        runtime_session: RuntimeAgentSession,
    ) -> Any:
        context = RuntimeContext(
            actor_id=task.assigned_subagent_id,
            org_id=runtime_session.org_id or "local-org",
            project_id=runtime_session.project_id,
            permissions=set(
                self.registry.require(task.assigned_subagent_id).required_permissions
            ),
            metadata={
                "runtime_session": runtime_session.model_dump(mode="json"),
                "subagent_context": runtime_session.metadata.get("subagent_context", {}),
            },
        )
        try:
            return self.runtime_agent.run(
                task.objective,
                context,
                requested_actions=_codex_supported_actions(task.allowed_tool_names),
            )
        except Exception as exc:  # pragma: no cover - defensive adapter for injected runtimes.
            return {
                "status": "failed",
                "guardrail_warnings": [],
                "warnings": [f"CodexRuntimeAgent call failed: {exc}"],
            }


def write_multi_agent_runtime_artifacts(
    output_dir: Path,
    session: MultiAgentSession,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "multi_agent_session": output_dir / "multi_agent_session.json",
        "subagent_results": output_dir / "subagent_results.json",
        "subagent_messages": output_dir / "subagent_messages.json",
        "subagent_critiques": output_dir / "subagent_critiques.json",
        "multi_agent_summary": output_dir / "multi_agent_summary.md",
    }
    paths["multi_agent_session"].write_text(
        json.dumps(session.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["subagent_results"].write_text(
        json.dumps(
            [result.model_dump(mode="json") for result in session.results],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["subagent_messages"].write_text(
        json.dumps(
            [message.model_dump(mode="json") for message in session.messages],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["subagent_critiques"].write_text(
        json.dumps(
            [critique.model_dump(mode="json") for critique in session.critiques],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["multi_agent_summary"].write_text(_summary_markdown(session), encoding="utf-8")
    return paths


def _runtime_plan_for_task(
    task: SubagentTask,
    runtime_session: RuntimeAgentSession,
    registry: RuntimeToolRegistry,
    optional: bool,
) -> RuntimeActionPlan:
    steps: list[RuntimeActionStep] = []
    tool_specs: dict[str, dict[str, Any]] = {}
    for index, tool_name in enumerate(task.allowed_tool_names):
        spec = registry.get(tool_name)
        if spec is None:
            continue
        steps.append(
            RuntimeActionStep(
                step_id=f"runtime-step-{task.task_id}-{index + 1}",
                plan_id=f"runtime-plan-{task.task_id}",
                step_index=index,
                action_type=tool_name,
                tool_name=tool_name,
                tool_args={
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "input_artifact_ids": task.input_artifact_ids,
                },
                requires_approval=spec.requires_approval_by_default,
                approval_reason=(
                    "Tool requires approval by policy."
                    if spec.requires_approval_by_default
                    else None
                ),
                expected_outputs=task.required_outputs,
                status="pending",
                result_id=None,
                warnings=[],
                metadata={"optional": optional},
            )
        )
        tool_specs[tool_name] = {
            "required_permissions": spec.required_permissions,
            "side_effect_level": spec.side_effect_level,
        }
    return RuntimeActionPlan(
        plan_id=f"runtime-plan-{task.task_id}",
        session_id=runtime_session.session_id,
        user_goal=task.objective,
        plan_summary=f"Execute scoped tools for {task.assigned_subagent_id}.",
        steps=steps,
        required_approvals=[
            step.tool_name for step in steps if step.requires_approval
        ],
        expected_artifacts=task.input_artifact_ids,
        risk_level=task.risk_level,
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=bool(steps),
        validation_errors=[] if steps else ["no registered tools"],
        metadata={
            "tool_specs": tool_specs,
            "runtime_context": {
                "project_id": runtime_session.project_id,
                "org_id": runtime_session.org_id,
                "user_id": runtime_session.user_id,
                "user_permissions": _runtime_permissions(runtime_session),
            },
            "subagent_id": task.assigned_subagent_id,
            "task_id": task.task_id,
        },
    )


def _subagent_result_from_execution(
    task: SubagentTask,
    execution: RuntimeExecutionResult,
    *,
    codex_result: Any,
    optional: bool,
) -> SubagentResult:
    failed_results = [
        result for result in execution.results if result.status != "succeeded"
    ]
    status = "succeeded"
    if failed_results:
        status = "failed"
    if execution.status in {"approval_required", "policy_blocked"}:
        status = "failed"
    warnings = [
        *execution.warnings,
        *[
            warning
            for result in execution.results
            for warning in result.warnings
        ],
        *_codex_warnings(codex_result),
    ]
    if task.assigned_subagent_id == "platform-operator" and status == "failed":
        summary = "Platform task failed; no scientific output was created."
        scientific_output_created = False
    else:
        summary = (
            f"{task.assigned_subagent_id} completed runtime execution."
            if status == "succeeded"
            else f"{task.assigned_subagent_id} failed runtime execution."
        )
        scientific_output_created = status == "succeeded"
    return SubagentResult(
        result_id=f"subagent-result-{uuid4().hex[:12]}",
        task_id=task.task_id,
        subagent_id=task.assigned_subagent_id,
        status=status,  # type: ignore[arg-type]
        output_json={
            "summary": summary,
            "findings": _execution_findings(execution),
            "recommended_next_actions": (
                ["Continue via fallback path or omit optional output."]
                if optional and status == "failed"
                else ["Review generated artifacts."]
            ),
        },
        output_text=summary,
        artifact_ids=list(dict.fromkeys(execution.artifact_ids)),
        tool_usage_ids=[result.result_id for result in execution.results],
        confidence=0.45 if status == "failed" else 0.82,
        warnings=list(dict.fromkeys(warnings)),
        guardrail_findings=_guardrail_findings(codex_result, execution),
        created_at=execution.completed_at or _now(),
        metadata={
            "execution_id": execution.execution_id,
            "execution_status": execution.status,
            "codex_runtime_status": _codex_status(codex_result),
            "optional": optional,
            "scientific_output_created": scientific_output_created,
            "artifact_provenance": _artifact_provenance(execution),
        },
    )


def _subagent_result_from_guardrail_failure(
    task: SubagentTask,
    codex_result: Any,
) -> SubagentResult:
    warnings = _codex_warnings(codex_result)
    return SubagentResult(
        result_id=f"subagent-result-{uuid4().hex[:12]}",
        task_id=task.task_id,
        subagent_id=task.assigned_subagent_id,
        status="guardrail_failed",
        output_json=None,
        output_text="Guardrail failure blocked unsafe final output.",
        artifact_ids=[],
        tool_usage_ids=[],
        confidence=0.0,
        warnings=warnings,
        guardrail_findings=[
            {"source": "CodexRuntimeAgent", "finding": warning} for warning in warnings
        ],
        created_at=_now(),
        metadata={"codex_runtime_status": _codex_status(codex_result)},
    )


def _failed_result(
    task: SubagentTask,
    error_summary: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> SubagentResult:
    scientific_output_created = task.assigned_subagent_id != "platform-operator"
    return SubagentResult(
        result_id=f"subagent-result-{uuid4().hex[:12]}",
        task_id=task.task_id,
        subagent_id=task.assigned_subagent_id,
        status="failed",
        output_json={
            "summary": (
                "Platform task failed; no scientific output was created."
                if not scientific_output_created
                else error_summary
            ),
            "findings": [],
            "recommended_next_actions": ["Escalate or choose fallback path."],
        },
        output_text=error_summary,
        artifact_ids=[],
        tool_usage_ids=[],
        confidence=0.0,
        warnings=[error_summary],
        guardrail_findings=[],
        created_at=_now(),
        metadata={"scientific_output_created": scientific_output_created, **(metadata or {})},
    )


def _session_status(
    results: list[SubagentResult],
    consensus: SubagentConsensus,
    *,
    stopped_for_required_failure: bool,
    optional_task_ids: set[str],
) -> str:
    if any(result.status == "guardrail_failed" for result in results):
        return "blocked_guardrail_failed"
    if stopped_for_required_failure:
        return "failed"
    if consensus.human_review_required:
        return "awaiting_human_review"
    failed_results = [result for result in results if result.status == "failed"]
    if failed_results and optional_task_ids:
        return "succeeded_with_optional_failures"
    if failed_results:
        return "failed"
    return "succeeded"


def _task_status_for_result(
    result: SubagentResult,
    *,
    optional: bool,
) -> SubagentTaskStatus:
    if result.status == "succeeded":
        return "succeeded"
    if result.status == "guardrail_failed":
        return "failed"
    if result.status == "failed" and optional:
        return "failed"
    return "failed"


def _execution_mode(autonomy_level: str) -> ExecutionMode:
    if autonomy_level in {"execute_safe_tools", "execute_with_approval", "full_auto_restricted"}:
        return cast(ExecutionMode, autonomy_level)
    return "execute_safe_tools"


def _runtime_autonomy(value: str) -> AutonomyLevel:
    allowed = {
        "observe_only",
        "suggest_only",
        "execute_safe_tools",
        "execute_with_approval",
        "full_auto_restricted",
    }
    return cast(AutonomyLevel, value if value in allowed else "execute_safe_tools")


def _runtime_permissions(runtime_session: RuntimeAgentSession) -> list[str]:
    context = runtime_session.metadata.get("subagent_context")
    if isinstance(context, dict):
        permissions = context.get("metadata", {}).get("required_permissions")
        if isinstance(permissions, list):
            return [str(permission) for permission in permissions]
    return []


def _copy_task_for_session(task: SubagentTask, session_id: str) -> SubagentTask:
    return task.model_copy(update={"parent_session_id": session_id, "status": "queued"})


def _default_artifacts_for_tasks(tasks: list[SubagentTask]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for task in tasks:
        artifact_type = _artifact_type_for_task(task)
        for artifact_id in task.input_artifact_ids:
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "summary": f"Scoped artifact for {task.assigned_subagent_id}.",
                }
            )
    return artifacts


def _artifact_type_for_task(task: SubagentTask) -> str:
    role = task.assigned_subagent_id
    if "evidence" in role:
        return "evidence"
    if "molecule" in role:
        return "generation"
    if "experiment" in role:
        return "result_summary"
    if "platform" in role:
        return "ops"
    if "guardrail" in role:
        return "guardrail"
    return "artifact"


def _codex_supported_actions(tool_names: list[str]) -> list[str]:
    supported = {
        "create_project",
        "run_ranking",
        "run_generation",
        "run_developability",
        "run_literature_update",
        "build_graph",
        "generate_hypotheses",
        "optimize_portfolio",
        "plan_campaign",
        "generate_support_bundle",
    }
    return [tool_name for tool_name in tool_names if tool_name in supported]


def _codex_status(codex_result: Any) -> str:
    if codex_result is None:
        return "not_run"
    if isinstance(codex_result, dict):
        return str(codex_result.get("status", "unknown"))
    return str(getattr(codex_result, "status", "unknown"))


def _codex_warnings(codex_result: Any) -> list[str]:
    if codex_result is None:
        return []
    if isinstance(codex_result, dict):
        values = [
            *list(codex_result.get("guardrail_warnings") or []),
            *list(codex_result.get("warnings") or []),
        ]
    else:
        values = [
            *list(getattr(codex_result, "guardrail_warnings", []) or []),
            *list(getattr(codex_result, "warnings", []) or []),
        ]
    return [str(value) for value in values]


def _guardrail_findings(
    codex_result: Any,
    execution: RuntimeExecutionResult,
) -> list[dict[str, Any]]:
    findings = [
        {"source": "CodexRuntimeAgent", "finding": warning}
        for warning in _codex_warnings(codex_result)
    ]
    for result in execution.results:
        if result.status == "validation_failed":
            findings.append(
                {
                    "source": "RuntimeActionExecutor",
                    "tool_name": result.tool_name,
                    "finding": result.error_summary or "validation failed",
                }
            )
    return findings


def _artifact_provenance(execution: RuntimeExecutionResult) -> dict[str, Any]:
    provenance: dict[str, Any] = {}
    for result in execution.results:
        metadata_provenance = result.metadata.get("artifact_provenance")
        if isinstance(metadata_provenance, dict):
            provenance.update(metadata_provenance)
        for artifact_id in result.artifact_ids:
            provenance.setdefault(artifact_id, result.tool_name)
    return provenance


def _execution_findings(execution: RuntimeExecutionResult) -> list[str]:
    findings: list[str] = []
    for result in execution.results:
        if result.status == "succeeded":
            findings.append(f"{result.tool_name} succeeded.")
        else:
            findings.append(f"{result.tool_name} {result.status}: {result.error_summary or ''}")
    return findings


def _summary_markdown(session: MultiAgentSession) -> str:
    lines = [
        f"# Multi-Agent Summary: {session.multi_agent_session_id}",
        "",
        f"Status: {session.status}",
        f"Goal: {session.user_goal}",
        "",
        "## Results",
    ]
    for result in session.results:
        lines.append(f"- {result.subagent_id}: {result.status} ({result.result_id})")
    lines.extend(["", "## Consensus"])
    for consensus in session.consensus:
        lines.append(f"- {consensus.consensus_status}: {consensus.summary}")
    return "\n".join(lines) + "\n"


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "MultiAgentRuntimeExecution",
    "MultiAgentRuntimeExecutor",
    "write_multi_agent_runtime_artifacts",
]
