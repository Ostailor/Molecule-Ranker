from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from molecule_ranker.campaigns import CampaignExecutionEvent, CampaignStore
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


class PortfolioJobRequest(BaseModel):
    job_type: str
    run_id: str | None = None
    candidate_id: str | None = None
    scenarios: list[str] = Field(default_factory=list)
    scenario: list[str] = Field(default_factory=list)
    batch_type: str | None = None
    use_codex: bool = False
    external_export: bool = False
    explicit_export_permission: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class GraphJobRequest(BaseModel):
    job_type: str
    query: str | None = None
    target_symbol: str | None = None
    disease: str | None = None
    molecule_id: str | None = None
    candidate_id: str | None = None
    graph_artifact_id: str | None = None
    graph_path: str | None = None
    run_id: str | None = None
    from_run: str | None = None
    output_format: str | None = None
    included_project_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class HypothesisJobRequest(BaseModel):
    job_type: str
    hypothesis_id: str | None = None
    hypothesis_type: str | None = None
    decision: str | None = None
    reviewer_id: str | None = None
    rationale: str | None = None
    use_codex_drafting: bool = False
    max_hypotheses: int | None = Field(default=None, ge=1)
    human_review_approved: bool = False
    follow_up_planning: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class HypothesisReviewRequest(BaseModel):
    decision: str
    reviewer_id: str | None = None
    rationale: str
    human_review_approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignJobRequest(BaseModel):
    job_type: str
    campaign_id: str | None = None
    campaign_plan_id: str | None = None
    work_package_id: str | None = None
    stage_gate_id: str | None = None
    event_artifact_id: str | None = None
    strategy: str = "balanced"
    use_codex: bool = False
    campaign_approval: bool = False
    stage_gate_approval: bool = False
    generated_molecule_followup: bool = False
    generated_review_gate_present: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class EvaluationJobRequest(BaseModel):
    job_type: str
    suite_id: str | None = None
    dataset_id: str | None = None
    split_id: str | None = None
    prospective_run_id: str | None = None
    frozen_prediction_set_id: str | None = None
    report_id: str | None = None
    metric: str | None = None
    from_run: str | None = None
    output_artifact_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CampaignStageGateApprovalRequest(BaseModel):
    reviewer_id: str | None = None
    decision: str = "approved"
    rationale: str


class DesignPlanApprovalRequest(BaseModel):
    plan_id: str
    run_id: str | None = None
    approval_note: str | None = None


class PortfolioStageGateApprovalRequest(BaseModel):
    candidate_id: str
    stage_gate_id: str | None = None
    run_id: str | None = None
    decision: str = "advance"
    reviewer_id: str | None = None
    approval_note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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


@router.post("/projects/{project_id}/portfolio/jobs")
def enqueue_portfolio_job(
    project_id: str,
    request: PortfolioJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    portfolio_job_types = {
        "portfolio_build_candidates",
        "portfolio_optimize",
        "portfolio_scenario_analysis",
        "portfolio_stage_gate",
        "portfolio_batch_build",
        "portfolio_memo",
    }
    if request.job_type not in portfolio_job_types:
        raise HTTPException(status_code=400, detail="Unsupported portfolio job type.")
    config = dict(request.config)
    for key, value in {
        "run_id": request.run_id,
        "candidate_id": request.candidate_id,
        "batch_type": request.batch_type,
    }.items():
        if value is not None:
            config[key] = value
    scenarios = request.scenarios or request.scenario
    if scenarios:
        config["scenarios"] = list(scenarios)
    config["use_codex"] = request.use_codex
    config["codex_memos_are_assistant_output"] = True
    config["portfolio_output_advisory_until_approved"] = True
    if request.external_export:
        config["external_export"] = True
    if request.explicit_export_permission:
        config["explicit_export_permission"] = True
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "portfolio_v1_4": True,
                "advisory_until_approved": True,
                "codex_memo_not_final_decision": True,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "portfolio_boundary": "advisory_until_approved",
        "memo_boundary": "codex_memos_are_assistant_output_not_final_decisions",
    }


@router.post("/projects/{project_id}/graph/jobs")
def enqueue_graph_job(
    project_id: str,
    request: GraphJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    graph_job_types = {
        "graph_build",
        "graph_query",
        "graph_mechanism_extract",
        "graph_contradiction_scan",
        "graph_staleness_scan",
        "graph_recommendation",
        "graph_export",
    }
    if request.job_type not in graph_job_types:
        raise HTTPException(status_code=400, detail="Unsupported graph job type.")
    require_project_access(database, user, project_id=project_id, action="read")
    config = dict(request.config)
    for key, value in {
        "query": request.query,
        "target_symbol": request.target_symbol,
        "disease": request.disease,
        "molecule_id": request.molecule_id,
        "candidate_id": request.candidate_id,
        "graph_artifact_id": request.graph_artifact_id,
        "graph_path": request.graph_path,
        "run_id": request.run_id,
        "from_run": request.from_run,
        "output_format": request.output_format,
    }.items():
        if value is not None:
            config[key] = value
    if request.included_project_ids:
        config["included_project_ids"] = list(request.included_project_ids)
    if request.project_ids:
        config["project_ids"] = list(request.project_ids)
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "knowledge_graph_v1_5": True,
                "graph_memory_layer_only": True,
                "graph_recommendations_advisory": request.job_type == "graph_recommendation",
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "graph_boundary": "memory_and_reasoning_layer_not_new_biomedical_truth",
        "recommendation_boundary": "advisory_not_automatic_decisions",
    }


@router.post("/projects/{project_id}/hypothesis/jobs")
def enqueue_hypothesis_job(
    project_id: str,
    request: HypothesisJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    hypothesis_job_types = {
        "hypothesis_generate",
        "hypothesis_rank",
        "hypothesis_questions",
        "hypothesis_report",
        "hypothesis_review",
    }
    if request.job_type not in hypothesis_job_types:
        raise HTTPException(status_code=400, detail="Unsupported hypothesis job type.")
    config = dict(request.config)
    for key, value in {
        "hypothesis_id": request.hypothesis_id,
        "hypothesis_type": request.hypothesis_type,
        "decision": request.decision,
        "reviewer_id": request.reviewer_id,
        "rationale": request.rationale,
        "max_hypotheses": request.max_hypotheses,
    }.items():
        if value is not None:
            config[key] = value
    config["use_codex_hypothesis_drafting"] = request.use_codex_drafting
    config["strict_hypothesis_guardrails"] = True
    config["deterministic_hypothesis_validation_required"] = True
    config["human_review_approved"] = request.human_review_approved
    config["follow_up_planning"] = request.follow_up_planning
    config["hypotheses_are_not_evidence"] = True
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "hypothesis_v1_6": True,
                "hypotheses_are_not_evidence": True,
                "codex_drafts_require_deterministic_validation": (
                    request.use_codex_drafting
                ),
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "hypothesis_boundary": "hypotheses_are_not_evidence",
        "codex_boundary": "codex_drafts_require_deterministic_validation",
    }


@router.post("/projects/{project_id}/campaign/jobs")
def enqueue_campaign_job(
    project_id: str,
    request: CampaignJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    campaign_job_types = {
        "campaign_create",
        "campaign_plan",
        "campaign_replan",
        "campaign_memo",
        "campaign_export",
    }
    if request.job_type not in campaign_job_types:
        raise HTTPException(status_code=400, detail="Unsupported campaign job type.")
    config = dict(request.config)
    for key, value in {
        "campaign_id": request.campaign_id,
        "campaign_plan_id": request.campaign_plan_id,
        "work_package_id": request.work_package_id,
        "stage_gate_id": request.stage_gate_id,
        "event_artifact_id": request.event_artifact_id,
        "strategy": request.strategy,
    }.items():
        if value is not None:
            config[key] = value
    config["use_codex"] = request.use_codex
    config["campaign_approval"] = request.campaign_approval
    config["stage_gate_approval"] = request.stage_gate_approval
    config["generated_molecule_followup"] = request.generated_molecule_followup
    config["generated_review_gate_present"] = request.generated_review_gate_present
    config["codex_memos_are_assistant_output"] = request.use_codex
    config["campaign_plan_is_deterministic"] = request.job_type in {
        "campaign_create",
        "campaign_plan",
        "campaign_replan",
    }
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "campaign_v1_7": True,
                "research_management_guidance": True,
                "work_packages_not_protocols": True,
                "codex_memo_assistant_output": request.use_codex,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "campaign_boundary": "research_management_guidance_not_lab_protocol",
        "memo_boundary": "codex_memos_are_assistant_output_not_deterministic_plans",
    }


@router.post("/projects/{project_id}/evaluation/jobs")
def enqueue_evaluation_job(
    project_id: str,
    request: EvaluationJobRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    evaluation_job_types = {
        "eval_dataset_build",
        "eval_split",
        "eval_benchmark_run",
        "eval_prospective_freeze",
        "eval_prospective_evaluate",
        "eval_guardrail_benchmark",
        "eval_reproducibility",
        "eval_trend_report",
    }
    if request.job_type not in evaluation_job_types:
        raise HTTPException(status_code=400, detail="Unsupported evaluation job type.")
    config = dict(request.config)
    for key, value in {
        "suite_id": request.suite_id,
        "dataset_id": request.dataset_id,
        "split_id": request.split_id,
        "prospective_run_id": request.prospective_run_id,
        "frozen_prediction_set_id": request.frozen_prediction_set_id,
        "report_id": request.report_id,
        "metric": request.metric,
        "from_run": request.from_run,
        "output_artifact_id": request.output_artifact_id,
    }.items():
        if value is not None:
            config[key] = value
    try:
        job = PlatformJobQueue(database).enqueue(
            job_type=request.job_type,
            requested_by=user,
            project_id=project_id,
            config_snapshot=config,
            metadata={
                "evaluation_v1_8": True,
                "evaluation_reports_are_not_evidence": True,
                "not_clinical_validation": True,
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformDatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": job.status,
        "job": job.model_dump(mode="json"),
        "evaluation_boundary": "evaluation_reports_are_not_evidence",
        "validation_boundary": "not_clinical_validation",
    }


@router.post("/projects/{project_id}/campaigns/{campaign_id}/stage-gates/{stage_gate_id}/approve")
def approve_campaign_stage_gate(
    project_id: str,
    campaign_id: str,
    stage_gate_id: str,
    request: CampaignStageGateApprovalRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if not user.is_admin and not has_permission(
        user,
        "campaign:approve",
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Campaign approval requires permission.")
    reviewer_id = request.reviewer_id or user.user_id
    if reviewer_id.lower().startswith("codex"):
        raise HTTPException(status_code=403, detail="Codex cannot approve campaign stage gates.")
    store = _campaign_store(database, project_id)
    try:
        gate = store.get_stage_gate(stage_gate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Campaign stage gate not found.") from exc
    if str(gate.get("campaign_id")) != campaign_id:
        raise HTTPException(status_code=404, detail="Campaign stage gate not found.")
    now = datetime.now(UTC)
    updated = {
        **gate,
        "approval_status": request.decision,
        "rationale": request.rationale,
        "approved_by": reviewer_id,
        "approved_at": now.isoformat(),
    }
    event = CampaignExecutionEvent(
        event_id=f"campaign-stage-gate-{stage_gate_id}-{now.timestamp()}",
        campaign_id=campaign_id,
        work_package_id=gate.get("work_package_id"),
        event_type="stage_gate_decided",
        actor=reviewer_id,
        timestamp=now,
        summary=f"Hosted stage gate {stage_gate_id} decided as {request.decision}.",
        before={"approval_status": gate.get("approval_status")},
        after={"approval_status": request.decision, "rationale": request.rationale},
        metadata={
            "project_id": project_id,
            "gate_id": stage_gate_id,
            "permission": "campaign:approve",
            "codex_approved": False,
        },
    )
    updated["audit_event"] = event.model_dump(mode="json")
    store.add_stage_gate_decision(updated)
    store.add_execution_event(event)
    audit = database.write_audit(
        "campaign_stage_gate_approved",
        actor_user_id=user.user_id,
        project_id=project_id,
        summary=f"Campaign stage gate {stage_gate_id} decided as {request.decision}.",
        object_type="campaign_stage_gate",
        object_id=stage_gate_id,
        metadata={
            "campaign_id": campaign_id,
            "reviewer_id": reviewer_id,
            "decision": request.decision,
            "campaign_store_event_id": event.event_id,
        },
    )
    return {
        "stage_gate": updated,
        "event": event.model_dump(mode="json"),
        "audit_event": audit.model_dump(mode="json"),
    }


@router.post("/projects/{project_id}/hypotheses/{hypothesis_id}/review")
def review_hypothesis(
    project_id: str,
    hypothesis_id: str,
    request: HypothesisReviewRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if not user.is_admin and not has_permission(
        user,
        "hypothesis:review",
        project_id=project_id,
        database=database,
    ):
        database.write_audit(
            "hypothesis_review_denied",
            actor_user_id=user.user_id,
            project_id=project_id,
            summary=f"Denied hypothesis review for {hypothesis_id}.",
            object_type="hypothesis",
            object_id=hypothesis_id,
            metadata={"permission": "hypothesis:review"},
        )
        raise HTTPException(status_code=403, detail="Hypothesis review denied.")
    from molecule_ranker.hypotheses.review import HypothesisReviewService
    from molecule_ranker.hypotheses.store import HypothesisStore

    store = HypothesisStore(_hosted_hypothesis_store_path(database.root_dir, project_id))
    try:
        decision = HypothesisReviewService(store).record_decision(
            hypothesis_id,
            reviewer_id=request.reviewer_id or user.user_id,
            decision=request.decision,
            rationale=request.rationale,
            human_approval=request.human_review_approved,
            metadata={"hosted_platform": True, **request.metadata},
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    database.write_audit(
        "hypothesis_review_status_changed",
        actor_user_id=user.user_id,
        project_id=project_id,
        summary=f"Recorded hypothesis review for {hypothesis_id}.",
        object_type="hypothesis",
        object_id=hypothesis_id,
        metadata={
            "decision": decision.decision,
            "decision_id": decision.decision_id,
            "review_decision_is_not_evidence": True,
        },
    )
    return {
        "review_decision": decision.model_dump(mode="json"),
        "review_decision_is_not_evidence": True,
        "status_changes_are_audited": True,
    }


@router.post("/projects/{project_id}/portfolio/stage-gates/approve")
def approve_portfolio_stage_gate(
    project_id: str,
    request: PortfolioStageGateApprovalRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    if not user.is_admin and not has_permission(
        user,
        "portfolio:approve_stage_gate",
        project_id=project_id,
        database=database,
    ):
        database.write_audit(
            "portfolio_stage_gate_approval_denied",
            actor_user_id=user.user_id,
            project_id=project_id,
            summary=f"Denied portfolio stage-gate approval for {request.candidate_id}.",
            object_type="portfolio_stage_gate",
            object_id=request.stage_gate_id or request.candidate_id,
            metadata={"candidate_id": request.candidate_id, "decision": request.decision},
        )
        raise HTTPException(status_code=403, detail="Portfolio stage-gate approval denied.")
    reviewer_id = request.reviewer_id or user.user_id
    database.write_audit(
        "portfolio_stage_gate_approved",
        actor_user_id=user.user_id,
        project_id=project_id,
        summary=f"Approved portfolio stage gate for {request.candidate_id}.",
        object_type="portfolio_stage_gate",
        object_id=request.stage_gate_id or request.candidate_id,
        metadata={
            "candidate_id": request.candidate_id,
            "stage_gate_id": request.stage_gate_id,
            "run_id": request.run_id,
            "decision": request.decision,
            "reviewer_id": reviewer_id,
            "approval_note": request.approval_note,
            **request.metadata,
        },
    )
    return {
        "approved": True,
        "candidate_id": request.candidate_id,
        "project_id": project_id,
        "decision": request.decision,
        "reviewer_id": reviewer_id,
        "advisory_boundary": "portfolio optimization output remains advisory until approved",
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


def _hosted_hypothesis_store_path(root_dir: Any, project_id: str) -> Any:
    from pathlib import Path

    return Path(root_dir) / ".molecule-ranker" / "hypotheses" / project_id / "hypotheses.sqlite"


def _campaign_store(database: PlatformDatabase, project_id: str) -> CampaignStore:
    return CampaignStore(_hosted_campaign_store_path(database.root_dir, project_id))


def _hosted_campaign_store_path(root_dir: Any, project_id: str) -> Any:
    from pathlib import Path

    return Path(root_dir) / ".molecule-ranker" / "campaigns" / project_id / "campaigns.sqlite"
