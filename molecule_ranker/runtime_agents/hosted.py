from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeAgentAuditEvent,
    RuntimeAgentSession,
    RuntimeApprovalRequest,
    RuntimeToolResult,
)


class RuntimeAgentHostedStore:
    """Small JSON-backed persistence adapter for hosted runtime-agent artifacts."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.base_dir = root_dir / ".molecule-ranker" / "runtime-agent"
        self.sessions_dir = self.base_dir / "sessions"

    def save_session(self, session: RuntimeAgentSession) -> None:
        self._write(session.session_id, "runtime_session.json", session.model_dump(mode="json"))

    def get_session(self, session_id: str) -> RuntimeAgentSession:
        return RuntimeAgentSession.model_validate(self._read(session_id, "runtime_session.json"))

    def list_sessions(self) -> list[RuntimeAgentSession]:
        if not self.sessions_dir.exists():
            return []
        sessions: list[RuntimeAgentSession] = []
        for path in sorted(self.sessions_dir.glob("*/runtime_session.json")):
            sessions.append(RuntimeAgentSession.model_validate(_read_json(path)))
        return sessions

    def save_plan(self, plan: RuntimeActionPlan) -> None:
        self._write(plan.session_id, "runtime_action_plan.json", plan.model_dump(mode="json"))

    def get_plan(self, session_id: str) -> RuntimeActionPlan:
        return RuntimeActionPlan.model_validate(self._read(session_id, "runtime_action_plan.json"))

    def save_tool_results(self, session_id: str, results: list[RuntimeToolResult]) -> None:
        self._write(
            session_id,
            "runtime_tool_results.json",
            [result.model_dump(mode="json") for result in results],
        )

    def list_tool_results(self, session_id: str) -> list[RuntimeToolResult]:
        path = self._path(session_id, "runtime_tool_results.json")
        if not path.exists():
            return []
        return [RuntimeToolResult.model_validate(item) for item in _read_json(path)]

    def save_audit_events(self, session_id: str, events: list[RuntimeAgentAuditEvent]) -> None:
        existing = self.list_audit_events(session_id)
        combined = [*existing, *events]
        self._write(
            session_id,
            "runtime_audit_log.json",
            [event.model_dump(mode="json") for event in combined],
        )

    def list_audit_events(self, session_id: str | None = None) -> list[RuntimeAgentAuditEvent]:
        paths = (
            [self._path(session_id, "runtime_audit_log.json")]
            if session_id is not None
            else sorted(self.sessions_dir.glob("*/runtime_audit_log.json"))
        )
        events: list[RuntimeAgentAuditEvent] = []
        for path in paths:
            if path.exists():
                events.extend(
                    RuntimeAgentAuditEvent.model_validate(item) for item in _read_json(path)
                )
        return events

    def save_approval(self, approval: RuntimeApprovalRequest) -> None:
        path = self._path(approval.session_id, f"approval_{approval.approval_id}.json")
        _write_json(path, approval.model_dump(mode="json"))

    def get_approval(self, approval_id: str) -> RuntimeApprovalRequest:
        for path in self.sessions_dir.glob(f"*/approval_{approval_id}.json"):
            return RuntimeApprovalRequest.model_validate(_read_json(path))
        raise KeyError(approval_id)

    def save_approval_decision(self, approval: RuntimeApprovalRequest) -> None:
        self.save_approval(approval)

    def list_approvals(self, session_id: str | None = None) -> list[RuntimeApprovalRequest]:
        pattern = f"{session_id}/approval_*.json" if session_id else "*/approval_*.json"
        return [
            RuntimeApprovalRequest.model_validate(_read_json(path))
            for path in sorted(self.sessions_dir.glob(pattern))
        ]

    def save_guardrail_report(self, session_id: str, report: dict[str, Any]) -> None:
        self._write(session_id, "runtime_guardrail_report.json", report)

    def get_guardrail_report(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id, "runtime_guardrail_report.json")
        return _read_json(path) if path.exists() else {"allowed": True, "violations": []}

    def _path(self, session_id: str, filename: str) -> Path:
        return self.sessions_dir / session_id / filename

    def _read(self, session_id: str, filename: str) -> Any:
        path = self._path(session_id, filename)
        if not path.exists():
            raise KeyError(f"{session_id}/{filename}")
        return _read_json(path)

    def _write(self, session_id: str, filename: str, payload: Any) -> None:
        _write_json(self._path(session_id, filename), payload)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["RuntimeAgentHostedStore"]
