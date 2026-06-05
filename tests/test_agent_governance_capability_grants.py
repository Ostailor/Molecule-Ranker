from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.agent_governance.capability_grants import (
    CapabilityGrantAuthorization,
    CapabilityGrantManager,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_grant_active_capability() -> None:
    manager = CapabilityGrantManager()

    created = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        authorization=_auth("admin-1", {"run_ranking"}),
        granted_at=NOW,
    )
    checked = manager.check_capability(
        agent_id="agent-1",
        capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        now=NOW,
    )

    assert created.allowed is True
    assert created.grant is not None
    assert created.grant.status == "active"
    assert checked.allowed is True
    assert checked.grant == created.grant
    assert [event.action for event in manager.audit_events] == ["created", "checked"]


def test_expired_grant_ignored_and_cleaned_up() -> None:
    manager = CapabilityGrantManager()
    created = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        authorization=_auth("admin-1", {"run_ranking"}),
        granted_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(days=1),
    )

    checked = manager.check_capability(
        agent_id="agent-1",
        capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        now=NOW,
    )

    assert created.grant is not None
    assert checked.allowed is False
    assert manager.grants[0].status == "expired"
    assert "expired" in [event.action for event in manager.audit_events]


def test_revoked_grant_ignored() -> None:
    manager = CapabilityGrantManager()
    created = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        authorization=_auth("admin-1", {"run_ranking"}),
        granted_at=NOW,
    )
    assert created.grant is not None

    manager.revoke_grant(created.grant.grant_id, revoked_by="admin-1", revoked_at=NOW)
    checked = manager.check_capability(
        agent_id="agent-1",
        capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        now=NOW,
    )

    assert checked.allowed is False
    assert manager.grants[0].status == "revoked"
    assert manager.grants[0].revoked_at == NOW


def test_codex_self_grant_blocked() -> None:
    manager = CapabilityGrantManager()

    self_grant = manager.create_grant(
        agent_id="codex",
        agent_type="codex_worker",
        granted_capability="run_codex_summary",
        scope_type="workflow",
        scope_id="workflow-1",
        authorization=CapabilityGrantAuthorization(
            actor_id="codex",
            actor_type="codex",
            permission_scope={"run_codex_summary"},
        ),
        granted_at=NOW,
    )
    codex_grant = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        authorization=CapabilityGrantAuthorization(
            actor_id="codex",
            actor_type="codex",
            permission_scope={"run_ranking"},
        ),
        granted_at=NOW,
    )

    assert self_grant.allowed is False
    assert "cannot grant themselves" in self_grant.reason
    assert codex_grant.allowed is False
    assert "Codex cannot create active" in codex_grant.reason


def test_grant_beyond_user_permission_blocked() -> None:
    manager = CapabilityGrantManager()

    decision = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_generation",
        scope_type="project",
        scope_id="project-1",
        authorization=_auth("user-1", {"run_ranking"}),
        granted_at=NOW,
    )
    policy_bypass = manager.create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_generation",
        scope_type="project",
        scope_id="project-1",
        authorization=_auth(
            "admin-1",
            {"run_generation"},
            policy_allowed_capabilities={"run_ranking"},
        ),
        granted_at=NOW,
    )

    assert decision.allowed is False
    assert "permission scope" in decision.reason
    assert policy_bypass.allowed is False
    assert "bypass RBAC or policy" in policy_bypass.reason


def _auth(
    actor_id: str,
    permission_scope: set[str],
    *,
    actor_type: str = "admin",
    policy_allowed_capabilities: set[str] | None = None,
) -> CapabilityGrantAuthorization:
    return CapabilityGrantAuthorization.model_validate(
        {
            "actor_id": actor_id,
            "actor_type": actor_type,
            "permission_scope": permission_scope,
            "policy_allowed_capabilities": policy_allowed_capabilities,
        }
    )
