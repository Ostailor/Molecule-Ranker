from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.schemas import (
    AgentCapabilityGrant,
    AgentCapabilityGrantStatus,
    AgentCapabilityScopeType,
    AgentGovernanceSchema,
    AgentType,
)

AuthorizationActorType = Literal["human", "admin", "service_account", "codex", "agent"]
GrantAuditAction = Literal["created", "revoked", "expired", "checked", "blocked"]

DEFAULT_GRANT_STORE_PATH = Path(".molecule-ranker/agent-governance/grants.json")
AUTHORIZED_GRANT_ACTORS = {"human", "admin", "service_account"}
CODEX_ACTOR_IDS = {"codex", "codex_cli", "codex-runtime-agent", "codex_worker"}


class CapabilityGrantError(ValueError):
    """Raised when a capability grant violates governance rules."""


class CapabilityGrantAuthorization(BaseModel):
    actor_id: str
    actor_type: AuthorizationActorType
    permission_scope: set[str] = Field(default_factory=set)
    policy_allowed_capabilities: set[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def can_authorize(self, capability: str) -> bool:
        return "*" in self.permission_scope or capability in self.permission_scope

    def policy_allows(self, capability: str) -> bool:
        return (
            self.policy_allowed_capabilities is None
            or capability in self.policy_allowed_capabilities
        )


class CapabilityGrantAuditEvent(AgentGovernanceSchema):
    audit_event_id: str
    grant_id: str | None
    action: GrantAuditAction
    actor_id: str
    occurred_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityGrantDecision(BaseModel):
    allowed: bool
    grant: AgentCapabilityGrant | None = None
    reason: str
    audit_event: CapabilityGrantAuditEvent
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityGrantStore:
    def __init__(self, path: Path | str = DEFAULT_GRANT_STORE_PATH) -> None:
        self.path = Path(path)

    def list_grants(self) -> list[AgentCapabilityGrant]:
        return [
            AgentCapabilityGrant.model_validate(item)
            for item in self._load().get("grants", [])
            if isinstance(item, dict)
        ]

    def list_audit_events(self) -> list[CapabilityGrantAuditEvent]:
        return [
            CapabilityGrantAuditEvent.model_validate(item)
            for item in self._load().get("audit_events", [])
            if isinstance(item, dict)
        ]

    def save_grants(
        self,
        grants: list[AgentCapabilityGrant],
        audit_events: list[CapabilityGrantAuditEvent],
    ) -> None:
        self._save(
            {
                "grants": [grant.model_dump(mode="json") for grant in grants],
                "audit_events": [event.model_dump(mode="json") for event in audit_events],
            }
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"grants": [], "audit_events": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"grants": [], "audit_events": []}
        raw.setdefault("grants", [])
        raw.setdefault("audit_events", [])
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class CapabilityGrantManager:
    def __init__(
        self,
        *,
        grants: list[AgentCapabilityGrant] | None = None,
        audit_events: list[CapabilityGrantAuditEvent] | None = None,
        store: CapabilityGrantStore | None = None,
    ) -> None:
        self.store = store
        if store is not None:
            self.grants = store.list_grants()
            self.audit_events = store.list_audit_events()
        else:
            self.grants = list(grants or [])
            self.audit_events = list(audit_events or [])

    def create_grant(
        self,
        *,
        agent_id: str,
        agent_type: AgentType,
        granted_capability: str,
        scope_type: AgentCapabilityScopeType,
        scope_id: str | None,
        authorization: CapabilityGrantAuthorization,
        expires_at: datetime | None = None,
        status: AgentCapabilityGrantStatus = "active",
        grant_id: str | None = None,
        granted_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CapabilityGrantDecision:
        now = granted_at or datetime.now(UTC)
        blocked_reason = _grant_block_reason(
            agent_id=agent_id,
            capability=granted_capability,
            authorization=authorization,
            status=status,
        )
        if blocked_reason is not None:
            event = self._audit(
                grant_id=None,
                action="blocked",
                actor_id=authorization.actor_id,
                summary=blocked_reason,
                metadata={
                    "agent_id": agent_id,
                    "capability": granted_capability,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
            )
            return CapabilityGrantDecision(
                allowed=False,
                grant=None,
                reason=blocked_reason,
                audit_event=event,
            )

        grant = AgentCapabilityGrant(
            grant_id=grant_id or f"agent-capability-grant-{uuid4().hex[:12]}",
            agent_id=agent_id,
            agent_type=agent_type,
            granted_capability=granted_capability,
            scope_type=scope_type,
            scope_id=scope_id,
            granted_by=authorization.actor_id,
            granted_at=now,
            expires_at=expires_at,
            revoked_at=None,
            status=status,
            metadata={
                **(metadata or {}),
                "authorized_actor_type": authorization.actor_type,
                "policy_narrowing_enforced": True,
            },
        )
        self.grants = [item for item in self.grants if item.grant_id != grant.grant_id]
        self.grants.append(grant)
        event = self._audit(
            grant_id=grant.grant_id,
            action="created",
            actor_id=authorization.actor_id,
            summary=f"Created capability grant {grant.grant_id}.",
            metadata=grant.model_dump(mode="json"),
        )
        self._persist()
        return CapabilityGrantDecision(
            allowed=True,
            grant=grant,
            reason="Capability grant created.",
            audit_event=event,
        )

    def revoke_grant(
        self,
        grant_id: str,
        *,
        revoked_by: str,
        revoked_at: datetime | None = None,
        reason: str = "Capability grant revoked.",
    ) -> AgentCapabilityGrant:
        grant = self._require_grant(grant_id)
        updated = grant.model_copy(
            update={
                "status": "revoked",
                "revoked_at": revoked_at or datetime.now(UTC),
                "metadata": {**grant.metadata, "revocation_reason": reason},
            }
        )
        self.grants = [updated if item.grant_id == grant_id else item for item in self.grants]
        self._audit(
            grant_id=grant_id,
            action="revoked",
            actor_id=revoked_by,
            summary=reason,
            metadata=updated.model_dump(mode="json"),
        )
        self._persist()
        return updated

    def check_capability(
        self,
        *,
        agent_id: str,
        capability: str,
        scope_type: AgentCapabilityScopeType | None = None,
        scope_id: str | None = None,
        now: datetime | None = None,
    ) -> CapabilityGrantDecision:
        current_time = now or datetime.now(UTC)
        self.cleanup_expired(now=current_time)
        grant = _matching_active_grant(
            self.grants,
            agent_id=agent_id,
            capability=capability,
            scope_type=scope_type,
            scope_id=scope_id,
            now=current_time,
        )
        allowed = grant is not None
        event = self._audit(
            grant_id=grant.grant_id if grant else None,
            action="checked",
            actor_id=agent_id,
            summary=(
                f"Capability {capability} is granted."
                if allowed
                else f"Capability {capability} is not granted."
            ),
            metadata={
                "agent_id": agent_id,
                "capability": capability,
                "scope_type": scope_type,
                "scope_id": scope_id,
            },
        )
        self._persist()
        return CapabilityGrantDecision(
            allowed=allowed,
            grant=grant,
            reason=event.summary,
            audit_event=event,
        )

    def list_grants(
        self,
        *,
        agent_id: str | None = None,
        include_inactive: bool = True,
        now: datetime | None = None,
    ) -> list[AgentCapabilityGrant]:
        current_time = now or datetime.now(UTC)
        grants = self.grants
        if agent_id is not None:
            grants = [grant for grant in grants if grant.agent_id == agent_id]
        if not include_inactive:
            grants = [
                grant
                for grant in grants
                if _grant_active(grant, now=current_time)
            ]
        return sorted(grants, key=lambda grant: (grant.agent_id, grant.granted_capability))

    def cleanup_expired(self, *, now: datetime | None = None) -> list[AgentCapabilityGrant]:
        current_time = now or datetime.now(UTC)
        expired: list[AgentCapabilityGrant] = []
        updated_grants: list[AgentCapabilityGrant] = []
        for grant in self.grants:
            is_expired = (
                grant.status == "active"
                and grant.expires_at is not None
                and grant.expires_at < current_time
            )
            if is_expired:
                updated = grant.model_copy(update={"status": "expired"})
                expired.append(updated)
                updated_grants.append(updated)
                self._audit(
                    grant_id=grant.grant_id,
                    action="expired",
                    actor_id="system",
                    summary=f"Expired capability grant {grant.grant_id}.",
                    metadata=updated.model_dump(mode="json"),
                )
            else:
                updated_grants.append(grant)
        if expired:
            self.grants = updated_grants
            self._persist()
        return expired

    def _require_grant(self, grant_id: str) -> AgentCapabilityGrant:
        for grant in self.grants:
            if grant.grant_id == grant_id:
                return grant
        raise CapabilityGrantError(f"Unknown capability grant: {grant_id}")

    def _audit(
        self,
        *,
        grant_id: str | None,
        action: GrantAuditAction,
        actor_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> CapabilityGrantAuditEvent:
        event = CapabilityGrantAuditEvent(
            audit_event_id=f"capability-grant-audit-{uuid4().hex[:12]}",
            grant_id=grant_id,
            action=action,
            actor_id=actor_id,
            occurred_at=datetime.now(UTC),
            summary=summary,
            metadata=metadata or {},
        )
        self.audit_events.append(event)
        return event

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save_grants(self.grants, self.audit_events)


def _grant_block_reason(
    *,
    agent_id: str,
    capability: str,
    authorization: CapabilityGrantAuthorization,
    status: str,
) -> str | None:
    if authorization.actor_id == agent_id:
        return "Agents cannot grant themselves new capabilities."
    if _is_codex_actor(authorization) and status == "active":
        return "Codex cannot create active capability grants."
    if authorization.actor_type not in AUTHORIZED_GRANT_ACTORS:
        return "Capability grants require human, admin, or service-account authorization."
    if not authorization.can_authorize(capability):
        return "Capability grant exceeds authorizing actor permission scope."
    if not authorization.policy_allows(capability):
        return "Capability grant would bypass RBAC or policy boundaries."
    return None


def _is_codex_actor(authorization: CapabilityGrantAuthorization) -> bool:
    return (
        authorization.actor_type == "codex"
        or authorization.actor_id.strip().lower() in CODEX_ACTOR_IDS
    )


def _matching_active_grant(
    grants: list[AgentCapabilityGrant],
    *,
    agent_id: str,
    capability: str,
    scope_type: AgentCapabilityScopeType | None,
    scope_id: str | None,
    now: datetime,
) -> AgentCapabilityGrant | None:
    for grant in grants:
        if grant.agent_id != agent_id or grant.granted_capability != capability:
            continue
        if scope_type is not None and grant.scope_type != scope_type:
            continue
        if scope_id is not None and grant.scope_id != scope_id:
            continue
        if _grant_active(grant, now=now):
            return grant
    return None


def _grant_active(grant: AgentCapabilityGrant, *, now: datetime) -> bool:
    return (
        grant.status == "active"
        and grant.revoked_at is None
        and (grant.expires_at is None or grant.expires_at >= now)
    )


__all__ = [
    "CapabilityGrantAuditEvent",
    "CapabilityGrantAuthorization",
    "CapabilityGrantDecision",
    "CapabilityGrantError",
    "CapabilityGrantManager",
    "CapabilityGrantStore",
    "AgentCapabilityGrant",
]
