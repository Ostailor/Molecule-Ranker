from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.review.schemas import ReviewAuditEvent, Reviewer


def audit_event(
    *,
    event_type: str,
    actor: Reviewer | str,
    object_type: str,
    object_id: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReviewAuditEvent:
    actor_id = actor.reviewer_id if isinstance(actor, Reviewer) else actor
    return ReviewAuditEvent(
        event_type=event_type,
        actor=actor_id,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        before=before,
        after=after,
        metadata=metadata or {},
    )


def create_audit_event(
    *,
    event_type: str,
    actor: Reviewer | str,
    object_type: str,
    object_id: str,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReviewAuditEvent:
    actor_id = actor.reviewer_id if isinstance(actor, Reviewer) else actor
    return ReviewAuditEvent(
        event_type=event_type,
        actor=actor_id,
        timestamp=datetime.now(UTC),
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        before=before,
        after=after,
        metadata=metadata or {},
    )
