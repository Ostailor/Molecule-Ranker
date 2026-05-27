from __future__ import annotations

from molecule_ranker.platform.schemas import Membership, Organization, ProjectPermission, Team


def organization_scope(org_id: str) -> dict[str, str]:
    return {"org_id": org_id}


def project_scope(org_id: str, project_id: str) -> dict[str, str]:
    return {"org_id": org_id, "project_id": project_id}


__all__ = [
    "Membership",
    "Organization",
    "ProjectPermission",
    "Team",
    "organization_scope",
    "project_scope",
]
