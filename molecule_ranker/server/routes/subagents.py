from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.observability import redact_for_log
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, platform_database
from molecule_ranker.subagents.consensus import synthesize_critique_consensus
from molecule_ranker.subagents.coordinator import MultiAgentCoordinator
from molecule_ranker.subagents.critique import review_result
from molecule_ranker.subagents.hosted import SubagentHostedStore
from molecule_ranker.subagents.messaging import check_message_safety
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentCritique,
    SubagentMessage,
    SubagentResult,
)
from molecule_ranker.subagents.skills import expand_multi_agent_skill

router = APIRouter(prefix="/subagents", tags=["subagents"])
SECRET_TOKEN_RE = re.compile(r"\b(?:sk|pk|token|secret|api[_-]?key)[-_][A-Za-z0-9._-]+\b", re.I)


class SubagentSessionCreateRequest(BaseModel):
    goal: str
    project_id: str | None = None
    skill: str | None = None
    autonomy_level: Literal[
        "observe_only",
        "suggest_only",
        "execute_safe_tools",
        "execute_with_approval",
        "full_auto_restricted",
    ] = "suggest_only"
    dry_run: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentResultCritiqueRequest(BaseModel):
    critic: str = "guardrail_sentinel"
    expected_output_schema: dict[str, Any] | None = None


@router.post("/sessions")
def create_subagent_session(
    body: SubagentSessionCreateRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_subagent_permission(database, user, "subagent:run", body.project_id)
    session = _planned_session(body, user_id=user.user_id)
    store = _store(request)
    store.save_full_session(session)
    return {"session": session.model_dump(mode="json")}


@router.get("/sessions")
def list_subagent_sessions(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    sessions = [
        session
        for session in store.list_sessions()
        if _can_subagent(database, user, "subagent:read", _project_id(session))
    ]
    return {"sessions": [session.model_dump(mode="json") for session in sessions]}


@router.get("/sessions/{session_id}")
def get_subagent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_subagent_permission(database, user, "subagent:read", _project_id(session))
    return _session_payload(store, session)


@router.get("/sessions/{session_id}/messages")
def get_subagent_session_messages(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_subagent_permission(database, user, "subagent:read", _project_id(session))
    messages = [_sanitize_message(message) for message in store.list_messages(session_id)]
    return {"session_id": session_id, "messages": messages}


@router.get("/sessions/{session_id}/results")
def get_subagent_session_results(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_subagent_permission(database, user, "subagent:read", _project_id(session))
    return {
        "session_id": session_id,
        "results": [result.model_dump(mode="json") for result in store.list_results(session_id)],
    }


@router.get("/sessions/{session_id}/critiques")
def get_subagent_session_critiques(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_subagent_permission(database, user, "subagent:read", _project_id(session))
    return {
        "session_id": session_id,
        "critiques": [
            critique.model_dump(mode="json")
            for critique in store.list_critiques(session_id)
        ],
    }


@router.post("/sessions/{session_id}/cancel")
def cancel_subagent_session(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session = _session_or_404(store, session_id)
    _require_subagent_permission(database, user, "subagent:cancel", _project_id(session))
    cancelled = session.model_copy(
        update={"status": "cancelled", "completed_at": datetime.now(UTC)}
    )
    cancelled.metadata = {
        **cancelled.metadata,
        "audit_events": [
            *_audit_events(cancelled),
            {
                "event_id": f"subagent-audit-{uuid.uuid4().hex[:12]}",
                "event_type": "subagent_session_cancelled",
                "actor": user.user_id,
                "summary": "Subagent session cancelled.",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ],
    }
    store.save_full_session(cancelled)
    return {"session": cancelled.model_dump(mode="json")}


@router.post("/results/{result_id}/critique")
def critique_subagent_result(
    result_id: str,
    body: SubagentResultCritiqueRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    store = _store(request)
    session, result = _find_result_or_404(store, result_id)
    _require_subagent_permission(database, user, "subagent:critique", _project_id(session))
    task = next((task for task in session.tasks if task.task_id == result.task_id), None)
    critiques = review_result(
        result,
        critic_subagent_id=_critic_id(body.critic),
        expected_output_schema=(
            body.expected_output_schema
            if body.expected_output_schema is not None
            else task.expected_output_schema if task is not None else None
        ),
        known_artifact_ids=set(
            [
                *result.artifact_ids,
                *(task.input_artifact_ids if task is not None else []),
            ]
        ),
    )
    session.critiques.extend(critiques)
    session.consensus = [
        synthesize_critique_consensus(
            parent_session_id=session.multi_agent_session_id,
            task_ids=[task.task_id for task in session.tasks],
            results=session.results,
            critiques=session.critiques,
            high_risk=any(task.risk_level in {"high", "critical"} for task in session.tasks),
        )
    ]
    store.save_full_session(session)
    return {
        "session_id": session.multi_agent_session_id,
        "result_id": result_id,
        "critiques": [critique.model_dump(mode="json") for critique in critiques],
        "consensus": session.consensus[0].model_dump(mode="json"),
    }


def _planned_session(body: SubagentSessionCreateRequest, *, user_id: str) -> MultiAgentSession:
    if body.skill:
        session = expand_multi_agent_skill(body.skill, user_goal=body.goal)
    else:
        coordinator = MultiAgentCoordinator()
        session = coordinator.coordinate(
            user_goal=body.goal,
            runtime_session_id=None,
            visible_artifact_ids=["user-goal"],
            scoped_artifact_ids=["user-goal"],
        )
        session.status = "queued"
        session.completed_at = None
    session.metadata = {
        **session.metadata,
        **body.metadata,
        "project_id": body.project_id,
        "autonomy_level": body.autonomy_level,
        "dry_run": body.dry_run,
        "created_by_user_id": user_id,
        "hosted": True,
        "audit_events": [
            *_audit_events(session),
            {
                "event_id": f"subagent-audit-{uuid.uuid4().hex[:12]}",
                "event_type": "subagent_session_created",
                "actor": user_id,
                "summary": "Hosted subagent session created.",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ],
    }
    if body.metadata.get("inject_secret_message"):
        session.messages.append(
            SubagentMessage(
                message_id=f"subagent-message-{uuid.uuid4().hex[:12]}",
                parent_session_id=session.multi_agent_session_id,
                from_subagent_id=session.supervisor_subagent_id,
                to_subagent_id=None,
                message_type="status_update",
                content=str(body.metadata["inject_secret_message"]),
                referenced_artifact_ids=["user-goal"],
                referenced_entity_ids=[],
                referenced_tool_names=[],
                created_at=datetime.now(UTC),
                metadata={},
            )
        )
    if body.metadata.get("force_guardrail_finding"):
        session.critiques.append(_guardrail_finding(session))
        session.consensus = [
            synthesize_critique_consensus(
                parent_session_id=session.multi_agent_session_id,
                task_ids=[task.task_id for task in session.tasks],
                results=session.results,
                critiques=session.critiques,
                high_risk=True,
            )
        ]
    return session


def _session_payload(store: SubagentHostedStore, session: MultiAgentSession) -> dict[str, Any]:
    session_id = session.multi_agent_session_id
    return {
        "session": session.model_dump(mode="json"),
        "messages": [_sanitize_message(message) for message in store.list_messages(session_id)],
        "results": [
            result.model_dump(mode="json")
            for result in store.list_results(session_id)
        ],
        "critiques": [
            critique.model_dump(mode="json")
            for critique in store.list_critiques(session_id)
        ],
        "consensus": [
            consensus.model_dump(mode="json")
            for consensus in store.list_consensus(session_id)
        ],
    }


def _sanitize_message(message: SubagentMessage) -> dict[str, Any]:
    report = check_message_safety(
        message.content,
        referenced_artifact_ids=message.referenced_artifact_ids,
    )
    payload = message.model_dump(mode="json")
    payload["content"] = _redact_subagent_secret(str(redact_for_log(report.sanitized_content)))
    payload["metadata"] = redact_for_log(payload.get("metadata", {}))
    return payload


def _guardrail_finding(session: MultiAgentSession) -> SubagentCritique:
    target_result_id = session.results[0].result_id if session.results else "pending-result"
    return SubagentCritique(
        critique_id=f"subagent-critique-{uuid.uuid4().hex[:12]}",
        critic_subagent_id="guardrail-sentinel",
        target_result_id=target_result_id,
        critique_type="scientific_guardrail",
        passed=False,
        findings=["Guardrail review required for generated candidate claims."],
        required_fixes=["Human reviewer must resolve guardrail finding."],
        confidence=0.92,
        metadata={"required_for_high_risk": True, "non_overridable": True},
    )


def _find_result_or_404(
    store: SubagentHostedStore,
    result_id: str,
) -> tuple[MultiAgentSession, SubagentResult]:
    for session in store.list_sessions():
        for result in store.list_results(session.multi_agent_session_id):
            if result.result_id == result_id:
                if not any(item.result_id == result.result_id for item in session.results):
                    session.results.append(result)
                return session, result
    raise HTTPException(status_code=404, detail="Subagent result not found.")


def _session_or_404(store: SubagentHostedStore, session_id: str) -> MultiAgentSession:
    try:
        return store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Subagent session not found.") from exc


def _store(request: Request) -> SubagentHostedStore:
    return SubagentHostedStore(request.app.state.root_dir)


def _require_subagent_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    project_id: str | None,
) -> None:
    if not _can_subagent(database, user, permission, project_id):
        raise HTTPException(status_code=403, detail="Permission denied.")


def _can_subagent(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    project_id: str | None,
) -> bool:
    return has_permission(user, permission, project_id=project_id, database=database)


def _project_id(session: MultiAgentSession) -> str | None:
    project_id = session.metadata.get("project_id")
    return str(project_id) if project_id else None


def _audit_events(session: MultiAgentSession) -> list[dict[str, Any]]:
    events = session.metadata.get("audit_events", [])
    if isinstance(events, list):
        return [event for event in events if isinstance(event, dict)]
    return []


def _critic_id(value: str) -> str:
    return value.replace("_", "-")


def _redact_subagent_secret(value: str) -> str:
    return SECRET_TOKEN_RE.sub("[REDACTED]", value)
