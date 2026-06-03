from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from molecule_ranker.platform.database import (
    artifact_records,
    integration_connectors,
    platform_jobs,
    project_workspaces,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.platform.tenancy import TenantNamespace
from molecule_ranker.workspace.schemas import ArtifactRecord, ProjectWorkspace


class IsolationViolation(PermissionError):
    def __init__(
        self,
        message: str,
        *,
        namespace: TenantNamespace,
        permission: str | None = None,
    ) -> None:
        self.namespace = namespace
        self.permission = permission
        super().__init__(message)


def require_project_access(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_id: str,
    permission: str = "project:read",
    org_id: str | None = None,
) -> TenantNamespace:
    namespace = project_namespace(database, project_id=project_id)
    if org_id is not None and namespace.org_id is not None and namespace.org_id != org_id:
        raise IsolationViolation(
            f"Project {project_id} does not belong to organization {org_id}.",
            namespace=namespace,
            permission=permission,
        )
    if _allowed(database, user, permission, namespace):
        return namespace
    raise IsolationViolation(
        f"Missing {permission} for project {project_id}.",
        namespace=namespace,
        permission=permission,
    )


def require_artifact_access(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    artifact_id: str,
    project_id: str | None = None,
    permission: str = "artifact:read",
) -> TenantNamespace:
    namespace = artifact_namespace(database, artifact_id=artifact_id)
    if project_id is not None and namespace.project_id != project_id:
        raise IsolationViolation(
            f"Artifact {artifact_id} does not belong to project {project_id}.",
            namespace=namespace,
            permission=permission,
        )
    if namespace.project_id is None:
        raise IsolationViolation(
            f"Artifact {artifact_id} is missing project scope.",
            namespace=namespace,
            permission=permission,
        )
    if _allowed(database, user, permission, namespace):
        return namespace
    raise IsolationViolation(
        f"Missing {permission} for artifact {artifact_id}.",
        namespace=namespace,
        permission=permission,
    )


def require_workspace_artifact_access(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    workspace: ProjectWorkspace,
    artifact: ArtifactRecord,
    permission: str = "artifact:read",
) -> TenantNamespace:
    if artifact.workspace_id != workspace.workspace_id:
        namespace = TenantNamespace(
            kind="artifact",
            project_id=artifact.workspace_id,
            artifact_id=artifact.artifact_id,
        )
        raise IsolationViolation(
            f"Artifact {artifact.artifact_id} does not belong to workspace "
            f"{workspace.workspace_id}.",
            namespace=namespace,
            permission=permission,
        )
    metadata_project = artifact.metadata.get("project_id")
    if metadata_project is not None and str(metadata_project) != workspace.workspace_id:
        namespace = TenantNamespace(
            kind="artifact",
            project_id=str(metadata_project),
            artifact_id=artifact.artifact_id,
        )
        raise IsolationViolation(
            f"Artifact {artifact.artifact_id} metadata points outside the project namespace.",
            namespace=namespace,
            permission=permission,
        )
    namespace = TenantNamespace(
        kind="artifact",
        org_id=str(artifact.metadata.get("org_id")) if artifact.metadata.get("org_id") else None,
        project_id=workspace.workspace_id,
        artifact_id=artifact.artifact_id,
    )
    if _allowed(database, user, permission, namespace):
        return namespace
    raise IsolationViolation(
        f"Missing {permission} for artifact {artifact.artifact_id}.",
        namespace=namespace,
        permission=permission,
    )


def validate_codex_artifact_scope(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    job_project_id: str,
    artifact_ids: Iterable[str],
    workspace_artifact_ids: Iterable[str] | None = None,
    allow_workspace_artifacts: bool = False,
) -> list[TenantNamespace]:
    require_project_access(
        database,
        user,
        project_id=job_project_id,
        permission="codex:run",
    )
    namespaces: list[TenantNamespace] = []
    workspace_ids = {str(item) for item in workspace_artifact_ids or []}
    for artifact_id in sorted({str(item) for item in artifact_ids}):
        try:
            namespaces.append(
                require_artifact_access(
                    database,
                    user,
                    artifact_id=artifact_id,
                    project_id=job_project_id,
                    permission="artifact:read",
                )
            )
        except IsolationViolation as exc:
            if (
                allow_workspace_artifacts
                and exc.namespace.project_id is None
                and artifact_id in workspace_ids
            ):
                require_project_access(
                    database,
                    user,
                    project_id=job_project_id,
                    permission="artifact:read",
                )
                namespaces.append(
                    TenantNamespace(
                        kind="artifact",
                        project_id=job_project_id,
                        artifact_id=artifact_id,
                        metadata={"registry": "workspace"},
                    )
                )
                continue
            raise
    return namespaces


def require_connector_access(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    connector_id: str,
    permission: str = "integration:read",
) -> TenantNamespace:
    namespace = integration_namespace(database, connector_id=connector_id)
    if user.is_admin:
        return namespace
    if namespace.project_id is None:
        raise IsolationViolation(
            f"Integration connector {connector_id} is missing project scope.",
            namespace=namespace,
            permission=permission,
        )
    if _allowed(database, user, permission, namespace):
        return namespace
    raise IsolationViolation(
        f"Missing {permission} for integration connector {connector_id}.",
        namespace=namespace,
        permission=permission,
    )


def require_cross_project_permissions(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_ids: Iterable[str],
    permission: str,
    domain: str,
) -> list[TenantNamespace]:
    namespaces: list[TenantNamespace] = []
    for project_id in sorted({str(item) for item in project_ids if str(item).strip()}):
        try:
            namespace = require_project_access(
                database,
                user,
                project_id=project_id,
                permission=permission,
            )
        except IsolationViolation as exc:
            raise IsolationViolation(
                f"Missing {permission} for {domain} cross-project scope {project_id}.",
                namespace=exc.namespace,
                permission=permission,
            ) from exc
        namespaces.append(namespace)
    return namespaces


def project_namespace(database: PlatformDatabase, *, project_id: str) -> TenantNamespace:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(project_workspaces).where(project_workspaces.c.project_id == project_id)
            )
            .mappings()
            .first()
        )
    return TenantNamespace(
        kind="project",
        org_id=str(row["org_id"]) if row and row["org_id"] else None,
        project_id=project_id,
    )


def artifact_namespace(database: PlatformDatabase, *, artifact_id: str) -> TenantNamespace:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(artifact_records).where(artifact_records.c.artifact_id == artifact_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise IsolationViolation(
            f"Artifact {artifact_id} not found.",
            namespace=TenantNamespace(kind="artifact", artifact_id=artifact_id),
        )
    return TenantNamespace(
        kind="artifact",
        org_id=str(row["org_id"]) if row["org_id"] else None,
        project_id=str(row["project_id"]) if row["project_id"] else None,
        artifact_id=artifact_id,
        metadata={"path": str(row["path"])},
    )


def integration_namespace(database: PlatformDatabase, *, connector_id: str) -> TenantNamespace:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(integration_connectors).where(
                    integration_connectors.c.connector_id == connector_id
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise IsolationViolation(
            f"Integration connector {connector_id} not found.",
            namespace=TenantNamespace(kind="integration", integration_id=connector_id),
        )
    return TenantNamespace(
        kind="integration",
        org_id=str(row["org_id"]) if row["org_id"] else None,
        project_id=str(row["project_id"]) if row["project_id"] else None,
        integration_id=connector_id,
    )


def run_isolation_audit(database: PlatformDatabase) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    with database.engine.connect() as connection:
        job_rows = connection.execute(
            select(platform_jobs.c.job_id, platform_jobs.c.org_id, platform_jobs.c.project_id)
        ).mappings().fetchall()
        artifact_rows = connection.execute(
            select(
                artifact_records.c.artifact_id,
                artifact_records.c.org_id,
                artifact_records.c.project_id,
                artifact_records.c.path,
            )
        ).mappings().fetchall()
        connector_rows = connection.execute(
            select(
                integration_connectors.c.connector_id,
                integration_connectors.c.org_id,
                integration_connectors.c.project_id,
            )
        ).mappings().fetchall()
    for row in job_rows:
        if not row["org_id"] or not row["project_id"]:
            findings.append(
                _finding(
                    "job_namespace_scope",
                    "platform_jobs",
                    str(row["job_id"]),
                    "Job is missing organization or project namespace.",
                )
            )
    for row in artifact_rows:
        if not row["org_id"] or not row["project_id"]:
            findings.append(
                _finding(
                    "artifact_namespace_scope",
                    "artifact_records",
                    str(row["artifact_id"]),
                    "Artifact is missing organization or project namespace.",
                )
            )
        path_text = str(row["path"] or "")
        if _path_contains_cache_or_secret(path_text):
            findings.append(
                _finding(
                    "artifact_namespace_path",
                    "artifact_records",
                    str(row["artifact_id"]),
                    "Artifact path points at cache or secret-like material.",
                )
            )
    for row in connector_rows:
        if not row["org_id"] or not row["project_id"]:
            findings.append(
                _finding(
                    "integration_namespace_scope",
                    "integration_connectors",
                    str(row["connector_id"]),
                    "Integration connector is missing organization or project namespace.",
                )
            )
    return {
        "status": "pass" if not findings else "fail",
        "checks": {
            "job_namespace_scope": len(job_rows),
            "artifact_namespace_scope": len(artifact_rows),
            "integration_namespace_scope": len(connector_rows),
        },
        "finding_count": len(findings),
        "findings": findings,
    }


def _allowed(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    namespace: TenantNamespace,
) -> bool:
    return user.is_admin or has_permission(
        user,
        permission,
        org_id=namespace.org_id,
        project_id=namespace.project_id,
        database=database,
    )


def _finding(check_id: str, table: str, object_id: str, message: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "table": table,
        "object_id": object_id,
        "message": message,
    }


def _path_contains_cache_or_secret(path_text: str) -> bool:
    lowered = Path(path_text).name.lower()
    full = path_text.lower()
    return any(
        marker in full
        for marker in (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
    ) or any(marker in lowered for marker in ("secret", "credential", ".env"))


__all__ = [
    "IsolationViolation",
    "artifact_namespace",
    "integration_namespace",
    "project_namespace",
    "require_artifact_access",
    "require_connector_access",
    "require_cross_project_permissions",
    "require_project_access",
    "require_workspace_artifact_access",
    "run_isolation_audit",
    "validate_codex_artifact_scope",
]
