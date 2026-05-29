from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette import status

from molecule_ranker.integrations.connectors import create_connector
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.models.registry import ModelRegistry
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
from molecule_ranker.server.security import reject_suspicious_identifier, safe_artifact_path
from molecule_ranker.web.components import (
    candidate_by_name,
    candidate_comment_key,
    codex_outputs,
    dashboard_design_runs,
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


@router.post("/dashboard/projects/create")
async def project_create_submit(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    body = await request.body()
    if not _csrf_token_valid(request, body):
        raise HTTPException(status_code=403, detail="CSRF token required.")
    form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    workspace_id = str((form.get("workspace_id") or [""])[0]).strip() or None
    name = str((form.get("name") or [""])[0]).strip() or None
    if store.workspace_path.exists():
        existing = store.load()
        require_project_access(database, user, project_id=existing.workspace_id, action="admin")
    workspace = store.create(workspace_id=workspace_id, name=name)
    database.grant_project_permission(
        project_id=workspace.workspace_id,
        role="owner",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    database.write_audit(
        "dashboard_project_created",
        actor_user_id=user.user_id,
        project_id=workspace.workspace_id,
        summary=f"Created project {workspace.workspace_id} from hosted dashboard.",
        object_type="project",
        object_id=workspace.workspace_id,
    )
    return RedirectResponse(
        f"/dashboard/projects/{workspace.workspace_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard/integrations", response_class=HTMLResponse)
def integrations_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    store = IntegrationStore(database, user=user)
    systems = store.list_external_systems()
    connectors = database.list_integration_connectors()
    return _integration_html(
        "Integrations",
        _nav()
        + _table(
            ["ID", "Name", "Type", "Vendor", "Mode", "Enabled"],
            [
                [
                    _link(
                        f"/dashboard/integrations/{system.external_system_id}",
                        system.external_system_id,
                    ),
                    system.name,
                    system.system_type,
                    system.vendor or "",
                    _mode_badge(system.default_mode),
                    system.enabled,
                ]
                for system in systems
            ]
            + [
                [
                    _link(
                        f"/dashboard/integrations/{connector.connector_id}",
                        connector.connector_id,
                    ),
                    connector.name,
                    connector.kind,
                    connector.provider,
                    _mode_badge(connector.mode),
                    True,
                ]
                for connector in connectors
                if connector.connector_id not in {system.external_system_id for system in systems}
            ],
        ),
    )


@router.get("/dashboard/integrations/credentials", response_class=HTMLResponse)
def integration_credentials_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:manage_credentials")
    credentials = IntegrationStore(database, user=user).list_credential_references()
    return _integration_html(
        "Integration credentials",
        _nav()
        + _table(
            ["ID", "System", "Type", "Secret reference", "Status"],
            [
                [
                    credential.credential_id,
                    credential.external_system_id,
                    credential.credential_type,
                    _redacted_secret_ref(credential.secret_ref),
                    credential.metadata.get("status") or "active",
                ]
                for credential in credentials
            ],
        ),
    )


@router.get("/dashboard/integrations/sync-jobs", response_class=HTMLResponse)
def integration_sync_jobs_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    jobs = IntegrationStore(database, user=user).list_sync_jobs(limit=100)
    return _integration_html(
        "Integration sync jobs",
        _nav()
        + _table(
            ["ID", "System", "Direction", "Mode", "Status", "Seen", "Failed"],
            [
                [
                    _link(f"/dashboard/integrations/sync-jobs/{job.sync_job_id}", job.sync_job_id),
                    job.external_system_id,
                    job.direction,
                    job.mode,
                    job.status,
                    job.records_seen,
                    job.records_failed,
                ]
                for job in jobs
            ],
        ),
    )


@router.get("/dashboard/integrations/sync-jobs/{sync_job_id}", response_class=HTMLResponse)
def integration_sync_job_detail_page(
    sync_job_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    store = IntegrationStore(database, user=user)
    job = store.get_sync_job(sync_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sync job not found.")
    records = store.list_sync_records(sync_job_id=sync_job_id, limit=500)
    return _integration_html(
        f"Sync job {sync_job_id}",
        _nav()
        + _definition_list(
            {
                "System": job.external_system_id,
                "Direction": job.direction,
                "Mode": job.mode,
                "Status": job.status,
                "Records seen": job.records_seen,
                "Errors": job.error_summary or "",
            }
        )
        + "<h2>Sync record detail</h2>"
        + _table(
            ["Record", "External ID", "Action", "Status", "Artifact"],
            [
                [
                    record.sync_record_id,
                    record.external_ref.external_record_id,
                    record.action,
                    record.status,
                    record.raw_payload_artifact_id or "",
                ]
                for record in records
            ],
        ),
    )


@router.get("/dashboard/integrations/mappings", response_class=HTMLResponse)
def integration_mapping_queue_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    mappings = IntegrationStore(database, user=user).find_mappings(status="pending_review")
    return _integration_html(
        "Mapping review queue",
        _nav()
        + _table(
            ["ID", "Internal", "External", "Method", "Confidence", "Status"],
            [
                [
                    mapping.mapping_id,
                    f"{mapping.internal_entity_type}:{mapping.internal_entity_id}",
                    mapping.external_ref.external_record_id,
                    mapping.mapping_method,
                    mapping.mapping_confidence,
                    mapping.status,
                ]
                for mapping in mappings
            ],
        ),
    )


@router.get("/dashboard/integrations/webhooks", response_class=HTMLResponse)
def integration_webhook_events_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    events = IntegrationStore(database, user=user).list_webhook_events(limit=100)
    return _integration_html(
        "Webhook events",
        _nav()
        + _table(
            ["ID", "System", "Type", "Status", "Payload artifact"],
            [
                [
                    event.get("webhook_event_id"),
                    event.get("external_system_id"),
                    event.get("event_type"),
                    event.get("status"),
                    event.get("payload_artifact_id"),
                ]
                for event in events
            ],
        ),
    )


@router.get("/dashboard/integrations/data-contracts", response_class=HTMLResponse)
def integration_data_contracts_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    contracts = IntegrationStore(database, user=user).list_data_contracts()
    return _integration_html(
        "Data contracts",
        _nav()
        + _table(
            ["ID", "Name", "Object type", "Version", "Required fields"],
            [
                [
                    contract.contract_id,
                    contract.name,
                    contract.object_type,
                    contract.version,
                    ", ".join(contract.required_fields),
                ]
                for contract in contracts
            ],
        ),
    )


@router.get("/dashboard/integrations/{external_system_id}", response_class=HTMLResponse)
def integration_detail_page(
    external_system_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_integration_dashboard_permission(database, user, "integration:read")
    store = IntegrationStore(database, user=user)
    system = store.get_external_system(external_system_id)
    connector = database.get_integration_connector(external_system_id)
    if system is None and connector is None:
        raise HTTPException(status_code=404, detail="Integration not found.")
    health_payload: dict[str, Any]
    if connector is not None:
        health_payload = create_connector(connector).health_check().model_dump(mode="json")
    else:
        health_payload = {"status": "unconfigured", "message": "No connector configured."}
    jobs = store.list_sync_jobs(external_system_id=external_system_id, limit=25)
    records = store.list_sync_records(external_system_id=external_system_id, limit=25)
    title = system.name if system is not None else str(connector.name if connector else "")
    return _integration_html(
        f"Integration {title}",
        _nav()
        + _definition_list(
            {
                "External system ID": external_system_id,
                "Name": title,
                "Health": health_payload.get("status"),
                "Health message": health_payload.get("message"),
            }
        )
        + "<h2>Health check results</h2>"
        + f"<pre>{_h(health_payload)}</pre>"
        + "<h2>Sync job history</h2>"
        + _table(
            ["ID", "Direction", "Status", "Seen"],
            [
                [
                    _link(f"/dashboard/integrations/sync-jobs/{job.sync_job_id}", job.sync_job_id),
                    job.direction,
                    job.status,
                    job.records_seen,
                ]
                for job in jobs
            ],
        )
        + "<h2>Recent sync records</h2>"
        + _table(
            ["ID", "External ID", "Action", "Status"],
            [
                [
                    record.sync_record_id,
                    record.external_ref.external_record_id,
                    record.action,
                    record.status,
                ]
                for record in records
            ],
        ),
    )


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
            "model_artifacts": [],
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


@router.get("/dashboard/projects/{project_id}/design/plans", response_class=HTMLResponse)
def design_plans_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Design plans",
        section="plans",
    )


@router.get("/dashboard/projects/{project_id}/design/generator-runs", response_class=HTMLResponse)
def design_generator_runs_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Generator ensemble runs",
        section="generator_runs",
    )


@router.get("/dashboard/projects/{project_id}/design/oracle-scores", response_class=HTMLResponse)
def design_oracle_scores_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Oracle scores",
        section="oracle_scores",
    )


@router.get("/dashboard/projects/{project_id}/design/readiness", response_class=HTMLResponse)
def design_readiness_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Experiment-readiness queue",
        section="readiness",
    )


@router.get("/dashboard/projects/{project_id}/design/benchmarks", response_class=HTMLResponse)
def design_benchmarks_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Design benchmark reports",
        section="benchmarks",
    )


@router.get("/dashboard/projects/{project_id}/design/active-loop", response_class=HTMLResponse)
def design_active_loop_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _design_page(
        project_id,
        request,
        user,
        database,
        store,
        title="Active design loop history",
        section="active_loop",
    )


@router.get("/dashboard/projects/{project_id}/models", response_class=HTMLResponse)
def model_registry_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    cards = registry.list_models(active_only=False)
    body = (
        _model_nav(project_id)
        + _model_boundary_notice()
        + _table(
            [
                "Model",
                "Endpoint",
                "Plugin",
                "Calibration status",
                "Applicability domain",
                "Status",
            ],
            [
                [
                    _link(
                        f"/dashboard/projects/{project_id}/models/{quote(card.model_id)}",
                        card.model_name,
                    ),
                    card.endpoint.endpoint_name,
                    card.plugin_name,
                    _model_calibration_status(card.calibration_metrics),
                    card.applicability_domain_method,
                    "active" if card.metadata.get("registry_active", True) else "inactive",
                ]
                for card in cards
            ],
        )
    )
    if not cards:
        body += "<p>No model cards registered.</p>"
    return _model_dashboard_html("Model registry", workspace, body)


@router.get("/dashboard/projects/{project_id}/models/training-runs", response_class=HTMLResponse)
def model_training_runs_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    runs = _model_json_artifacts(registry, "training_runs")
    body = _model_nav(project_id) + _model_boundary_notice() + _table(
        ["Training run", "Model", "Dataset", "Status", "Warnings"],
        [
            [
                item.get("training_run_id"),
                item.get("model_id"),
                item.get("dataset_id"),
                item.get("status"),
                ", ".join(str(value) for value in item.get("warnings", [])),
            ]
            for item in runs
        ],
    )
    if not runs:
        body += "<p>No training runs registered.</p>"
    return _model_dashboard_html("Training runs", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/models/evaluation-reports",
    response_class=HTMLResponse,
)
def model_evaluation_reports_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    reports = _model_json_artifacts(registry, "evaluation_reports")
    body = _model_nav(project_id) + _model_boundary_notice() + _table(
        ["Evaluation", "Model", "Dataset", "Split strategy", "Leakage checks", "Warnings"],
        [
            [
                item.get("evaluation_id"),
                item.get("model_id"),
                item.get("dataset_id"),
                item.get("split_strategy"),
                _compact_json(item.get("leakage_checks") or {}),
                ", ".join(str(value) for value in item.get("warnings", [])),
            ]
            for item in reports
        ],
    )
    if not reports:
        body += "<p>No evaluation reports registered.</p>"
    return _model_dashboard_html("Evaluation reports", workspace, body)


@router.get("/dashboard/projects/{project_id}/models/calibration", response_class=HTMLResponse)
def model_calibration_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    cards = registry.list_models(active_only=False)
    reports = _model_json_artifacts(registry, "evaluation_reports")
    body = _model_nav(project_id) + _model_boundary_notice() + _table(
        ["Model", "Endpoint", "Calibration status", "Calibration metrics"],
        [
            [
                _link(
                    f"/dashboard/projects/{project_id}/models/{quote(card.model_id)}",
                    card.model_name,
                ),
                card.endpoint.endpoint_name,
                _model_calibration_status(card.calibration_metrics),
                _compact_json(card.calibration_metrics),
            ]
            for card in cards
        ],
    )
    body += "<h2>Calibration plots/summary</h2>"
    body += _table(
        ["Evaluation", "Model", "Calibration metrics"],
        [
            [
                item.get("evaluation_id"),
                item.get("model_id"),
                _compact_json(item.get("calibration_metrics") or {}),
            ]
            for item in reports
        ],
    )
    if any(_model_calibration_status(card.calibration_metrics) != "calibrated" for card in cards):
        body += (
            "<p class=\"warning\"><strong>Uncalibrated warning shown:</strong> "
            "uncalibrated predictions must be treated as prioritization hints only.</p>"
        )
    return _model_dashboard_html("Calibration summary", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/models/prediction-batches",
    response_class=HTMLResponse,
)
def model_prediction_batches_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    batches = _model_json_artifacts(registry, "prediction_batches")
    predictions = _model_prediction_records(registry)
    body = (
        _model_nav(project_id)
        + _model_boundary_notice()
        + _prediction_warnings(predictions)
        + _table(
            ["Batch", "Model", "Prediction count", "Created", "Metadata"],
            [
                [
                    item.get("batch_id"),
                    item.get("model_id"),
                    len(item.get("predictions") or []),
                    item.get("created_at"),
                    _compact_json(item.get("metadata") or {}),
                ]
                for item in batches
            ],
        )
        + "<h2>Prediction artifact contents</h2>"
        + _table(
            [
                "Batch",
                "Candidate",
                "Model",
                "Endpoint",
                "Prediction label",
                "Probability",
                "Uncertainty",
                "Confidence",
                "Applicability domain",
                "Calibration status",
                "Warnings",
            ],
            [
                [
                    item.get("batch_id"),
                    _link(
                        f"/dashboard/projects/{project_id}/models/predictions/"
                        f"{quote(_prediction_candidate_key(item))}",
                        str(item.get("candidate_name") or item.get("candidate_id") or "unknown"),
                    ),
                    item.get("model_id"),
                    item.get("endpoint_id"),
                    _prediction_label(item),
                    item.get("predicted_probability"),
                    item.get("uncertainty"),
                    item.get("confidence"),
                    item.get("applicability_domain"),
                    item.get("calibration_status"),
                    ", ".join(str(value) for value in item.get("warnings", [])),
                ]
                for item in predictions
            ],
        )
    )
    if not batches:
        body += "<p>No prediction batches registered.</p>"
    if batches and not predictions:
        body += "<p>No prediction artifacts found in registered batches.</p>"
    return _model_dashboard_html("Prediction batches", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/models/predictions/{candidate_name}",
    response_class=HTMLResponse,
)
def model_prediction_detail_page(
    project_id: str,
    candidate_name: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    predictions = [
        item
        for item in _model_prediction_records(registry)
        if str(item.get("candidate_name") or item.get("candidate_id") or "") == candidate_name
    ]
    body = (
        _model_nav(project_id)
        + _model_boundary_notice()
        + _prediction_warnings(predictions)
        + _table(
            [
                "Candidate",
                "Model",
                "Endpoint",
                "Prediction label",
                "Probability",
                "Uncertainty",
                "Confidence",
                "Applicability domain",
                "Calibration status",
                "Warnings",
            ],
            [
                [
                    item.get("candidate_name"),
                    item.get("model_id"),
                    item.get("endpoint_id"),
                    _prediction_label(item),
                    item.get("predicted_probability"),
                    item.get("uncertainty"),
                    item.get("confidence"),
                    item.get("applicability_domain"),
                    item.get("calibration_status"),
                    ", ".join(str(value) for value in item.get("warnings", [])),
                ]
                for item in predictions
            ],
        )
    )
    if not predictions:
        body += "<p>No prediction artifacts found for this candidate.</p>"
    return _model_dashboard_html("Prediction detail for candidate", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/models/active-design-influence",
    response_class=HTMLResponse,
)
def model_active_design_influence_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    design_runs = dashboard_design_runs(workspace)
    rows: list[list[Any]] = []
    for dashboard_run in design_runs:
        active_learning = dashboard_run.active_learning
        rows.append(
            [
                dashboard_run.run.run_id,
                _compact_json(active_learning.get("model_influence") or {}),
                _compact_json(active_learning.get("model_uncertainty") or {}),
                _compact_json(active_learning.get("suggestions") or []),
            ]
        )
    body = (
        _model_nav(project_id)
        + _model_boundary_notice()
        + "<p>Model influence in active design is tracked as a prioritization rationale, "
        "not experimental evidence. Calibrated surrogate oracle signals may contribute only "
        "bounded scoring modifiers, and model uncertainty remains auditable for each run.</p>"
        + _table(["Run", "Model influence", "Model uncertainty", "Suggestions"], rows)
    )
    if not rows:
        body += "<p>No active design model influence records found.</p>"
    return _model_dashboard_html("Model influence in active design", workspace, body)


@router.get("/dashboard/projects/{project_id}/models/{model_id}", response_class=HTMLResponse)
def model_card_detail_page(
    project_id: str,
    model_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _model_workspace(database, user, store, project_id)
    registry = _model_registry(database)
    try:
        card = registry.get_model_card(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model card not found.") from exc
    body = (
        _model_nav(project_id)
        + _model_boundary_notice()
        + _definition_list(
            {
                "Model ID": card.model_id,
                "Model name": card.model_name,
                "Version": card.model_version,
                "Plugin": card.plugin_name,
                "Endpoint": card.endpoint.endpoint_name,
                "Target": card.endpoint.target_symbol or "",
                "Disease": card.endpoint.disease_name or "",
                "Calibration status": _model_calibration_status(card.calibration_metrics),
                "Intended use": card.intended_use,
                "Applicability domain": card.applicability_domain_method,
            }
        )
        + "<h2>Limitations</h2>"
        + "<ul>"
        + "".join(f"<li>{_h(value)}</li>" for value in card.limitations)
        + "</ul>"
        + "<h2>Metrics</h2>"
        + f"<pre>{_h(_compact_json(card.metrics))}</pre>"
        + "<h2>Calibration metrics</h2>"
        + f"<pre>{_h(_compact_json(card.calibration_metrics))}</pre>"
    )
    if _model_calibration_status(card.calibration_metrics) != "calibrated":
        body += (
            "<p class=\"warning\"><strong>Uncalibrated warning shown:</strong> "
            "do not display predictions from this model as active without imported result "
            "evidence.</p>"
        )
    return _model_dashboard_html("Model card detail", workspace, body)


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


@router.get(
    "/dashboard/projects/{project_id}/artifacts/{artifact_id}/download",
    response_class=FileResponse,
)
def dashboard_artifact_download(
    project_id: str,
    artifact_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> FileResponse:
    reject_suspicious_identifier(artifact_id, label="artifact ID")
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    artifact = next((item for item in workspace.artifacts if item.artifact_id == artifact_id), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    path = safe_artifact_path(Path(artifact.path), root_dir=store.root_dir)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
        headers={"X-Artifact-ID": artifact.artifact_id},
    )


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
            "codex_enabled": bool(request.app.state.enable_codex_backbone),
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


def _design_page(
    project_id: str,
    request: Request,
    user: UserAccount,
    database: PlatformDatabase,
    store: ProjectWorkspaceStore,
    *,
    title: str,
    section: str,
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "design:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Design permission denied.")
    return _template(
        request,
        "design.html",
        {
            "title": title,
            "user": user,
            "project": workspace,
            "section": section,
            "design_runs": dashboard_design_runs(workspace),
            "design_jobs": PlatformJobQueue(database).list_jobs(project_id=project_id, limit=100),
        },
    )


def _model_workspace(
    database: PlatformDatabase,
    user: UserAccount,
    store: ProjectWorkspaceStore,
    project_id: str,
) -> ProjectWorkspace:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "model:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Model permission denied.")
    return workspace


def _model_registry(database: PlatformDatabase) -> ModelRegistry:
    root = database.root_dir / ".molecule-ranker" / "models"
    return ModelRegistry(
        db_path=root / "model_registry.sqlite",
        artifact_dir=root / "registry_artifacts",
    )


def _model_json_artifacts(registry: ModelRegistry, folder: str) -> list[dict[str, Any]]:
    artifact_dir = (registry.artifact_dir / folder).resolve()
    registry_root = registry.artifact_dir.resolve()
    if not artifact_dir.exists() or registry_root not in artifact_dir.parents:
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(artifact_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _model_prediction_records(registry: ModelRegistry) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for batch in _model_json_artifacts(registry, "prediction_batches"):
        batch_id = batch.get("batch_id")
        for prediction in batch.get("predictions") or []:
            if isinstance(prediction, dict):
                records.append(
                    {
                        "batch_id": batch_id,
                        "batch_model_id": batch.get("model_id"),
                        **prediction,
                    }
                )
    return records


def _model_dashboard_html(title: str, workspace: ProjectWorkspace, body: str) -> HTMLResponse:
    project_id = workspace.workspace_id
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=1.2.0-model-ui\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V1.2</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/models', 'Models')}"
        f"{_link(f'/dashboard/projects/{project_id}/runs', 'Runs')}"
        f"{_link(f'/dashboard/projects/{project_id}/design/plans', 'Design')}"
        "</nav></div></header><main class=\"content\">"
        "<aside class=\"research-disclaimer\">Internal research use only. Source-backed "
        "evidence remains authoritative. Model predictions are computational prioritization "
        "artifacts and are not assay results.</aside>"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(project_id)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _model_nav(project_id: str) -> str:
    links = {
        "Model registry": f"/dashboard/projects/{project_id}/models",
        "Training runs": f"/dashboard/projects/{project_id}/models/training-runs",
        "Evaluation reports": f"/dashboard/projects/{project_id}/models/evaluation-reports",
        "Calibration": f"/dashboard/projects/{project_id}/models/calibration",
        "Prediction batches": f"/dashboard/projects/{project_id}/models/prediction-batches",
        "Active design influence": (
            f"/dashboard/projects/{project_id}/models/active-design-influence"
        ),
    }
    return "<nav class=\"section\">" + " ".join(
        _link(href, label) for label, href in links.items()
    ) + "</nav>"


def _model_boundary_notice() -> str:
    return (
        "<p class=\"notice\"><strong>Prediction artifacts are separate. Model predictions are "
        "predictions, not experimental evidence and not assay results.</strong> Generated "
        "molecules still require exact imported experimental results before any direct evidence "
        "is shown. No prediction is displayed as active without imported result evidence.</p>"
    )


def _model_calibration_status(metrics: dict[str, Any]) -> str:
    return str(metrics.get("calibration_status") or metrics.get("status") or "unknown")


def _prediction_warnings(predictions: list[dict[str, Any]]) -> str:
    if not predictions:
        return ""
    warnings: list[str] = []
    if any(str(item.get("calibration_status")) != "calibrated" for item in predictions):
        warnings.append("Uncalibrated prediction warning shown")
    if any(str(item.get("applicability_domain")) == "out_of_domain" for item in predictions):
        warnings.append("Out-of-domain prediction warning shown")
    if not warnings:
        return ""
    return (
        "<p class=\"warning\"><strong>"
        + _h("; ".join(warnings))
        + ":</strong> these predictions must remain visually separate from evidence.</p>"
    )


def _prediction_label(prediction: dict[str, Any]) -> str:
    label = str(prediction.get("prediction_label") or prediction.get("predicted_value") or "")
    if label.lower() in {"active", "predicted_active", "surrogate_active"}:
        return "model-favorable prediction only; imported result evidence required"
    if label:
        return f"{label} (prediction only)"
    return "prediction only"


def _prediction_candidate_key(prediction: dict[str, Any]) -> str:
    return str(prediction.get("candidate_name") or prediction.get("candidate_id") or "")


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ": "))


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


def _require_integration_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    if user.is_admin:
        return
    if not has_permission(user, permission, database=database):
        raise HTTPException(status_code=403, detail="Integration permission denied.")


def _integration_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)}</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/integrations.css\">"
        "</head><body>"
        f"<header class=\"integration-header\"><h1>{_h(title)}</h1></header>"
        "<main class=\"integration-content\">"
        "<p class=\"notice\"><strong>Dry-run/read-only by default.</strong> "
        "External integrations do not write externally unless a connector is explicitly "
        "write-enabled. <strong>Write-enabled requires explicit permission</strong>, approved "
        "credentials, and operator review. Secrets are redacted.</p>"
        f"{body}</main></body></html>\n"
    )


def _nav() -> str:
    links = {
        "Integrations": "/dashboard/integrations",
        "Credentials": "/dashboard/integrations/credentials",
        "Sync jobs": "/dashboard/integrations/sync-jobs",
        "Mappings": "/dashboard/integrations/mappings",
        "Webhooks": "/dashboard/integrations/webhooks",
        "Data contracts": "/dashboard/integrations/data-contracts",
    }
    return "<nav>" + " ".join(_link(href, label) for label, href in links.items()) + "</nav>"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_h(header)}</th>" for header in headers)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{cell if _is_html(cell) else _h(cell)}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return (
        "<div class=\"table-scroll\">"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        "</div>"
    )


def _definition_list(values: dict[str, Any]) -> str:
    rows = "".join(f"<dt>{_h(key)}</dt><dd>{_h(value)}</dd>" for key, value in values.items())
    return f"<dl>{rows}</dl>"


def _link(href: str, label: Any) -> str:
    return f"<a href=\"{_h(href)}\">{_h(label)}</a>"


def _is_html(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("<a ") or value.startswith("<span "))


def _mode_badge(mode: Any) -> str:
    normalized = str(mode or "dry_run")
    label = normalized.replace("_", " ")
    css = normalized.replace("_", "-")
    return f"<span class=\"mode-badge {css}\">{_h(label)}</span>"


def _h(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _redacted_secret_ref(secret_ref: str) -> str:
    if ":" not in secret_ref:
        return "[redacted]"
    prefix, reference = secret_ref.split(":", 1)
    visible = reference[-4:] if len(reference) > 4 else "ref"
    return f"{prefix}:...{visible}"


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
