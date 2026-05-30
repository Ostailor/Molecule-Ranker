from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from molecule_ranker.platform.auth import generate_opaque_token
from molecule_ranker.platform.dashboard import render_hosted_dashboard
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.rbac import (
    has_permission,
    require_platform_admin,
    require_project_access,
)
from molecule_ranker.platform.schemas import RetentionPolicy, UserAccount
from molecule_ranker.server.dependencies import current_user, platform_database, workspace_store
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["platform"])


class UserCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["user"])


class OrganizationCreateRequest(BaseModel):
    name: str
    org_id: str | None = None


class TeamCreateRequest(BaseModel):
    org_id: str
    name: str
    team_id: str | None = None


class ProjectShareRequest(BaseModel):
    role: str
    user_id: str | None = None
    org_id: str | None = None
    team_id: str | None = None


class ProjectCommentRequest(BaseModel):
    body: str
    object_type: str = "project"
    object_id: str | None = None
    run_id: str | None = None
    candidate_id: str | None = None


class AssignmentCreateRequest(BaseModel):
    assigned_to_user_id: str
    object_type: str = "review_item"
    object_id: str
    run_id: str | None = None
    candidate_id: str | None = None


class DesignJobRequest(BaseModel):
    job_type: str
    run_id: str | None = None
    budget: int | None = Field(default=None, ge=0)
    budget_limit: int | None = Field(default=None, ge=0)
    random_seed: int | None = None
    generator: list[str] = Field(default_factory=list)
    use_codex_planner: bool = False
    plan_approved: bool = False
    warning_acknowledged: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class ModelJobRequest(BaseModel):
    job_type: str
    model_id: str | None = None
    endpoint_name: str | None = None
    dataset_id: str | None = None
    run_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class StructureJobRequest(BaseModel):
    job_type: str
    target_symbol: str | None = None
    run_id: str | None = None
    structure_id: str | None = None
    binding_site_id: str | None = None
    enable_docking: bool = False
    warning_acknowledged: bool = False
    max_ligands: int | None = Field(default=None, ge=0)
    budget_limit: int | None = Field(default=None, ge=0)
    use_codex_planner: bool = False
    structure_plan_approved: bool = False
    approval_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class DesignPlanApprovalRequest(BaseModel):
    plan_id: str
    run_id: str | None = None
    approval_note: str | None = None


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8)


class MembershipCreateRequest(BaseModel):
    user_id: str
    org_id: str
    role: str
    team_id: str | None = None


class ServiceAccountCreateRequest(BaseModel):
    name: str
    user_id: str
    scopes: list[str] = Field(default_factory=list)
    expires_in_seconds: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceAccountRevokeRequest(BaseModel):
    token_id: str


class PlatformSettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/admin/users")
def create_user(
    request: UserCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    created = database.create_user(
        email=request.email,
        password=request.password,
        display_name=request.display_name,
        roles=request.roles,
    )
    return {"user": public_user(created)}


@router.get("/admin/users")
def list_users(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return {"users": [public_user(item) for item in database.list_users()]}


@router.post("/admin/users/{user_id}/activate")
def activate_user(
    user_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    database.activate_user(user_id, actor_user_id=user.user_id)
    return {"activated": True, "user_id": user_id}


@router.post("/admin/users/{user_id}/deactivate")
def deactivate_user(
    user_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    database.disable_user(user_id, actor_user_id=user.user_id)
    return {"deactivated": True, "user_id": user_id}


@router.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    request: PasswordResetRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    try:
        database.reset_local_password(
            user_id=user_id,
            new_password=request.new_password,
            actor_user_id=user.user_id,
        )
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"reset": True, "user_id": user_id}


@router.get("/admin/orgs")
def admin_orgs(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return {
        "organizations": [
            item.model_dump(mode="json") for item in database.list_organizations()
        ]
    }


@router.get("/admin/teams")
def admin_teams(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    from molecule_ranker.web.components import list_admin_teams

    return {"teams": list_admin_teams(database)}


@router.get("/admin/memberships")
def admin_memberships(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return {"memberships": database.list_memberships()}


@router.post("/admin/memberships")
def admin_add_membership(
    request: MembershipCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    membership_id = database.add_membership(
        user_id=request.user_id,
        org_id=request.org_id,
        team_id=request.team_id,
        role=request.role,
        actor_user_id=user.user_id,
    )
    return {"membership_id": membership_id}


@router.delete("/admin/memberships/{membership_id}")
def admin_remove_membership(
    membership_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    removed = database.remove_membership(membership_id, actor_user_id=user.user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Membership not found.")
    return {"removed": True, "membership_id": membership_id}


@router.get("/admin/service-accounts")
def admin_service_accounts(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return {"service_accounts": database.list_service_account_tokens()}


@router.post("/admin/service-accounts")
def admin_create_service_account(
    request: ServiceAccountCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    target_user = database.get_user(request.user_id)
    if target_user is None or not target_user.is_active:
        raise HTTPException(status_code=404, detail="Service account user not found or inactive.")
    token = generate_opaque_token(prefix="mrs")
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=request.expires_in_seconds)
        if request.expires_in_seconds
        else None
    )
    token_id = database.create_service_account_token(
        name=request.name,
        token=token,
        user_id=target_user.user_id,
        created_by_user_id=user.user_id,
        scopes=request.scopes,
        expires_at=expires_at,
        metadata=request.metadata,
    )
    return {
        "token_id": token_id,
        "access_token": token,
        "token_type": "bearer",
        "shown_once": True,
        "scopes": request.scopes,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.post("/admin/service-accounts/revoke")
def admin_revoke_service_account(
    request: ServiceAccountRevokeRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    revoked = database.revoke_service_account_token(
        token_id=request.token_id,
        actor_user_id=user.user_id,
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="Service account token not found.")
    return {"revoked": True, "token_id": request.token_id}


@router.get("/admin/jobs")
def admin_jobs(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    status: str | None = None,
) -> dict[str, Any]:
    require_platform_admin(user)
    jobs = PlatformJobQueue(database).list_jobs(status=status)
    return {
        "jobs": [job.model_dump(mode="json") for job in jobs],
        "failed_jobs": database.list_failed_jobs(),
    }


@router.get("/admin/audit")
def admin_audit(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    limit: int = 100,
) -> dict[str, Any]:
    require_platform_admin(user)
    return {
        "events": [
            event.model_dump(mode="json") for event in database.list_audit_events(limit=limit)
        ]
    }


@router.get("/admin/health")
def admin_health(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return database.health()


@router.get("/admin/codex-status")
def admin_codex_status(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return database.codex_worker_status()


@router.put("/admin/settings")
def admin_configure_settings(
    request: PlatformSettingsRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return {
        "settings": database.configure_platform_settings(
            actor_user_id=user.user_id,
            settings=request.settings,
        )
    }


@router.post("/organizations")
def create_organization(
    request: OrganizationCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    organization = database.create_organization(
        name=request.name,
        org_id=request.org_id,
        created_by_user_id=user.user_id,
    )
    return organization.model_dump(mode="json")


@router.get("/organizations")
def list_organizations(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if "platform_admin" in user.roles:
        organizations = database.list_organizations()
    else:
        organizations = database.list_organizations(user_id=user.user_id)
    return {"organizations": [item.model_dump(mode="json") for item in organizations]}


@router.post("/teams")
def create_team(
    request: TeamCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    team = database.create_team(
        org_id=request.org_id,
        name=request.name,
        team_id=request.team_id,
        created_by_user_id=user.user_id,
    )
    return team.model_dump(mode="json")


@router.post("/projects/{project_id}/share")
def share_project(
    project_id: str,
    request: ProjectShareRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_project_access(database, user, project_id=project_id, action="admin")
    permission = database.grant_project_permission(
        project_id=project_id,
        role=request.role,
        actor_user_id=user.user_id,
        user_id=request.user_id,
        org_id=request.org_id,
        team_id=request.team_id,
    )
    return permission.model_dump(mode="json")


@router.get("/projects/{project_id}/permissions")
def project_permissions(
    project_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_project_access(database, user, project_id=project_id, action="admin")
    return {
        "project_id": project_id,
        "permissions": [
            permission.model_dump(mode="json")
            for permission in database.project_permissions(project_id)
        ],
    }


@router.post("/projects/{project_id}/comments")
def add_project_comment(
    project_id: str,
    request: ProjectCommentRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_project_access(database, user, project_id=project_id, action="read")
    try:
        comment = database.add_project_comment(
            project_id=project_id,
            author_user_id=user.user_id,
            body=request.body,
            object_type=request.object_type,
            object_id=request.object_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return comment.model_dump(mode="json")


@router.get("/projects/{project_id}/comments")
def project_comments(
    project_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    object_type: str | None = None,
    object_id: str | None = None,
) -> dict[str, Any]:
    require_project_access(database, user, project_id=project_id, action="read")
    return {
        "comments": [
            item.model_dump(mode="json")
            for item in database.list_project_comments(
                project_id=project_id,
                object_type=object_type,
                object_id=object_id,
            )
        ]
    }


@router.post("/projects/{project_id}/assignments")
def create_assignment(
    project_id: str,
    request: AssignmentCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    try:
        assignment = database.create_assignment(
            project_id=project_id,
            assigned_to_user_id=request.assigned_to_user_id,
            assigned_by_user_id=user.user_id,
            object_type=request.object_type,
            object_id=request.object_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return assignment.model_dump(mode="json")


@router.get("/projects/{project_id}/activity")
def project_activity(
    project_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_project_access(database, user, project_id=project_id, action="read")
    return {
        "activity": [
            item.model_dump(mode="json") for item in database.list_activity(project_id=project_id)
        ]
    }


@router.post("/projects/{project_id}/design/jobs")
def enqueue_design_job(
    project_id: str,
    request: DesignJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    config = dict(request.config)
    if request.run_id is not None:
        config["run_id"] = request.run_id
    if request.budget is not None:
        config["budget"] = request.budget
    if request.budget_limit is not None:
        config["budget_limit"] = request.budget_limit
    if request.random_seed is not None:
        config["random_seed"] = request.random_seed
    if request.generator:
        config["generators"] = list(request.generator)
    config["use_codex_planner"] = request.use_codex_planner
    config["plan_approved"] = request.plan_approved
    config["generated_molecule_warning_acknowledged"] = request.warning_acknowledged
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "design_v1_1": True,
                "generated_molecule_label": "computational_hypothesis",
                "human_review_required": True,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "generated_molecule_label": "computational_hypothesis",
        "warning": "Generated molecules are computational hypotheses and require expert review.",
    }


@router.post("/projects/{project_id}/model/jobs")
def enqueue_model_job(
    project_id: str,
    request: ModelJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    config = dict(request.config)
    if request.model_id is not None:
        config["model_id"] = request.model_id
    if request.endpoint_name is not None:
        config["endpoint_name"] = request.endpoint_name
    if request.dataset_id is not None:
        config["dataset_id"] = request.dataset_id
    if request.run_id is not None:
        config["run_id"] = request.run_id
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "predictive_model_v1_2": True,
                "predictions_are_not_evidence": True,
                "predictions_are_not_assay_results": True,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "prediction_boundary": "model_predictions_are_not_evidence_or_assay_results",
    }


@router.post("/projects/{project_id}/structure/jobs")
def enqueue_structure_job(
    project_id: str,
    request: StructureJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    config = dict(request.config)
    for key, value in {
        "target_symbol": request.target_symbol,
        "run_id": request.run_id,
        "structure_id": request.structure_id,
        "binding_site_id": request.binding_site_id,
        "max_ligands": request.max_ligands,
        "budget_limit": request.budget_limit,
        "approval_id": request.approval_id,
    }.items():
        if value is not None:
            config[key] = value
    config["enable_docking"] = request.enable_docking
    config["use_codex_planner"] = request.use_codex_planner
    config["structure_plan_approved"] = request.structure_plan_approved
    if request.warning_acknowledged:
        config["structure_warning_acknowledged"] = True
        config["docking_limitations_acknowledged"] = True
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "structure_v1_3": True,
                "structure_reports_not_binding_evidence": True,
                "human_review_required": True,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "structure_report_boundary": "not_binding_evidence",
        "warning": "Structure reports are computational prioritization artifacts only.",
    }


@router.post("/projects/{project_id}/design/plans/approve")
def approve_design_plan(
    project_id: str,
    request: DesignPlanApprovalRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if not user.is_admin:
        if not has_permission(
            user,
            "design:approve_plan",
            project_id=project_id,
            database=database,
        ):
            raise HTTPException(status_code=403, detail="Design plan approval denied.")
    database.write_audit(
        "design_plan_approved",
        actor_user_id=user.user_id,
        project_id=project_id,
        summary=f"Approved design plan {request.plan_id}.",
        object_type="design_plan",
        object_id=request.plan_id,
        metadata={"run_id": request.run_id, "approval_note": request.approval_note},
    )
    return {"approved": True, "plan_id": request.plan_id, "project_id": project_id}


@router.get("/notifications")
def notifications(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    unread_only: bool = False,
) -> dict[str, Any]:
    return {
        "notifications": [
            item.model_dump(mode="json")
            for item in database.list_notifications(user_id=user.user_id, unread_only=unread_only)
        ]
    }


@router.get("/audit/events")
def audit_events(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if project_id is not None:
        require_project_access(database, user, project_id=project_id, action="admin")
    elif "platform_admin" not in user.roles:
        raise HTTPException(status_code=403, detail="Admin role required.")
    return {
        "events": [
            event.model_dump(mode="json")
            for event in database.list_audit_events(project_id=project_id, limit=limit)
        ]
    }


@router.get("/ops/health")
def ops_health(
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return database.health()


@router.get("/dashboard", response_class=Response)
def dashboard(
    _user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return Response(
        content=render_hosted_dashboard(database=database, workspace_store=store),
        media_type="text/html",
    )


@router.get("/data/users/{user_id}/export")
def export_user_data(
    user_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if user.user_id != user_id:
        require_platform_admin(user)
    try:
        return database.export_user_data(user_id)
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/data/users/{user_id}")
def delete_user_data(
    user_id: str,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if user.user_id != user_id:
        require_platform_admin(user)
    try:
        database.delete_user(user_id, actor_user_id=user.user_id)
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True, "user_id": user_id}


@router.put("/admin/retention")
def set_retention_policy(
    request: RetentionPolicy,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    return database.set_retention_policy(request, actor_user_id=user.user_id).model_dump(
        mode="json"
    )


def public_user(user: UserAccount) -> dict[str, Any]:
    return user.model_dump(mode="json", exclude={"last_login_at"}) | {
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None
    }
