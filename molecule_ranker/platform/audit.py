from __future__ import annotations

from typing import Any

from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.schemas import PlatformAuditEvent


def write_platform_audit_event(
    database: PlatformDatabase,
    event: PlatformAuditEvent,
) -> PlatformAuditEvent:
    return database.write_audit(
        event.event_type,
        actor_user_id=event.actor_user_id,
        project_id=event.project_id,
        org_id=event.org_id,
        summary=event.summary,
        metadata=_event_metadata(event),
    )


def _event_metadata(event: PlatformAuditEvent) -> dict[str, Any]:
    return {
        **event.metadata,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "before": event.before,
        "after": event.after,
    }


__all__ = ["PlatformAuditEvent", "write_platform_audit_event"]
