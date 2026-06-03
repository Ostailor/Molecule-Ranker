from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.runtime_agents.memory import RuntimeMemoryStore
from molecule_ranker.runtime_agents.state import RuntimeMemoryPolicyError


def test_save_and_retrieve_session_memory(tmp_path: Path) -> None:
    store = RuntimeMemoryStore(tmp_path / "memory.json")
    record = store.save_session_summary(
        session_id="session-1",
        project_id="project-1",
        user_id="user-1",
        summary="Ranking completed and follow-up graph build was useful.",
        content={"tools": ["run_ranking", "build_graph"], "outcome": "succeeded"},
        actor="codex",
        created_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
    )

    retrieved = store.retrieve_relevant_memory(
        project_id="project-1",
        user_id="user-1",
        query="ranking graph follow-up",
    )

    assert retrieved[0].memory_id == record.memory_id
    assert retrieved[0].memory_type == "session"
    assert retrieved[0].provenance["operational_context_only"] is True
    assert retrieved[0].provenance["source_session_id"] == "session-1"
    assert store.memory_audit_events()[-1].event_type == "runtime_memory_retrieved"


def test_secrets_are_redacted_before_storage(tmp_path: Path) -> None:
    store = RuntimeMemoryStore(tmp_path / "memory.json")

    record = store.save_session_summary(
        session_id="session-1",
        project_id="project-1",
        user_id="user-1",
        summary="Used API_KEY=abc123 and password: hunter2 in a failed setup.",
        content={"token": "raw-token", "note": "Authorization: Bearer secret-value"},
        actor="codex",
        created_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
    )

    exported = store.export_memory()

    assert "abc123" not in str(exported)
    assert "hunter2" not in str(exported)
    assert "raw-token" not in str(exported)
    assert "secret-value" not in str(exported)
    assert record.content["token"] == "[REDACTED]"


def test_memory_cannot_be_evidence_or_assay_result(tmp_path: Path) -> None:
    store = RuntimeMemoryStore(tmp_path / "memory.json")

    with pytest.raises(RuntimeMemoryPolicyError, match="Memory cannot create EvidenceItem"):
        store.save_memory(
            memory_type="session",
            session_id="session-1",
            project_id="project-1",
            user_id="user-1",
            summary="EvidenceItem should not be stored as memory.",
            content={"EvidenceItem": {"source": "invented"}},
            provenance={"source": "test"},
            actor="codex",
            created_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        )

    assert store.export_memory()["records"] == []


def test_delete_memory_works(tmp_path: Path) -> None:
    store = RuntimeMemoryStore(tmp_path / "memory.json")
    record = store.save_session_summary(
        session_id="session-1",
        project_id="project-1",
        user_id="user-1",
        summary="Failure pattern: retry literature job after rate limit.",
        content={"failure": "rate_limit", "next_action": "retry"},
        actor="codex",
        created_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
    )

    deleted = store.delete_memory(record.memory_id, actor="user-1")

    assert deleted is True
    assert store.retrieve_relevant_memory(project_id="project-1", query="rate limit") == []
    assert "runtime_memory_deleted" in {
        event.event_type for event in store.memory_audit_events()
    }
