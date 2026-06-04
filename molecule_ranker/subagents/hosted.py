from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentMessage,
    SubagentResult,
)


class SubagentHostedStore:
    """JSON-backed persistence for hosted multi-agent subagent sessions."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.base_dir = root_dir / ".molecule-ranker" / "subagents"
        self.sessions_dir = self.base_dir / "sessions"

    def save_session(self, session: MultiAgentSession) -> None:
        self._write(
            session.multi_agent_session_id,
            "multi_agent_session.json",
            session.model_dump(mode="json"),
        )

    def get_session(self, session_id: str) -> MultiAgentSession:
        return MultiAgentSession.model_validate(
            self._read(session_id, "multi_agent_session.json")
        )

    def list_sessions(self) -> list[MultiAgentSession]:
        if not self.sessions_dir.exists():
            return []
        return [
            MultiAgentSession.model_validate(_read_json(path))
            for path in sorted(self.sessions_dir.glob("*/multi_agent_session.json"))
        ]

    def save_messages(self, session_id: str, messages: list[SubagentMessage]) -> None:
        self._write(
            session_id,
            "subagent_messages.json",
            [message.model_dump(mode="json") for message in messages],
        )

    def list_messages(self, session_id: str) -> list[SubagentMessage]:
        path = self._path(session_id, "subagent_messages.json")
        if not path.exists():
            return self.get_session(session_id).messages
        return [SubagentMessage.model_validate(item) for item in _read_json(path)]

    def save_results(self, session_id: str, results: list[SubagentResult]) -> None:
        self._write(
            session_id,
            "subagent_results.json",
            [result.model_dump(mode="json") for result in results],
        )

    def list_results(self, session_id: str) -> list[SubagentResult]:
        path = self._path(session_id, "subagent_results.json")
        if not path.exists():
            return self.get_session(session_id).results
        return [SubagentResult.model_validate(item) for item in _read_json(path)]

    def save_critiques(self, session_id: str, critiques: list[SubagentCritique]) -> None:
        self._write(
            session_id,
            "subagent_critiques.json",
            [critique.model_dump(mode="json") for critique in critiques],
        )

    def list_critiques(self, session_id: str) -> list[SubagentCritique]:
        path = self._path(session_id, "subagent_critiques.json")
        if not path.exists():
            return self.get_session(session_id).critiques
        return [SubagentCritique.model_validate(item) for item in _read_json(path)]

    def save_consensus(self, session_id: str, consensus: list[SubagentConsensus]) -> None:
        self._write(
            session_id,
            "subagent_consensus.json",
            [item.model_dump(mode="json") for item in consensus],
        )

    def list_consensus(self, session_id: str) -> list[SubagentConsensus]:
        path = self._path(session_id, "subagent_consensus.json")
        if not path.exists():
            return self.get_session(session_id).consensus
        return [SubagentConsensus.model_validate(item) for item in _read_json(path)]

    def save_full_session(self, session: MultiAgentSession) -> None:
        session_id = session.multi_agent_session_id
        self.save_session(session)
        self.save_messages(session_id, session.messages)
        self.save_results(session_id, session.results)
        self.save_critiques(session_id, session.critiques)
        self.save_consensus(session_id, session.consensus)

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


__all__ = ["SubagentHostedStore"]
