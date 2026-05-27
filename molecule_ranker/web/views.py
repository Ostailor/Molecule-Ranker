from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette import status

from molecule_ranker.platform.auth import (
    AuthError,
    AuthTokenConfig,
    SessionTokenManager,
    generate_opaque_token,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.rbac import (
    has_permission,
    require_platform_admin,
    require_project_access,
)
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import platform_database, workspace_store
from molecule_ranker.web.components import (
    candidate_by_name,
    candidate_comment_key,
    codex_outputs,
    display_candidate_name,
    evidence_fields,
    list_admin_teams,
    load_dashboard_run,
    load_project,
    prediction_fields,
    safe_dashboard_text,
    visible_workspaces,
)
from molecule_ranker.workspace.schemas import ProjectWorkspace
from molecule_ranker.workspace.store import ProjectWorkspaceStore

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["display_candidate_name"] = display_candidate_name
templates.env.globals["candidate_comment_key"] = candidate_comment_key
templates.env.filters["safe_dashboard_text"] = safe_dashboard_text

ACCESS_COOKIE = "mr_access_token"
REFRESH_COOKIE = "mr_refresh_token"
CSRF_COOKIE = "mr_csrf_token"


def require_dashboard_user(request: Request) -> UserAccount:
    return dashboard_user(request)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return _template(request, "login.html", {"title": "Login"})


@router.post("/login")
async def login_submit(
    request: Request,
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    email = str((form.get("email") or [""])[0])
    password = str((form.get("password") or [""])[0])
    try:
        user = database.authenticate_user(email=email, password=password)
    except AuthError:
        return _template(
            request,
            "login.html",
            {"title": "Login", "error": "Invalid email or password."},
            status_code=401,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_browser_session(
        response,
        database=database,
        user=user,
        auth_secret=request.app.state.auth_secret,
        secure_cookie=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
async def logout_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    if not _csrf_token_valid(request, await request.body()):
        raise HTTPException(status_code=403, detail="CSRF token required.")
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        try:
            payload = SessionTokenManager(request.app.state.auth_secret).verify(token)
            session_id = payload.get("sid")
            if session_id:
                database.revoke_auth_session(session_id=str(session_id), actor_user_id=user.user_id)
        except AuthError:
            pass
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def project_list_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    projects = visible_workspaces(store=store, database=database, user=user)
    return _template(
        request,
        "project_list.html",
        {"title": "Projects", "user": user, "projects": projects},
    )


@router.get("/dashboard/projects", response_class=HTMLResponse)
def project_list_alias(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return project_list_page(request, user, database, store)


@router.get("/dashboard/projects/{project_id}", response_class=HTMLResponse)
def project_detail_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    return _template(
        request,
        "project_detail.html",
        {
            "title": workspace.name,
            "user": user,
            "project": workspace,
            "activity": database.list_activity(project_id=project_id, limit=10),
        },
    )


@router.get("/dashboard/projects/{project_id}/activity", response_class=HTMLResponse)
def project_activity_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    return _template(
        request,
        "project_activity.html",
        {
            "title": "Activity",
            "user": user,
            "project": workspace,
            "activity": database.list_activity(project_id=project_id, limit=100),
            "assignments": database.list_assignments(project_id=project_id, limit=100),
            "comments": database.list_project_comments(project_id=project_id, limit=100),
        },
    )


@router.get("/dashboard/projects/{project_id}/runs", response_class=HTMLResponse)
def run_list_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    return _template(
        request,
        "run_list.html",
        {"title": "Runs", "user": user, "project": workspace, "runs": workspace.runs},
    )


@router.get("/dashboard/projects/{project_id}/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "run_detail.html",
        {"title": run_id, "user": user, "project": workspace, "dashboard_run": dashboard_run},
    )


@router.get(
    "/dashboard/projects/{project_id}/runs/{run_id}/candidates",
    response_class=HTMLResponse,
)
def candidate_table_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "candidate_table.html",
        {
            "title": "Candidate ranking",
            "user": user,
            "project": workspace,
            "dashboard_run": dashboard_run,
            "candidates": dashboard_run.candidates,
        },
    )


@router.get("/dashboard/projects/{project_id}/runs/{run_id}/generated", response_class=HTMLResponse)
def generated_molecule_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "generated_table.html",
        {
            "title": "Generated molecules",
            "user": user,
            "project": workspace,
            "dashboard_run": dashboard_run,
            "molecules": dashboard_run.generated_molecules,
        },
    )


@router.get(
    "/dashboard/projects/{project_id}/runs/{run_id}/developability",
    response_class=HTMLResponse,
)
def developability_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "developability.html",
        {
            "title": "Developability",
            "user": user,
            "project": workspace,
            "dashboard_run": dashboard_run,
        },
    )


@router.get(
    "/dashboard/projects/{project_id}/runs/{run_id}/experiments",
    response_class=HTMLResponse,
)
def experimental_results_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "experiments.html",
        {
            "title": "Experimental results",
            "user": user,
            "project": workspace,
            "dashboard_run": dashboard_run,
            "predictions": [prediction_fields(item) for item in dashboard_run.candidates],
        },
    )


@router.get(
    "/dashboard/projects/{project_id}/runs/{run_id}/active-learning",
    response_class=HTMLResponse,
)
def active_learning_page(
    project_id: str,
    run_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, dashboard_run = _run_or_404(store, database, user, project_id, run_id)
    return _template(
        request,
        "active_learning.html",
        {
            "title": "Active learning",
            "user": user,
            "project": workspace,
            "dashboard_run": dashboard_run,
        },
    )


@router.get("/dashboard/projects/{project_id}/review", response_class=HTMLResponse)
def review_queue_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "review:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Review permission denied.")
    review_items = []
    for run in workspace.runs:
        dashboard_run = load_dashboard_run(workspace, run.run_id)
        if dashboard_run:
            review_items.extend(dashboard_run.candidates)
            review_items.extend(dashboard_run.generated_molecules)
    return _template(
        request,
        "review_queue.html",
        {
            "title": "Review queue",
            "user": user,
            "project": workspace,
            "items": review_items,
            "assignments": database.list_assignments(
                project_id=project_id,
                assigned_to_user_id=user.user_id,
                status="open",
            ),
        },
    )


@router.get(
    "/dashboard/projects/{project_id}/candidates/{candidate_name}",
    response_class=HTMLResponse,
)
def candidate_dossier_page(
    project_id: str,
    candidate_name: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    for run in workspace.runs:
        dashboard_run = load_dashboard_run(workspace, run.run_id)
        if dashboard_run is None:
            continue
        candidate = candidate_by_name(dashboard_run, candidate_name)
        if candidate is not None:
            comment_key = candidate_comment_key(candidate)
            return _template(
                request,
                "candidate_dossier.html",
                {
                    "title": display_candidate_name(candidate),
                    "user": user,
                    "project": workspace,
                    "dashboard_run": dashboard_run,
                    "candidate": candidate,
                    "predictions": prediction_fields(candidate),
                    "evidence": evidence_fields(candidate),
                    "comments": database.list_project_comments(
                        project_id=project_id,
                        object_type="candidate",
                        object_id=comment_key,
                    ),
                },
            )
    raise HTTPException(status_code=404, detail="Candidate not found.")


@router.get("/dashboard/projects/{project_id}/codex", response_class=HTMLResponse)
def codex_assistant_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "codex:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Codex permission denied.")
    return _template(
        request,
        "codex.html",
        {
            "title": "Codex assistant",
            "user": user,
            "project": workspace,
            "codex_outputs": codex_outputs(workspace),
            "can_run_codex": has_permission(
                user,
                "codex:run",
                project_id=project_id,
                database=database,
            ),
        },
    )


@router.get("/dashboard/audit", response_class=HTMLResponse)
def audit_log_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    if not has_permission(user, "admin:view_audit", database=database):
        raise HTTPException(status_code=403, detail="Audit permission denied.")
    return _template(
        request,
        "audit.html",
        {"title": "Audit log", "user": user, "events": database.list_audit_events(limit=100)},
    )


@router.get("/dashboard/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin.html",
        {
            "title": "Admin",
            "user": user,
            "users": database.list_users(),
            "organizations": database.list_organizations(),
            "teams": list_admin_teams(database),
        },
    )


@router.get("/dashboard/admin/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_users.html",
        {"title": "Admin users", "user": user, "users": database.list_users()},
    )


@router.get("/dashboard/admin/organizations", response_class=HTMLResponse)
def admin_organizations_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_organizations.html",
        {
            "title": "Admin organizations",
            "user": user,
            "organizations": database.list_organizations(),
        },
    )


@router.get("/dashboard/admin/teams", response_class=HTMLResponse)
def admin_teams_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_teams.html",
        {"title": "Admin teams", "user": user, "teams": list_admin_teams(database)},
    )


@router.get("/dashboard/admin/memberships", response_class=HTMLResponse)
def admin_memberships_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_memberships.html",
        {"title": "Admin memberships", "user": user, "memberships": database.list_memberships()},
    )


@router.get("/dashboard/admin/service-accounts", response_class=HTMLResponse)
def admin_service_accounts_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_service_accounts.html",
        {
            "title": "Admin service accounts",
            "user": user,
            "service_accounts": database.list_service_account_tokens(),
        },
    )


@router.get("/dashboard/admin/audit", response_class=HTMLResponse)
def admin_audit_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_audit.html",
        {"title": "Admin audit", "user": user, "events": database.list_audit_events(limit=100)},
    )


@router.get("/dashboard/admin/jobs", response_class=HTMLResponse)
def admin_jobs_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_jobs.html",
        {
            "title": "Admin job queue",
            "user": user,
            "jobs": PlatformJobQueue(database).list_jobs(limit=100),
            "failed_jobs": database.list_failed_jobs(),
        },
    )


@router.get("/dashboard/admin/health", response_class=HTMLResponse)
def admin_health_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_health.html",
        {"title": "System health", "user": user, "health": database.health()},
    )


@router.get("/dashboard/admin/codex-worker", response_class=HTMLResponse)
def admin_codex_worker_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_codex_worker.html",
        {
            "title": "Codex worker status",
            "user": user,
            "codex_status": database.codex_worker_status(),
        },
    )


@router.get("/dashboard/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    return _template(
        request,
        "notifications.html",
        {
            "title": "Notifications",
            "user": user,
            "notifications": database.list_notifications(user_id=user.user_id, limit=100),
        },
    )


def dashboard_user(request: Request) -> UserAccount:
    database = request.app.state.platform_database
    if database is None:
        return UserAccount(
            user_id="local-user",
            email="local@molecule-ranker.internal",
            display_name="Local user",
            is_active=True,
            is_admin=True,
            auth_provider="service_account",
            metadata={"permissions": ["*"]},
        )
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        authorization = request.headers.get("Authorization", "")
        token = (
            authorization.removeprefix("Bearer ").strip()
            if authorization.startswith("Bearer ")
            else ""
        )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Login required.",
            headers={"Location": "/login"},
        )
    try:
        payload = SessionTokenManager(request.app.state.auth_secret).verify(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail=str(exc),
            headers={"Location": "/login"},
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Access token required.")
    session_id = payload.get("sid")
    if session_id and not database.auth_session_active(str(session_id)):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Session expired.",
            headers={"Location": "/login"},
        )
    user = database.get_user(str(payload["user_id"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User is not active.")
    return user


def _run_or_404(
    store: ProjectWorkspaceStore,
    database: PlatformDatabase,
    user: UserAccount,
    project_id: str,
    run_id: str,
) -> tuple[ProjectWorkspace, Any]:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    dashboard_run = load_dashboard_run(workspace, run_id)
    if dashboard_run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return workspace, dashboard_run


def _project_or_404(*, store: ProjectWorkspaceStore, project_id: str) -> ProjectWorkspace:
    workspace = load_project(store=store, project_id=project_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return workspace


def _template(
    request: Request,
    name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> Response:
    if CSRF_COOKIE in request.cookies and "csrf_token" not in context:
        context = {**context, "csrf_token": request.cookies[CSRF_COOKIE]}
    return templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
    )


def _set_browser_session(
    response: Response,
    *,
    database: PlatformDatabase,
    user: UserAccount,
    auth_secret: str,
    secure_cookie: bool,
) -> None:
    config = AuthTokenConfig()
    refresh_token = generate_opaque_token(prefix="mrr")
    session_id = database.create_auth_session(
        user_id=user.user_id,
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(seconds=config.refresh_token_ttl_seconds),
        metadata={"flow": "browser_cookie"},
    )
    access_token = SessionTokenManager(auth_secret).issue(
        user_id=user.user_id,
        roles=user.roles,
        token_type="access",
        session_id=session_id,
        ttl_seconds=config.access_token_ttl_seconds,
    )
    cookie_settings = {
        "httponly": True,
        "samesite": "lax",
        "secure": secure_cookie,
        "path": "/",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=config.access_token_ttl_seconds,
        **cookie_settings,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=config.refresh_token_ttl_seconds,
        **cookie_settings,
    )
    response.set_cookie(
        CSRF_COOKIE,
        generate_opaque_token(prefix="mrc"),
        max_age=config.refresh_token_ttl_seconds,
        httponly=False,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )


def _csrf_token_valid(request: Request, body: bytes) -> bool:
    expected = request.cookies.get(CSRF_COOKIE)
    if not expected:
        return False
    provided = request.headers.get("X-CSRF-Token", "")
    if not provided:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        provided = str((form.get("csrf_token") or [""])[0])
    return bool(provided) and provided == expected
