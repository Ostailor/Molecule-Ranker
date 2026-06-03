from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from molecule_ranker.platform.schemas import Membership, Organization, ProjectPermission, Team

NamespaceKind = Literal[
    "organization",
    "team",
    "project",
    "artifact",
    "job",
    "integration",
    "codex_worker",
    "model_registry",
    "graph",
]


class TenantNamespace(BaseModel):
    kind: NamespaceKind
    org_id: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    integration_id: str | None = None
    codex_job_id: str | None = None
    model_registry_id: str | None = None
    graph_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def organization_scope(org_id: str) -> dict[str, str]:
    return {"org_id": org_id}


def project_scope(org_id: str, project_id: str) -> dict[str, str]:
    return {"org_id": org_id, "project_id": project_id}


__all__ = [
    "Membership",
    "NamespaceKind",
    "Organization",
    "ProjectPermission",
    "TenantNamespace",
    "Team",
    "organization_scope",
    "project_scope",
]
