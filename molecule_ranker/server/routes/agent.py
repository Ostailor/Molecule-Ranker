from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.runtime_agents.approvals import (
    ApprovalPolicyError,
    RuntimeApprovalController,
    approval_type_for_tool,
)
from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor, RuntimeExecutionResult
from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.hosted import RuntimeAgentHostedStore
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
    RuntimeAgentSession,
    RuntimeApprovalRequest,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.server.dependencies import current_user, platform_database

router = APIRouter(prefix="/agent", tags=["runtime-agent"])


class RuntimeAgentSessionCreateRequest(BaseModel):
    goal: str
    project_id: str | None = None
    autonomy_level: Literal[
        "observe_only",
        "suggest_only",
        "execute_safe_tools",
        "execute_with_approval",
        "full_auto_restricted",
    ] = "suggest_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeAgentApprovalDecisionRequest(BaseModel):
    decided_by: str
    rationale: str


@router.post("/sessions")
def create_runtime_agent_session(
    body: RuntimeAgentSessionCreateRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_agent_permission(database, user, "agent:plan", body.project_id)
    store = _store(request)
    now = datetime.now(UTC)
    session = RuntimeAgentSession(
        session_id=f"runtime-session-{uuid.uuid4().hex[:12]}",
        project_id=body.project_id,
        org_id=None,
        user_id=user.user_id,
        user_goal=body.goal,
        autonomy_level=body.autonomy_level,
        status="created",
        started_at=now,
        completed_at=None,
        metadata={**body.metadata, "hosted": True},
    )
    store.save_session(session)
    store.save_audit_events(
        session.session_id,
        [
            _audit(
                session,
                "runtime_session_created",
                user.user_id,
                "Runtime agent session created.",
            )
        ],
    )
    return {"session": session.model_dump(mode="json")}


@router.get("/sessions")
def list_runtime_agent_sessions(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    sessions = [
        session
        for session in store.list_sessions()
        if _can_agent(database, user, "agent:read", session.project_id)
    ]
    return {
        "sessions": [session.model_dump(mode="json") for session in sessions],
        "approvals": [
            approval.model_dump(mode="json")
            for approval in store.list_approvals()
            if _can_agent(
                database,
                user,
                "agent:read",
                _session_project(store, approval.session_id),
            )
        ],
    }


@router.get("/sessions/{session_id}")
def get_runtime_agent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_agent_permission(database, user, "agent:read", session.project_id)
    return _session_payload(store, session)


@router.post("/sessions/{session_id}/plan")
def plan_runtime_agent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_agent_permission(database, user, "agent:plan", session.project_id)
    plan = _deterministic_plan(session)
    session = session.model_copy(update={"status": "planning"})
    store.save_session(session)
    store.save_plan(plan)
    store.save_audit_events(
        session.session_id,
        [_audit(session, "runtime_plan_created", user.user_id, "Runtime action plan created.")],
    )
    return {"session": session.model_dump(mode="json"), "plan": plan.model_dump(mode="json")}


@router.post("/sessions/{session_id}/execute")
def execute_runtime_agent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_agent_permission(database, user, "agent:execute", session.project_id)
    try:
        plan = store.get_plan(session_id)
    except KeyError:
        plan = _deterministic_plan(session)
        store.save_plan(plan)
    approvals = {
        approval.approval_type
        for approval in store.list_approvals(session_id)
        if approval.status == "approved"
    }
    execution = _execute_plan(plan, mode=session.autonomy_level, approvals=approvals)
    session_status = _session_status_from_execution(execution)
    session = session.model_copy(
        update={
            "status": session_status,
            "completed_at": datetime.now(UTC)
            if session_status in {"succeeded", "failed", "cancelled"}
            else None,
        }
    )
    store.save_session(session)
    store.save_tool_results(session_id, execution.results)
    store.save_audit_events(session_id, execution.audit_events)
    guardrail_report = RuntimeGuardrailChecker().check_plan(
        plan,
        user_permissions=_all_tool_permissions(),
        approvals=approvals,
    )
    store.save_guardrail_report(session_id, guardrail_report.model_dump(mode="json"))
    approval_requests = _approval_requests_for_execution(execution)
    for approval in approval_requests:
        store.save_approval(approval)
    return {
        "session": session.model_dump(mode="json"),
        "status": execution.status,
        "results": [result.model_dump(mode="json") for result in execution.results],
        "approvals": [approval.model_dump(mode="json") for approval in approval_requests],
    }


@router.post("/approvals/{approval_id}/approve")
def approve_runtime_agent_request(
    approval_id: str,
    body: RuntimeAgentApprovalDecisionRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    return _decide_approval(
        approval_id,
        body,
        request,
        user,
        database,
        approved=True,
    )


@router.post("/approvals/{approval_id}/reject")
def reject_runtime_agent_request(
    approval_id: str,
    body: RuntimeAgentApprovalDecisionRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    return _decide_approval(
        approval_id,
        body,
        request,
        user,
        database,
        approved=False,
    )


@router.post("/sessions/{session_id}/cancel")
def cancel_runtime_agent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_agent_permission(database, user, "agent:execute", session.project_id)
    cancelled = session.model_copy(
        update={"status": "cancelled", "completed_at": datetime.now(UTC)}
    )
    store.save_session(cancelled)
    store.save_audit_events(
        session_id,
        [_audit(cancelled, "runtime_session_cancelled", user.user_id, "Session cancelled.")],
    )
    return {"session": cancelled.model_dump(mode="json")}


def _decide_approval(
    approval_id: str,
    body: RuntimeAgentApprovalDecisionRequest,
    request: Request,
    user: UserAccount,
    database: PlatformDatabase,
    *,
    approved: bool,
) -> dict[str, Any]:
    store = _store(request)
    try:
        approval = store.get_approval(approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval not found.") from exc
    project_id = _session_project(store, approval.session_id)
    _require_agent_permission(database, user, "agent:approve", project_id)
    try:
        decision = RuntimeApprovalController().decide(
            approval,
            decided_by=body.decided_by,
            approved=approved,
            rationale=body.rationale,
        )
    except ApprovalPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.save_approval_decision(decision.request)
    store.save_audit_events(approval.session_id, [decision.audit_event])
    return {"approval": decision.request.model_dump(mode="json")}


def _store(request: Request) -> RuntimeAgentHostedStore:
    return RuntimeAgentHostedStore(request.app.state.root_dir)


def _session_or_404(store: RuntimeAgentHostedStore, session_id: str) -> RuntimeAgentSession:
    try:
        return store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime session not found.") from exc


def _session_project(store: RuntimeAgentHostedStore, session_id: str) -> str | None:
    try:
        return store.get_session(session_id).project_id
    except KeyError:
        return None


def _session_payload(
    store: RuntimeAgentHostedStore,
    session: RuntimeAgentSession,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session": session.model_dump(mode="json"),
        "approvals": [
            approval.model_dump(mode="json")
            for approval in store.list_approvals(session.session_id)
        ],
        "audit_events": [
            event.model_dump(mode="json") for event in store.list_audit_events(session.session_id)
        ],
        "tool_results": [
            result.model_dump(mode="json") for result in store.list_tool_results(session.session_id)
        ],
        "guardrail_report": store.get_guardrail_report(session.session_id),
    }
    try:
        payload["plan"] = store.get_plan(session.session_id).model_dump(mode="json")
    except KeyError:
        payload["plan"] = None
    return payload


def _require_agent_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    project_id: str | None,
) -> None:
    if not _can_agent(database, user, permission, project_id):
        raise HTTPException(status_code=403, detail="Permission denied.")


def _can_agent(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    project_id: str | None,
) -> bool:
    return has_permission(user, permission, project_id=project_id, database=database)


def _deterministic_plan(session: RuntimeAgentSession) -> RuntimeActionPlan:
    registry = RuntimeToolRegistry.default()
    goal = session.user_goal.lower()
    tool_name = "summarize_artifacts"
    if "sync" in goal and "write" in goal:
        tool_name = "run_sync_write_enabled"
    elif "rank" in goal:
        tool_name = "run_ranking"
    elif "review" in goal:
        tool_name = "create_review_workspace"
    spec = registry.require(tool_name)
    plan_id = f"runtime-plan-{uuid.uuid4().hex[:12]}"
    step = RuntimeActionStep(
        step_id=f"runtime-step-{uuid.uuid4().hex[:12]}",
        plan_id=plan_id,
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args={"goal": session.user_goal},
        requires_approval=spec.requires_approval_by_default,
        approval_reason="Tool requires approval by default."
        if spec.requires_approval_by_default
        else None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={"hosted_template": True},
    )
    return RuntimeActionPlan(
        plan_id=plan_id,
        session_id=session.session_id,
        user_goal=session.user_goal,
        plan_summary="Hosted deterministic runtime-agent plan.",
        steps=[step],
        required_approvals=["external_write"] if spec.side_effect_level == "external_write" else [],
        expected_artifacts=[],
        risk_level="high" if spec.side_effect_level == "external_write" else "low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                tool_name: {
                    "required_permissions": spec.required_permissions,
                    "side_effect_level": spec.side_effect_level,
                    "policy_tags": spec.policy_tags,
                }
            }
        },
    )


def _execute_plan(
    plan: RuntimeActionPlan,
    *,
    mode: str,
    approvals: set[str],
) -> RuntimeExecutionResult:
    registry = RuntimeToolRegistry.default()
    executor = RuntimeActionExecutor(
        registry=registry,
        tool_handlers={
            spec.tool_name: _tool_handler
            for spec in registry.list_tools()
            if spec.category != "codex"
        },
    )
    execution_mode = "execute_with_approval" if mode in {"execute_with_approval"} else mode
    if execution_mode in {"observe_only", "suggest_only"}:
        execution_mode = "suggest_only"
    return executor.execute(
        plan,
        mode=execution_mode,  # type: ignore[arg-type]
        actor="user",
        approvals=approvals,
    )


def _tool_handler(step: RuntimeActionStep, spec: Any) -> dict[str, Any]:
    artifact_ids = (
        [f"runtime-artifact-{step.tool_name}-{uuid.uuid4().hex[:8]}"]
        if spec.side_effect_level == "artifact_write"
        else []
    )
    return {
        "status": "succeeded",
        "output": {"summary": f"{step.tool_name} executed via hosted runtime registry."},
        "artifact_ids": artifact_ids,
        "job_ids": [f"runtime-job-{step.tool_name}-{uuid.uuid4().hex[:8]}"],
        "metadata": {
            "artifact_provenance": {
                artifact_id: step.step_id for artifact_id in artifact_ids
            }
        },
    }


def _approval_requests_for_execution(
    execution: RuntimeExecutionResult,
) -> list[RuntimeApprovalRequest]:
    if execution.status != "approval_required":
        return []
    registry = RuntimeToolRegistry.default()
    controller = RuntimeApprovalController()
    approvals: list[RuntimeApprovalRequest] = []
    for result in execution.results:
        if result.status != "approval_required":
            continue
        step = next((item for item in execution.plan.steps if item.step_id == result.step_id), None)
        if step is None:
            continue
        spec = registry.require(step.tool_name)
        approvals.append(
            controller.create_approval_request(
                session_id=execution.plan.session_id,
                plan_id=execution.plan.plan_id,
                step_id=step.step_id,
                requested_by="codex",
                approval_type=approval_type_for_tool(spec) or "execute_plan",
                reason=result.error_summary or "Runtime policy requires approval.",
                risk_summary=f"{step.tool_name} side effect: {spec.side_effect_level}.",
            )
        )
    return approvals


def _session_status_from_execution(execution: RuntimeExecutionResult) -> str:
    if execution.status == "approval_required":
        return "awaiting_approval"
    if execution.status in {"failed", "policy_blocked"}:
        return "failed"
    if execution.status == "cancelled":
        return "cancelled"
    return "succeeded"


def _all_tool_permissions() -> set[str]:
    return {
        permission
        for spec in RuntimeToolRegistry.default().list_tools()
        for permission in spec.required_permissions
    }


def _audit(
    session: RuntimeAgentSession,
    event_type: str,
    actor: str | None,
    summary: str,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"runtime-audit-{uuid.uuid4().hex[:12]}",
        session_id=session.session_id,
        event_type=event_type,
        actor=actor,
        timestamp=datetime.now(UTC),
        summary=summary,
        object_type="RuntimeAgentSession",
        object_id=session.session_id,
        before=None,
        after=session.model_dump(mode="json"),
        metadata={"hosted": True},
    )


__all__ = ["router"]
