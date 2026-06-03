from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from molecule_ranker.runtime_agents.context import redact_sensitive_context
from molecule_ranker.runtime_agents.schemas import RuntimeAgentAuditEvent
from molecule_ranker.runtime_agents.state import (
    RuntimeMemoryPolicyError,
    RuntimeMemoryRecord,
    RuntimeMemoryType,
    load_memory_state,
    save_memory_state,
)

DEFAULT_MEMORY_PATH = Path(".omx/state/runtime_agents/memory.json")
FORBIDDEN_MEMORY_KEYS = {
    "assay_result",
    "assay_results",
    "assayresult",
    "evidence_item",
    "evidence_items",
    "evidenceitem",
}
SECRET_FIELD_NAMES = {
    "access_key",
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


class RuntimeMemoryStore:
    """JSON-backed operational memory for runtime-agent planning."""

    def __init__(self, path: str | Path = DEFAULT_MEMORY_PATH) -> None:
        self.path = Path(path)
        self._audit_events: list[RuntimeAgentAuditEvent] = []

    def save_session_summary(
        self,
        *,
        session_id: str,
        project_id: str | None,
        user_id: str | None,
        summary: str,
        content: dict[str, Any],
        actor: str,
        created_at: datetime | None = None,
        org_id: str | None = None,
        provenance: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> RuntimeMemoryRecord:
        return self.save_memory(
            memory_type="session",
            session_id=session_id,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            summary=summary,
            content=content,
            provenance={"source_session_id": session_id, **(provenance or {})},
            actor=actor,
            created_at=created_at,
            tags=tags,
        )

    def save_memory(
        self,
        *,
        memory_type: RuntimeMemoryType,
        session_id: str | None,
        project_id: str | None,
        user_id: str | None,
        summary: str,
        content: dict[str, Any],
        provenance: dict[str, Any],
        actor: str,
        created_at: datetime | None = None,
        org_id: str | None = None,
        expires_at: datetime | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeMemoryRecord:
        _enforce_memory_policy(content=content, metadata=metadata or {})
        now = created_at or datetime.now(UTC)
        redacted_content = _redact_json_like(content)
        redacted_summary = redact_sensitive_context(summary)
        record = RuntimeMemoryRecord(
            memory_id=f"runtime-memory-{uuid4().hex[:12]}",
            memory_type=memory_type,
            session_id=session_id,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            summary=redacted_summary,
            content=redacted_content,
            provenance={
                **_redact_json_like(provenance),
                "actor": actor,
                "created_at": now.isoformat(),
                "operational_context_only": True,
                "not_biomedical_evidence": True,
                "cannot_override_source_backed_evidence": True,
            },
            created_at=now,
            expires_at=expires_at,
            tags=tags or [],
            metadata={
                **_redact_json_like(metadata or {}),
                "memory_policy": "operational_context_only",
            },
        )
        state = load_memory_state(self.path)
        state.records.append(record)
        save_memory_state(self.path, state)
        self._audit_events.append(
            _audit_event(
                event_type="runtime_memory_saved",
                session_id=session_id,
                actor=actor,
                summary=f"Saved runtime {memory_type} memory.",
                object_id=record.memory_id,
                timestamp=now,
                after=record.model_dump(mode="json"),
            )
        )
        return record

    def retrieve_relevant_memory(
        self,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        query: str = "",
        memory_types: list[RuntimeMemoryType] | None = None,
        limit: int = 10,
    ) -> list[RuntimeMemoryRecord]:
        state = load_memory_state(self.path)
        query_terms = _terms(query)
        allowed_types = set(memory_types or [])
        now = datetime.now(UTC)
        candidates: list[tuple[int, RuntimeMemoryRecord]] = []
        for record in state.records:
            if record.expires_at is not None and record.expires_at <= now:
                continue
            if allowed_types and record.memory_type not in allowed_types:
                continue
            if project_id is not None and record.project_id not in {None, project_id}:
                continue
            if user_id is not None and record.user_id not in {None, user_id}:
                continue
            if session_id is not None and record.session_id not in {None, session_id}:
                continue
            score = _relevance_score(record, query_terms)
            if query_terms and score == 0:
                continue
            candidates.append((score, record))
        candidates.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        results = [record for _, record in candidates[:limit]]
        self._audit_events.append(
            _audit_event(
                event_type="runtime_memory_retrieved",
                session_id=session_id,
                actor=None,
                summary=f"Retrieved {len(results)} runtime memory records.",
                object_id=None,
                timestamp=now,
                after={"memory_ids": [record.memory_id for record in results]},
            )
        )
        return results

    def delete_memory(self, memory_id: str, *, actor: str) -> bool:
        state = load_memory_state(self.path)
        before_records = list(state.records)
        state.records = [record for record in state.records if record.memory_id != memory_id]
        deleted = len(before_records) != len(state.records)
        if deleted:
            save_memory_state(self.path, state)
            self._audit_events.append(
                _audit_event(
                    event_type="runtime_memory_deleted",
                    session_id=None,
                    actor=actor,
                    summary=f"Deleted runtime memory {memory_id}.",
                    object_id=memory_id,
                    timestamp=datetime.now(UTC),
                    before={
                        "memory_id": memory_id,
                        "record_count": len(before_records),
                    },
                    after={"record_count": len(state.records)},
                )
            )
        return deleted

    def export_memory(
        self,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        state = load_memory_state(self.path)
        records = [
            record
            for record in state.records
            if (project_id is None or record.project_id == project_id)
            and (user_id is None or record.user_id == user_id)
        ]
        return {
            "records": [record.model_dump(mode="json") for record in records],
            "policy": {
                "operational_context_only": True,
                "not_biomedical_evidence": True,
                "cannot_create_evidence_item": True,
                "cannot_create_assay_result": True,
                "cannot_override_source_backed_evidence": True,
            },
        }

    def memory_audit_events(self) -> list[RuntimeAgentAuditEvent]:
        return list(self._audit_events)


def save_session_summary(store: RuntimeMemoryStore, **kwargs: Any) -> RuntimeMemoryRecord:
    return store.save_session_summary(**kwargs)


def retrieve_relevant_memory(store: RuntimeMemoryStore, **kwargs: Any) -> list[RuntimeMemoryRecord]:
    return store.retrieve_relevant_memory(**kwargs)


def delete_memory(store: RuntimeMemoryStore, memory_id: str, *, actor: str) -> bool:
    return store.delete_memory(memory_id, actor=actor)


def export_memory(store: RuntimeMemoryStore, **kwargs: Any) -> dict[str, Any]:
    return store.export_memory(**kwargs)


def memory_audit_events(store: RuntimeMemoryStore) -> list[RuntimeAgentAuditEvent]:
    return store.memory_audit_events()


def _enforce_memory_policy(*, content: dict[str, Any], metadata: dict[str, Any]) -> None:
    flattened_keys = _flatten_keys(content)
    if "evidenceitem" in flattened_keys or "evidence_item" in flattened_keys:
        raise RuntimeMemoryPolicyError("Memory cannot create EvidenceItem records.")
    if "assayresult" in flattened_keys or "assay_result" in flattened_keys:
        raise RuntimeMemoryPolicyError("Memory cannot create assay result records.")
    if any(key in FORBIDDEN_MEMORY_KEYS for key in flattened_keys):
        raise RuntimeMemoryPolicyError(
            "Memory is operational context and cannot create evidence or assay records."
        )
    if metadata.get("contains_codex_transcript") and not metadata.get(
        "transcript_retention_policy"
    ):
        raise RuntimeMemoryPolicyError(
            "Codex transcripts require an explicit retention policy before storage."
        )


def _flatten_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            keys.add(normalized)
            keys.update(_flatten_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_flatten_keys(item))
    return keys


def _redact_json_like(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_context(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _sensitive_key(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json_like(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json_like(item) for item in value[:100]]
    return value


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SECRET_FIELD_NAMES or any(
        part in normalized for part in SECRET_FIELD_NAMES
    )


def _terms(text: str) -> set[str]:
    return {
        term
        for term in "".join(
            char.lower() if char.isalnum() else " " for char in text
        ).split()
    }


def _relevance_score(record: RuntimeMemoryRecord, query_terms: set[str]) -> int:
    if not query_terms:
        return 1
    haystack = _terms(
        " ".join(
            [
                record.summary,
                " ".join(record.tags),
                str(record.content),
                str(record.provenance),
            ]
        )
    )
    return len(query_terms.intersection(haystack))


def _audit_event(
    *,
    event_type: str,
    session_id: str | None,
    actor: str | None,
    summary: str,
    object_id: str | None,
    timestamp: datetime,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"runtime-audit-{uuid4().hex[:12]}",
        session_id=session_id or "runtime-memory",
        event_type=event_type,
        actor=actor,
        timestamp=timestamp,
        summary=summary,
        object_type="RuntimeMemoryRecord" if object_id else "RuntimeMemoryStore",
        object_id=object_id,
        before=before,
        after=after,
        metadata={
            "operational_context_only": True,
            "not_biomedical_evidence": True,
        },
    )


__all__ = [
    "RuntimeMemoryStore",
    "delete_memory",
    "export_memory",
    "memory_audit_events",
    "retrieve_relevant_memory",
    "save_session_summary",
]
