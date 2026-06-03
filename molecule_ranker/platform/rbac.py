from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Literal, Protocol

from fastapi import HTTPException
from sqlalchemy import select

from molecule_ranker.platform.database import memberships, project_permissions, project_workspaces
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.schemas import UserAccount

Permission = Literal[
    "project:create",
    "project:read",
    "project:update",
    "project:delete",
    "run:create",
    "run:read",
    "run:cancel",
    "artifact:read",
    "artifact:export",
    "review:read",
    "review:write",
    "experiment:import",
    "experiment:read",
    "integration:read",
    "integration:write",
    "integration:manage",
    "integration:sync",
    "integration:approve_mapping",
    "integration:manage_credentials",
    "integration:view_audit",
    "codex:run",
    "codex:read",
    "design:read",
    "design:run",
    "design:approve_plan",
    "design:export",
    "model:read",
    "model:train",
    "model:predict",
    "model:register",
    "model:deactivate",
    "model:export",
    "structure:read",
    "structure:run",
    "structure:dock",
    "structure:export",
    "structure:approve",
    "portfolio:read",
    "portfolio:run",
    "portfolio:approve_stage_gate",
    "portfolio:export",
    "portfolio:configure",
    "graph:read",
    "graph:build",
    "graph:query",
    "graph:export",
    "graph:admin",
    "hypothesis:read",
    "hypothesis:generate",
    "hypothesis:review",
    "hypothesis:export",
    "hypothesis:admin",
    "campaign:read",
    "campaign:create",
    "campaign:plan",
    "campaign:approve",
    "campaign:update",
    "campaign:export",
    "campaign:admin",
    "evaluation:read",
    "evaluation:run",
    "evaluation:export",
    "evaluation:admin",
    "agent:read",
    "agent:plan",
    "agent:execute",
    "agent:approve",
    "agent:admin",
    "admin:manage_users",
    "admin:manage_org",
    "admin:view_audit",
]
ProjectAction = Literal["read", "write", "admin", "run_codex"]

ORG_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"project:create", "admin:manage_users", "admin:manage_org", "admin:view_audit"},
    "admin": {"project:create", "admin:manage_users", "admin:manage_org", "admin:view_audit"},
    "scientist": {"project:create"},
    "reviewer": set(),
    "viewer": set(),
    "service_account": set(),
}

PROJECT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "project_owner": {
        "project:read",
        "project:update",
        "project:delete",
        "run:create",
        "run:read",
        "run:cancel",
        "artifact:read",
        "artifact:export",
        "review:read",
        "review:write",
        "experiment:import",
        "experiment:read",
        "integration:read",
        "integration:write",
        "integration:manage",
        "integration:sync",
        "integration:approve_mapping",
        "integration:manage_credentials",
        "integration:view_audit",
        "codex:run",
        "codex:read",
        "design:read",
        "design:run",
        "design:approve_plan",
        "design:export",
        "model:read",
        "model:train",
        "model:predict",
        "model:register",
        "model:deactivate",
        "model:export",
        "structure:read",
        "structure:run",
        "structure:dock",
        "structure:export",
        "structure:approve",
        "portfolio:read",
        "portfolio:run",
        "portfolio:approve_stage_gate",
        "portfolio:export",
        "portfolio:configure",
        "graph:read",
        "graph:build",
        "graph:query",
        "graph:export",
        "graph:admin",
        "hypothesis:read",
        "hypothesis:generate",
        "hypothesis:review",
        "hypothesis:export",
        "hypothesis:admin",
        "campaign:read",
        "campaign:create",
        "campaign:plan",
        "campaign:approve",
        "campaign:update",
        "campaign:export",
        "campaign:admin",
        "evaluation:read",
        "evaluation:run",
        "evaluation:export",
        "evaluation:admin",
        "agent:read",
        "agent:plan",
        "agent:execute",
        "agent:approve",
        "agent:admin",
        "admin:view_audit",
    },
    "editor": {
        "project:read",
        "project:update",
        "run:create",
        "run:read",
        "run:cancel",
        "artifact:read",
        "artifact:export",
        "review:read",
        "review:write",
        "experiment:import",
        "experiment:read",
        "integration:read",
        "integration:write",
        "integration:sync",
        "codex:read",
        "design:read",
        "design:run",
        "design:approve_plan",
        "model:read",
        "model:train",
        "model:predict",
        "model:register",
        "model:export",
        "structure:read",
        "structure:run",
        "structure:dock",
        "structure:export",
        "structure:approve",
        "portfolio:read",
        "portfolio:run",
        "portfolio:export",
        "portfolio:configure",
        "graph:read",
        "graph:build",
        "graph:query",
        "graph:export",
        "hypothesis:read",
        "hypothesis:generate",
        "hypothesis:export",
        "campaign:read",
        "campaign:create",
        "campaign:plan",
        "campaign:update",
        "campaign:export",
        "evaluation:read",
        "evaluation:run",
        "evaluation:export",
        "agent:read",
        "agent:plan",
        "agent:execute",
    },
    "reviewer": {
        "project:read",
        "run:read",
        "artifact:read",
        "review:read",
        "review:write",
        "experiment:read",
        "integration:read",
        "integration:approve_mapping",
        "codex:read",
        "design:read",
        "design:approve_plan",
        "model:read",
        "structure:read",
        "portfolio:read",
        "portfolio:approve_stage_gate",
        "graph:read",
        "graph:query",
        "hypothesis:read",
        "hypothesis:review",
        "campaign:read",
        "campaign:approve",
        "evaluation:read",
        "agent:read",
        "agent:approve",
    },
    "viewer": {
        "project:read",
        "run:read",
        "artifact:read",
        "review:read",
        "experiment:read",
        "integration:read",
        "codex:read",
        "design:read",
        "model:read",
        "structure:read",
        "portfolio:read",
        "graph:read",
        "graph:query",
        "hypothesis:read",
        "campaign:read",
        "evaluation:read",
        "agent:read",
    },
    "runner": {
        "project:read",
        "run:create",
        "run:read",
        "run:cancel",
        "artifact:read",
        "experiment:read",
        "integration:read",
        "integration:sync",
        "codex:read",
        "design:read",
        "design:run",
        "model:read",
        "model:train",
        "model:predict",
        "structure:read",
        "structure:run",
        "portfolio:read",
        "portfolio:run",
        "graph:read",
        "graph:build",
        "graph:query",
        "hypothesis:read",
        "hypothesis:generate",
        "campaign:read",
        "campaign:create",
        "campaign:plan",
        "evaluation:read",
        "evaluation:run",
        "agent:read",
        "agent:plan",
        "agent:execute",
    },
}

ACTION_PERMISSION: dict[ProjectAction, Permission] = {
    "read": "project:read",
    "write": "project:update",
    "admin": "admin:view_audit",
    "run_codex": "codex:run",
}


class ProjectLike(Protocol):
    workspace_id: str


def has_permission(
    user: UserAccount,
    permission: str,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
    database: PlatformDatabase | None = None,
) -> bool:
    """Return whether a user has a permission in the given org/project scope."""

    if not _service_account_scope_allows(user, permission):
        return False
    if database is None:
        return _metadata_permission(user, permission, org_id=org_id, project_id=project_id)
    if permission.startswith("admin:") and user.is_admin:
        return True
    if org_id is not None and _org_permission_allows(database, user, permission, org_id=org_id):
        return True
    if project_id is not None and _project_permission_allows(
        database,
        user,
        permission,
        project_id=project_id,
    ):
        return True
    return False


def can_access_project(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_id: str,
    action: ProjectAction,
) -> bool:
    return has_permission(
        user,
        ACTION_PERMISSION[action],
        project_id=project_id,
        database=database,
    )


def require_project_access(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_id: str,
    action: ProjectAction,
) -> None:
    permission = ACTION_PERMISSION[action]
    if not has_permission(user, permission, project_id=project_id, database=database):
        _audit_denied_if_configured(
            database,
            user,
            permission,
            org_id=None,
            project_id=project_id,
        )
        raise HTTPException(status_code=403, detail="Project permission denied.")


def require_platform_admin(user: UserAccount) -> None:
    if not _service_account_scope_allows(user, "admin:manage_users"):
        raise HTTPException(status_code=403, detail="Admin role required.")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required.")


def visible_project_ids(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    org_id: str | None = None,
) -> set[str]:
    if not _service_account_scope_allows(user, "project:read"):
        return set()
    with database.engine.connect() as connection:
        rows = connection.execute(select(project_permissions)).mappings().fetchall()
        member_rows = (
            connection.execute(select(memberships).where(memberships.c.user_id == user.user_id))
            .mappings()
            .fetchall()
        )
        project_rows = connection.execute(select(project_workspaces)).mappings().fetchall()
    deleted_project_ids = {
        str(row["project_id"])
        for row in project_rows
        if dict(row["metadata_json"] or {}).get("deleted_at")
    }
    org_ids = {str(row["org_id"]) for row in member_rows}
    team_ids = {str(row["team_id"]) for row in member_rows if row["team_id"]}
    visible: set[str] = set()
    for row in rows:
        if org_id is not None and row["principal_type"] == "org" and row["principal_id"] != org_id:
            continue
        if row["role"] not in PROJECT_ROLE_PERMISSIONS:
            continue
        if "project:read" not in PROJECT_ROLE_PERMISSIONS[str(row["role"])]:
            continue
        if row["principal_type"] == "user" and row["principal_id"] == user.user_id:
            visible.add(str(row["project_id"]))
        elif row["principal_type"] == "org" and row["principal_id"] in org_ids:
            visible.add(str(row["project_id"]))
        elif row["principal_type"] == "team" and row["principal_id"] in team_ids:
            visible.add(str(row["project_id"]))
    return visible - deleted_project_ids


def filter_visible_projects(
    database: PlatformDatabase,
    user: UserAccount,
    projects: Iterable[Any],
    *,
    id_getter: Callable[[Any], str] | None = None,
    org_id: str | None = None,
) -> list[Any]:
    ids = visible_project_ids(database, user, org_id=org_id)
    get_id = id_getter or _default_project_id
    return [project for project in projects if get_id(project) in ids]


def is_org_member(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    org_id: str,
    roles: set[str] | None = None,
) -> bool:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(memberships).where(
                    (memberships.c.user_id == user.user_id) & (memberships.c.org_id == org_id)
                )
            )
            .mappings()
            .fetchall()
        )
    if roles is None:
        return bool(rows)
    return any(str(row["role"]) in roles for row in rows)


def _project_permission_allows(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    *,
    project_id: str,
) -> bool:
    roles = _effective_project_roles(database, user, project_id=project_id)
    return any(permission in PROJECT_ROLE_PERMISSIONS.get(role, set()) for role in roles)


def _org_permission_allows(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    *,
    org_id: str,
) -> bool:
    roles = _org_roles(database, user.user_id, org_id=org_id)
    return any(permission in ORG_ROLE_PERMISSIONS.get(role, set()) for role in roles)


def _effective_project_roles(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_id: str,
) -> set[str]:
    with database.engine.connect() as connection:
        member_rows = (
            connection.execute(select(memberships).where(memberships.c.user_id == user.user_id))
            .mappings()
            .fetchall()
        )
        permission_rows = (
            connection.execute(
                select(project_permissions).where(project_permissions.c.project_id == project_id)
            )
            .mappings()
            .fetchall()
        )
    org_ids = {str(row["org_id"]) for row in member_rows}
    team_ids = {str(row["team_id"]) for row in member_rows if row["team_id"]}
    roles: set[str] = set()
    for row in permission_rows:
        if row["principal_type"] == "user" and row["principal_id"] == user.user_id:
            roles.add(str(row["role"]))
        elif row["principal_type"] == "org" and row["principal_id"] in org_ids:
            roles.add(str(row["role"]))
        elif row["principal_type"] == "team" and row["principal_id"] in team_ids:
            roles.add(str(row["role"]))
    return roles


def _org_roles(database: PlatformDatabase, user_id: str, *, org_id: str) -> set[str]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(memberships.c.role).where(
                    (memberships.c.user_id == user_id) & (memberships.c.org_id == org_id)
                )
            )
            .mappings()
            .fetchall()
        )
    return {str(row["role"]) for row in rows}


def _service_account_scope_allows(user: UserAccount, permission: str) -> bool:
    if user.auth_provider != "service_account":
        return True
    scopes = set(str(scope) for scope in user.metadata.get("scopes", []))
    return "*" in scopes or permission in scopes


def _metadata_permission(
    user: UserAccount,
    permission: str,
    *,
    org_id: str | None,
    project_id: str | None,
) -> bool:
    permissions = set(str(item) for item in user.metadata.get("permissions", []))
    if permission in permissions or "*" in permissions:
        return True
    if permission.startswith("admin:") and user.is_admin:
        return True
    scoped = user.metadata.get("project_permissions", {})
    if project_id and permission in set(scoped.get(project_id, [])):
        return True
    org_scoped = user.metadata.get("org_permissions", {})
    return bool(org_id and permission in set(org_scoped.get(org_id, [])))


def _audit_denied_if_configured(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    *,
    org_id: str | None,
    project_id: str | None,
) -> None:
    if not bool(getattr(database, "audit_permission_denials", False)):
        return
    database.write_audit(
        "permission_denied",
        actor_user_id=user.user_id,
        org_id=org_id,
        project_id=project_id,
        summary=f"Denied {permission}.",
        object_type="permission",
        object_id=permission,
        metadata={"permission": permission},
    )


def _default_project_id(project: Any) -> str:
    if isinstance(project, dict):
        return str(project.get("workspace_id") or project.get("project_id"))
    return str(getattr(project, "workspace_id", getattr(project, "project_id", "")))
