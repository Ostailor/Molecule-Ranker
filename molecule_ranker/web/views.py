from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette import status

from molecule_ranker.campaigns import CampaignPlan, CampaignStore
from molecule_ranker.integrations.connectors import create_connector
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.knowledge_graph import (
    GraphEntity,
    KnowledgeGraph,
    KnowledgeGraphStore,
    analyze_cross_program_knowledge,
    build_contradiction_report,
    build_staleness_report,
    generate_graph_recommendations,
)
from molecule_ranker.knowledge_graph.reasoning import GraphReasoner
from molecule_ranker.models.registry import ModelRegistry
from molecule_ranker.platform.auth import (
    AuthError,
    AuthTokenConfig,
    SessionTokenManager,
    generate_opaque_token,
)
from molecule_ranker.platform.database import artifact_records, project_permissions
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.observability import redact_for_log
from molecule_ranker.platform.rbac import (
    ORG_ROLE_PERMISSIONS,
    PROJECT_ROLE_PERMISSIONS,
    has_permission,
    require_platform_admin,
    require_project_access,
)
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.runtime_agents.hosted import RuntimeAgentHostedStore
from molecule_ranker.server.dependencies import platform_database, workspace_store
from molecule_ranker.server.security import reject_suspicious_identifier, safe_artifact_path
from molecule_ranker.utils.json_io import JsonArtifactTooLargeError, load_json_file
from molecule_ranker.utils.pagination import normalize_limit_offset
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


@router.get("/dashboard/agent/sessions", response_class=HTMLResponse)
def agent_sessions_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _agent_store(request)
    sessions = [
        session
        for session in store.list_sessions()
        if _can_view_agent_session(database, user, session.project_id)
    ]
    rows = [
        [
            _link(f"/dashboard/agent/sessions/{quote(session.session_id)}", session.session_id),
            session.project_id or "",
            session.autonomy_level,
            session.status,
            session.user_goal,
        ]
        for session in sessions
    ]
    return _agent_dashboard_html(
        "Agent sessions",
        _agent_nav()
        + _table(["Session", "Project", "Autonomy", "Status", "Goal"], rows),
    )


@router.get("/dashboard/agent/sessions/{session_id}", response_class=HTMLResponse)
def agent_session_detail_dashboard_page(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _agent_store(request)
    session = _agent_session_or_404(store, session_id)
    if not _can_view_agent_session(database, user, session.project_id):
        raise HTTPException(status_code=403, detail="Agent permission denied.")
    try:
        plan = store.get_plan(session_id)
        step_rows = [
            [step.step_index + 1, step.tool_name, step.status, step.approval_reason or ""]
            for step in plan.steps
        ]
        plan_summary = plan.plan_summary
    except KeyError:
        step_rows = []
        plan_summary = "No action plan has been created."
    approvals = store.list_approvals(session_id)
    audit_events = store.list_audit_events(session_id)
    guardrails = store.get_guardrail_report(session_id)
    results = store.list_tool_results(session_id)
    body = (
        _agent_nav()
        + f"<h2>Agent session detail</h2><p>{escape(session.user_goal)}</p>"
        + f"<h2>Action plan view</h2><p>{escape(plan_summary)}</p>"
        + "<h2>Step execution view</h2>"
        + _table(["Index", "Tool", "Status", "Approval"], step_rows)
        + "<h2>Approval queue</h2>"
        + _table(
            ["Approval", "Type", "Status", "Reason"],
            [
                [
                    approval.approval_id,
                    approval.approval_type,
                    approval.status,
                    approval.reason,
                ]
                for approval in approvals
            ],
        )
        + "<h2>Runtime audit log</h2>"
        + _table(
            ["Event", "Actor", "Summary"],
            [[event.event_type, event.actor or "", event.summary] for event in audit_events],
        )
        + "<h2>Guardrail report</h2>"
        + f"<pre>{escape(json.dumps(guardrails, indent=2, sort_keys=True))}</pre>"
        + "<h2>Produced artifacts</h2>"
        + _table(
            ["Result", "Artifacts", "Jobs"],
            [
                [
                    result.result_id,
                    ", ".join(result.artifact_ids),
                    ", ".join(result.job_ids),
                ]
                for result in results
            ],
        )
        + "<h2>Next actions</h2><ul><li>Review produced artifacts.</li>"
        + "<li>Resolve pending approvals.</li><li>Inspect guardrail warnings.</li></ul>"
    )
    return _agent_dashboard_html("Agent session detail", body)


@router.get("/dashboard/agent/approvals", response_class=HTMLResponse)
def agent_approvals_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _agent_store(request)
    approvals = [
        approval
        for approval in store.list_approvals()
        if _can_view_agent_session(
            database,
            user,
            _agent_session_project(store, approval.session_id),
        )
    ]
    return _agent_dashboard_html(
        "Approval queue",
        _agent_nav()
        + _table(
            ["Approval", "Session", "Type", "Status", "Reason"],
            [
                [
                    approval.approval_id,
                    _link(
                        f"/dashboard/agent/sessions/{quote(approval.session_id)}",
                        approval.session_id,
                    ),
                    approval.approval_type,
                    approval.status,
                    approval.reason,
                ]
                for approval in approvals
            ],
        ),
    )


@router.get("/dashboard/agent/audit", response_class=HTMLResponse)
def agent_audit_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _agent_store(request)
    events = [
        event
        for event in store.list_audit_events()
        if _can_view_agent_session(database, user, _agent_session_project(store, event.session_id))
    ]
    return _agent_dashboard_html(
        "Runtime audit log",
        _agent_nav()
        + _table(
            ["Session", "Event", "Actor", "Summary"],
            [
                [
                    event.session_id,
                    event.event_type,
                    event.actor or "",
                    event.summary,
                ]
                for event in events
            ],
        ),
    )


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
    database.register_project_workspace(
        project_id=workspace.workspace_id,
        name=workspace.name,
        root_dir=str(store.root_dir),
        actor_user_id=user.user_id,
    )
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


@router.get("/dashboard/projects/{project_id}/evaluation", response_class=HTMLResponse)
def evaluation_overview_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    return _evaluation_dashboard_html(
        request,
        user=user,
        workspace=workspace,
        database=database,
        section="overview",
    )


@router.get("/dashboard/projects/{project_id}/evaluation/{section}", response_class=HTMLResponse)
def evaluation_section_page(
    project_id: str,
    section: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    if section == "prospective-validation-runs" and not (
        user.is_admin
        or has_permission(user, "evaluation:admin", project_id=project_id, database=database)
    ):
        raise HTTPException(
            status_code=403,
            detail="Only authorized users can view imported outcomes.",
        )
    return _evaluation_dashboard_html(
        request,
        user=user,
        workspace=workspace,
        database=database,
        section=section,
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


@router.get("/dashboard/projects/{project_id}/structure/{section}", response_class=HTMLResponse)
def structure_dashboard_page(
    project_id: str,
    section: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    return _structure_page(project_id, section, user, database, store)


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


@router.get("/dashboard/projects/{project_id}/portfolio", response_class=HTMLResponse)
def portfolio_overview_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    _require_portfolio_read(database, user, project_id=project_id)
    return _portfolio_dashboard_html(
        "Program overview",
        workspace,
        _portfolio_section_body(workspace=workspace, database=database, section="overview"),
    )


@router.get("/dashboard/projects/{project_id}/portfolio/{section}", response_class=HTMLResponse)
def portfolio_section_page(
    project_id: str,
    section: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace = _project_or_404(store=store, project_id=project_id)
    _require_portfolio_read(database, user, project_id=project_id)
    normalized = _portfolio_section_slug(section)
    title = _portfolio_section_title(normalized)
    if title is None:
        raise HTTPException(status_code=404, detail="Portfolio dashboard page not found.")
    return _portfolio_dashboard_html(
        title,
        workspace,
        _portfolio_section_body(workspace=workspace, database=database, section=normalized),
    )


@router.get("/dashboard/projects/{project_id}/campaigns", response_class=HTMLResponse)
def campaign_list_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, campaign_store = _campaign_workspace(database, user, store, project_id)
    campaigns = campaign_store.list_campaigns(project_id=project_id)
    body = (
        _campaign_nav(project_id)
        + _campaign_boundary_notice()
        + "<h2>Campaign list</h2>"
        + _table(
            ["Campaign", "Name", "Status", "Hypotheses", "Portfolio selections"],
            [
                [
                    _link(
                        f"/dashboard/projects/{quote(project_id)}/campaigns/{quote(campaign.campaign_id)}",
                        campaign.campaign_id,
                    ),
                    campaign.name,
                    campaign.status,
                    ", ".join(campaign.hypothesis_ids),
                    ", ".join(campaign.portfolio_selection_ids),
                ]
                for campaign in campaigns
            ],
        )
    )
    if not campaigns:
        body += "<p>No campaign planning artifacts have been saved for this project.</p>"
    return _campaign_dashboard_html("Campaign list", workspace, body)


@router.get("/dashboard/projects/{project_id}/campaigns/{section}", response_class=HTMLResponse)
def campaign_section_page(
    project_id: str,
    section: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, campaign_store = _campaign_workspace(database, user, store, project_id)
    normalized = _campaign_section_slug(section)
    if normalized in {"detail", ""}:
        raise HTTPException(status_code=404, detail="Campaign dashboard page not found.")
    title = _campaign_section_title(normalized)
    if title is None:
        return campaign_detail_page(
            project_id=project_id,
            campaign_id=section,
            user=user,
            database=database,
            store=store,
        )
    return _campaign_dashboard_html(
        title,
        workspace,
        _campaign_section_body(
            campaign_store=campaign_store,
            database=database,
            project_id=project_id,
            section=normalized,
        ),
    )


def campaign_detail_page(
    project_id: str,
    campaign_id: str,
    user: UserAccount,
    database: PlatformDatabase,
    store: ProjectWorkspaceStore,
) -> Response:
    workspace, campaign_store = _campaign_workspace(database, user, store, project_id)
    try:
        campaign = campaign_store.get_campaign(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Campaign not found.") from exc
    if campaign.project_id not in {None, project_id}:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    body = (
        _campaign_nav(project_id)
        + _campaign_boundary_notice()
        + "<h2>Campaign detail</h2>"
        + _definition_list(
            {
                "Campaign ID": campaign.campaign_id,
                "Name": campaign.name,
                "Status": campaign.status,
                "Program": campaign.program_id or "",
                "Disease focus": ", ".join(campaign.disease_focus),
                "Target focus": ", ".join(campaign.target_focus),
                "Hypotheses": ", ".join(campaign.hypothesis_ids),
                "Portfolio selections": ", ".join(campaign.portfolio_selection_ids),
            }
        )
    )
    return _campaign_dashboard_html("Campaign detail", workspace, body)


@router.get("/dashboard/projects/{project_id}/knowledge-graph", response_class=HTMLResponse)
def knowledge_graph_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    return _knowledge_graph_dashboard_html(
        "Cross-program knowledge graph",
        workspace,
        _graph_overview_body(workspace, graph, request),
    )


@router.get("/dashboard/projects/{project_id}/knowledge-graph/search", response_class=HTMLResponse)
def knowledge_graph_search_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    query = request.query_params.get("q", "")
    entity_type = request.query_params.get("entity_type", "")
    entities = _graph_entities_matching(graph, query=query, entity_type=entity_type)
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Entity search</h2>"
        + "<p class=\"muted\">Search returns stored graph entities only; it does not create new "
        "nodes, claims, mechanisms, evidence, or assay results.</p>"
        + "<form class=\"panel form-grid\" method=\"get\">"
        "<label>Query <input name=\"q\" value=\""
        + _h(query)
        + "\"></label><label>Entity type <input name=\"entity_type\" value=\""
        + _h(entity_type)
        + "\"></label><button type=\"submit\">Search</button></form>"
        + _graph_entity_table(project_id, entities)
    )
    return _knowledge_graph_dashboard_html("Knowledge graph entity search", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/targets/{entity_id:path}",
    response_class=HTMLResponse,
)
def knowledge_graph_target_page(
    project_id: str,
    entity_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    entity = _graph_entity_or_404(graph, entity_id, {"target"})
    return _knowledge_graph_dashboard_html(
        f"Target graph page: {entity.name}",
        workspace,
        _graph_entity_detail_body(project_id, graph, entity),
    )


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/molecules/{entity_id:path}",
    response_class=HTMLResponse,
)
def knowledge_graph_molecule_page(
    project_id: str,
    entity_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    entity = _graph_entity_or_404(graph, entity_id, {"molecule"})
    return _knowledge_graph_dashboard_html(
        f"Molecule graph page: {entity.name}",
        workspace,
        _graph_entity_detail_body(project_id, graph, entity),
    )


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/generated-molecules/{entity_id:path}",
    response_class=HTMLResponse,
)
def knowledge_graph_generated_molecule_page(
    project_id: str,
    entity_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    entity = _graph_entity_or_404(graph, entity_id, {"generated_molecule"})
    return _knowledge_graph_dashboard_html(
        f"Generated molecule graph page: {entity.name}",
        workspace,
        _graph_entity_detail_body(project_id, graph, entity),
    )


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/mechanisms/{mechanism_id:path}",
    response_class=HTMLResponse,
)
def knowledge_graph_mechanism_page(
    project_id: str,
    mechanism_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    mechanism = next(
        (item for item in graph.mechanisms if item.mechanism_id == mechanism_id),
        None,
    )
    if mechanism is None:
        raise HTTPException(status_code=404, detail="Mechanism hypothesis not found.")
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Mechanism hypothesis page</h2>"
        + "<p><span class=\"mode-badge dry-run\">hypothesis, not proof</span></p>"
        + _definition_list(
            {
                "Mechanism ID": mechanism.mechanism_id,
                "Status": mechanism.status,
                "Summary": mechanism.summary,
                "Support score": mechanism.support_score,
                "Contradiction score": mechanism.contradiction_score,
                "Novelty score": mechanism.novelty_score,
                "Confidence": mechanism.confidence,
                "Warnings": "; ".join(mechanism.warnings),
            }
        )
        + _table(
            ["Linked entity class", "Entity IDs"],
            [
                ["Disease", mechanism.disease_entity_id or ""],
                ["Targets", ", ".join(mechanism.target_entity_ids)],
                ["Pathways", ", ".join(mechanism.pathway_entity_ids)],
                ["Molecules", ", ".join(mechanism.molecule_entity_ids)],
                ["Generated molecules", ", ".join(mechanism.generated_molecule_entity_ids)],
                ["Claims", ", ".join(mechanism.claim_entity_ids)],
                ["Support relations", ", ".join(mechanism.evidence_relation_ids)],
                ["Contradiction relations", ", ".join(mechanism.contradiction_relation_ids)],
            ],
        )
    )
    return _knowledge_graph_dashboard_html("Mechanism hypothesis", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/contradictions",
    response_class=HTMLResponse,
)
def knowledge_graph_contradictions_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    try:
        report = build_contradiction_report(graph)
        contradiction_relations = report.contradiction_relations
        findings = report.findings
    except ValueError:
        contradiction_relations = [
            relation for relation in graph.relations if relation.predicate == "contradicts"
        ]
        findings = []
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Contradiction report</h2>"
        + "<p class=\"warning\">Contradiction detection is advisory. Older evidence is retained "
        "and graph paths do not prove activity, safety, efficacy, binding, or causality.</p>"
        + _graph_relation_table(project_id, graph, contradiction_relations)
        + _graph_finding_table(findings)
    )
    return _knowledge_graph_dashboard_html("Contradiction report", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/staleness",
    response_class=HTMLResponse,
)
def knowledge_graph_staleness_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    report = build_staleness_report(graph)
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Staleness report</h2>"
        + "<p class=\"warning\">Staleness detection is advisory and preserves temporal "
        "provenance. Stale decisions and predictions should be reviewed, not deleted.</p>"
        + _graph_relation_table(project_id, graph, report.stale_relations)
        + _graph_finding_table(report.findings)
    )
    return _knowledge_graph_dashboard_html("Staleness report", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/recommendations",
    response_class=HTMLResponse,
)
def knowledge_graph_recommendations_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    recommendations = generate_graph_recommendations(graph, current_project_id=project_id)
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Cross-program recommendations</h2>"
        + "<p>Recommendations are advisory graph-derived summaries. They must not be read as "
        "activity, efficacy, safety, binding, or synthesis claims.</p>"
        + _table(
            ["Type", "Rationale", "Confidence", "Entities", "Relations", "Provenance"],
            [
                [
                    item.recommendation_type,
                    item.rationale,
                    item.confidence,
                    ", ".join(item.reuse_entity_ids),
                    ", ".join(item.relation_ids),
                    ", ".join(item.provenance),
                ]
                for item in recommendations
            ],
        )
    )
    if not recommendations:
        body += "<p>No cross-program graph recommendations are available yet.</p>"
    return _knowledge_graph_dashboard_html("Cross-program recommendations", workspace, body)


@router.get("/dashboard/projects/{project_id}/knowledge-graph/query", response_class=HTMLResponse)
def knowledge_graph_query_page(
    project_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    query_name = request.query_params.get("query", "generated_molecules_without_direct_evidence")
    results = _run_graph_dashboard_query(graph, query_name)
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Graph query explorer</h2>"
        + "<p>Query results are graph-derived summaries, not new evidence.</p>"
        + "<form class=\"panel form-grid\" method=\"get\"><label>Query <select name=\"query\">"
        + "".join(
            "<option value=\""
            + _h(name)
            + ("\" selected>" if name == query_name else "\">")
            + _h(name)
            + "</option>"
            for name in _graph_dashboard_queries()
        )
        + "</select></label><button type=\"submit\">Run query</button></form>"
        + _table(
            ["Query", "Entities", "Relations", "Provenance", "Confidence", "Warnings"],
            [
                [
                    result.query_name,
                    ", ".join(ref.name for ref in result.entity_refs),
                    ", ".join(ref.relation_id for ref in result.relation_refs),
                    ", ".join(result.provenance),
                    result.confidence,
                    "; ".join(result.warnings),
                ]
                for result in results
            ],
        )
    )
    if not results:
        body += "<p>No graph query results for this selection.</p>"
    return _knowledge_graph_dashboard_html("Graph query explorer", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/knowledge-graph/portfolio",
    response_class=HTMLResponse,
)
def knowledge_graph_portfolio_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, graph = _knowledge_graph_workspace(database, user, store, project_id)
    portfolio_entities = [
        entity
        for entity in graph.entities
        if entity.entity_type in {"portfolio", "program", "project"}
    ]
    selected_relations = [
        relation
        for relation in graph.relations
        if relation.predicate
        in {"selected_in_portfolio", "has_scaffold", "has_developability_risk"}
    ]
    body = (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + "<h2>Portfolio graph view</h2>"
        + "<p>Portfolio graph links are decision-memory context only. Selected candidates are "
        "not safety or activity claims.</p>"
        + _graph_entity_table(project_id, portfolio_entities)
        + _graph_relation_table(project_id, graph, selected_relations)
    )
    return _knowledge_graph_dashboard_html("Portfolio graph view", workspace, body)


@router.get("/dashboard/projects/{project_id}/hypotheses", response_class=HTMLResponse)
def hypothesis_overview_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    hypotheses = hypothesis_store.list_hypotheses(project_id=project_id)
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Hypothesis overview</h2>"
        + _hypothesis_generated_warning(hypotheses)
        + _table(
            ["Metric", "Value"],
            [
                ["Hypotheses", len(hypotheses)],
                ["Evidence gaps", len(hypothesis_store.list_evidence_gaps())],
                ["Research questions", len(hypothesis_store.list_research_questions())],
                [
                    "Falsification criteria",
                    len(hypothesis_store.list_falsification_criteria()),
                ],
            ],
        )
        + _hypothesis_table(project_id, hypotheses)
    )
    return _hypothesis_dashboard_html("Hypothesis overview", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/evidence-gaps",
    response_class=HTMLResponse,
)
def hypothesis_evidence_gaps_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    gaps = _project_hypothesis_children(
        hypothesis_store.list_evidence_gaps(),
        hypothesis_store,
        project_id,
    )
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Evidence gaps</h2>"
        + "<p>Evidence gaps are not failures. Absence of evidence is not evidence of absence.</p>"
        + _table(
            ["Gap", "Hypothesis", "Type", "Severity", "Suggested high-level resolution"],
            [
                [
                    gap.gap_id,
                    _hypothesis_link(project_id, gap.hypothesis_id),
                    gap.gap_type,
                    gap.severity,
                    gap.suggested_high_level_resolution,
                ]
                for gap in gaps
            ],
        )
    )
    return _hypothesis_dashboard_html("Evidence gaps", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/research-questions",
    response_class=HTMLResponse,
)
def hypothesis_research_questions_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    questions = _project_hypothesis_children(
        hypothesis_store.list_research_questions(),
        hypothesis_store,
        project_id,
    )
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Research questions</h2>"
        + "<p>Research questions are high-level planning questions, not lab protocols.</p>"
        + _table(
            ["Question", "Hypothesis", "Type", "Category", "Ambiguity notes"],
            [
                [
                    question.question_text,
                    _hypothesis_link(project_id, question.hypothesis_id),
                    question.question_type,
                    question.high_level_validation_category,
                    "; ".join(question.ambiguity_notes),
                ]
                for question in questions
            ],
        )
    )
    return _hypothesis_dashboard_html("Research questions", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/falsification-criteria",
    response_class=HTMLResponse,
)
def hypothesis_falsification_criteria_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    criteria = _project_hypothesis_children(
        hypothesis_store.list_falsification_criteria(),
        hypothesis_store,
        project_id,
    )
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Falsification criteria</h2>"
        + "<p>Criteria are decision-focused planning checks, not experimental procedures.</p>"
        + _table(
            ["Criterion", "Hypothesis", "Evidence type", "Decision impact"],
            [
                [
                    criterion.criterion_text,
                    _hypothesis_link(project_id, criterion.hypothesis_id),
                    criterion.evidence_type_needed,
                    criterion.decision_impact,
                ]
                for criterion in criteria
            ],
        )
    )
    return _hypothesis_dashboard_html("Falsification criteria", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/contradictions",
    response_class=HTMLResponse,
)
def hypothesis_contradictions_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    hypotheses = [
        hypothesis
        for hypothesis in hypothesis_store.list_hypotheses(project_id=project_id)
        if hypothesis.hypothesis_type == "assay_contradiction"
        or hypothesis.contradicting_relation_ids
        or hypothesis.status == "contradicted"
    ]
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Contradictions</h2>"
        + "<p>Contradiction-resolution hypotheses are review prompts, not proof that either "
        "side is correct.</p>"
        + _hypothesis_table(project_id, hypotheses)
    )
    return _hypothesis_dashboard_html("Contradictions", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/review-queue",
    response_class=HTMLResponse,
)
def hypothesis_review_queue_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    hypotheses = [
        hypothesis
        for hypothesis in hypothesis_store.list_hypotheses(project_id=project_id)
        if hypothesis.status in {"proposed", "under_review", "needs_more_evidence"}
        or _requires_generated_review(hypothesis)
    ]
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Review queue</h2>"
        + "<p>Codex cannot approve hypotheses. Generated-molecule hypotheses require human "
        "review before follow-up planning.</p>"
        + _hypothesis_table(project_id, hypotheses)
    )
    return _hypothesis_dashboard_html("Review queue", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/lifecycle",
    response_class=HTMLResponse,
)
def hypothesis_lifecycle_page(
    project_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    hypothesis_ids = {
        hypothesis.hypothesis_id
        for hypothesis in hypothesis_store.list_hypotheses(project_id=project_id)
    }
    events = [
        event
        for event in hypothesis_store.list_lifecycle_events()
        if event.hypothesis_id in hypothesis_ids
    ]
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Lifecycle timeline</h2>"
        + "<p>Hypothesis status changes are audited through lifecycle events.</p>"
        + _table(
            ["Time", "Hypothesis", "Event", "Actor", "Summary"],
            [
                [
                    event.timestamp.isoformat(),
                    _hypothesis_link(project_id, event.hypothesis_id),
                    event.event_type,
                    event.actor or "",
                    event.summary,
                ]
                for event in events
            ],
        )
    )
    return _hypothesis_dashboard_html("Lifecycle timeline", workspace, body)


@router.get(
    "/dashboard/projects/{project_id}/hypotheses/{hypothesis_id:path}",
    response_class=HTMLResponse,
)
def hypothesis_detail_page(
    project_id: str,
    hypothesis_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    store: Annotated[ProjectWorkspaceStore, Depends(workspace_store)],
) -> Response:
    workspace, hypothesis_store = _hypothesis_workspace(database, user, store, project_id)
    try:
        hypothesis = hypothesis_store.get_hypothesis(hypothesis_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Hypothesis not found.") from exc
    if hypothesis.metadata.get("project_id") not in {None, project_id}:
        raise HTTPException(status_code=404, detail="Hypothesis not found.")
    gaps = hypothesis_store.list_evidence_gaps(hypothesis_id)
    questions = hypothesis_store.list_research_questions(hypothesis_id)
    criteria = hypothesis_store.list_falsification_criteria(hypothesis_id)
    events = hypothesis_store.list_lifecycle_events(hypothesis_id)
    body = (
        _hypothesis_nav(project_id)
        + _hypothesis_boundary_notice()
        + "<h2>Hypothesis detail</h2>"
        + _hypothesis_generated_warning([hypothesis])
        + _definition_list(
            {
                "Hypothesis ID": hypothesis.hypothesis_id,
                "Type": hypothesis.hypothesis_type,
                "Status": hypothesis.status,
                "Priority": hypothesis.priority_score,
                "Confidence": hypothesis.confidence,
                "Title": hypothesis.title,
                "Statement": hypothesis.statement,
                "Warnings": _hypothesis_warning_labels(hypothesis),
                "Supporting relations": ", ".join(hypothesis.supporting_relation_ids),
                "Contradicting relations": ", ".join(hypothesis.contradicting_relation_ids),
                "Source artifacts": ", ".join(hypothesis.source_artifact_ids),
            }
        )
        + "<h2>Evidence gaps</h2>"
        + _table(
            ["Gap", "Type", "Severity"],
            [[gap.gap_id, gap.gap_type, gap.severity] for gap in gaps],
        )
        + "<h2>Research questions</h2>"
        + _table(
            ["Question", "Category"],
            [
                [question.question_text, question.high_level_validation_category]
                for question in questions
            ],
        )
        + "<h2>Falsification criteria</h2>"
        + _table(
            ["Criterion", "Decision impact"],
            [
                [criterion.criterion_text, criterion.decision_impact]
                for criterion in criteria
            ],
        )
        + "<h2>Lifecycle timeline</h2>"
        + _table(
            ["Time", "Event", "Actor", "Summary"],
            [
                [event.timestamp.isoformat(), event.event_type, event.actor or "", event.summary]
                for event in events
            ],
        )
    )
    return _hypothesis_dashboard_html(f"Hypothesis detail {hypothesis_id}", workspace, body)


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
    platform_artifact = (
        _platform_artifact_by_id(database, project_id=project_id, artifact_id=artifact_id)
        if artifact is None
        else None
    )
    if artifact is None and platform_artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if artifact is not None:
        artifact_type = artifact.artifact_type
        artifact_path = artifact.path
    else:
        assert platform_artifact is not None
        artifact_type = platform_artifact["type"]
        artifact_path = platform_artifact["path"]
    if _is_pose_artifact(artifact_type) and not has_permission(
        user,
        "structure:export",
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Missing permission structure:export.")
    if _is_hypothesis_artifact(artifact_type) and not has_permission(
        user,
        "hypothesis:export",
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Missing permission hypothesis:export.")
    path = safe_artifact_path(Path(artifact_path), root_dir=store.root_dir)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
        headers={"X-Artifact-ID": artifact_id},
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
        {
            "title": "Audit log",
            "user": user,
            "events": _admin_audit_events(database),
        },
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


@router.get("/dashboard/admin/roles", response_class=HTMLResponse)
def admin_roles_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_roles.html",
        {
            "title": "Admin roles",
            "user": user,
            "org_roles": _admin_role_matrix(ORG_ROLE_PERMISSIONS),
            "project_roles": _admin_role_matrix(PROJECT_ROLE_PERMISSIONS),
        },
    )


@router.get("/dashboard/admin/project-permissions", response_class=HTMLResponse)
def admin_project_permissions_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_project_permissions.html",
        {
            "title": "Project permissions",
            "user": user,
            "permissions": _admin_project_permissions(database),
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


@router.get("/dashboard/admin/integrations", response_class=HTMLResponse)
def admin_integrations_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    return _template(
        request,
        "admin_integrations.html",
        {
            "title": "Integration administration",
            "user": user,
            "summary": redact_for_log(database.integration_dashboard_summary()),
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
        {"title": "Admin audit", "user": user, "events": _admin_audit_events(database)},
    )


@router.get("/dashboard/admin/jobs", response_class=HTMLResponse)
def admin_jobs_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    page = normalize_limit_offset(
        limit=_query_int(request, "limit", default=100),
        offset=_query_int(request, "offset", default=0),
        max_limit=200,
    )
    jobs = PlatformJobQueue(database).list_jobs(limit=page.limit, offset=page.offset)
    page = page.__class__(limit=page.limit, offset=page.offset, count=len(jobs))
    return _template(
        request,
        "admin_jobs.html",
        {
            "title": "Admin job queue",
            "user": user,
            "jobs": jobs,
            "failed_jobs": database.list_failed_jobs(),
            "pagination": page,
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


@router.get("/dashboard/admin/workers", response_class=HTMLResponse)
def admin_workers_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    jobs = PlatformJobQueue(database).list_jobs(limit=200)
    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for job in jobs:
        status_counts[str(job.status)] = status_counts.get(str(job.status), 0) + 1
        type_counts[str(job.job_type)] = type_counts.get(str(job.job_type), 0) + 1
    return _template(
        request,
        "admin_workers.html",
        {
            "title": "Workers",
            "user": user,
            "status_counts": status_counts,
            "type_counts": type_counts,
            "queue_backlog": status_counts.get("queued", 0),
            "codex_status": database.codex_worker_status(),
        },
    )


@router.get("/dashboard/admin/slo", response_class=HTMLResponse)
def admin_slo_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.platform.slo import generate_slo_report

    report = generate_slo_report(
        database=database,
        backup_path=database.root_dir / ".molecule-ranker" / "backups",
    )
    return _template(
        request,
        "admin_slo.html",
        {
            "title": "SLO dashboard",
            "user": user,
            "report": report.to_dict(),
        },
    )


@router.get("/dashboard/admin/policies", response_class=HTMLResponse)
def admin_policies_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.platform.policy import PolicyEngine, default_policy_pack

    engine = PolicyEngine.default()
    action = str(request.query_params.get("action") or "export_generated_molecule")
    explanation = engine.explain(action, {"generated_molecule": True, "review_status": "pending"})
    return _template(
        request,
        "admin_policies.html",
        {
            "title": "Policies",
            "user": user,
            "rules": [rule.model_dump(mode="json") for rule in default_policy_pack()],
            "explanation": explanation,
        },
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


@router.get("/dashboard/admin/backup-restore", response_class=HTMLResponse)
def admin_backup_restore_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import build_admin_support_console

    console = build_admin_support_console(database, root_dir=database.root_dir)
    return _template(
        request,
        "admin_backup_restore.html",
        {
            "title": "Backup/restore",
            "user": user,
            "console": console,
            "action_result": request.query_params.get("action_result"),
        },
    )


@router.post("/dashboard/admin/backup-restore/verify")
def dashboard_admin_backup_restore_verify(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import run_admin_backup_verification

    run_admin_backup_verification(database, root_dir=database.root_dir, actor=user)
    return RedirectResponse(
        "/dashboard/admin/backup-restore?action_result=backup verification completed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard/admin/release-validation", response_class=HTMLResponse)
def admin_release_validation_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.v2 import validate_v2_release_contracts

    return _template(
        request,
        "admin_release_validation.html",
        {
            "title": "Release and validation package",
            "user": user,
            "contract_report": validate_v2_release_contracts(),
        },
    )


@router.get("/dashboard/admin/support", response_class=HTMLResponse)
def admin_support_console_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import build_admin_support_console

    return _template(
        request,
        "admin_support.html",
        {
            "title": "Admin support console",
            "user": user,
            "console": build_admin_support_console(database, root_dir=database.root_dir),
            "action_result": request.query_params.get("action_result"),
        },
    )


def _admin_support_redirect(action_result: str) -> RedirectResponse:
    return RedirectResponse(
        f"/dashboard/admin/support?action_result={quote(action_result)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard/feedback", response_class=HTMLResponse)
def feedback_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    from molecule_ranker.pilot.feedback import PilotFeedbackStore

    return _template(
        request,
        "pilot_feedback.html",
        {
            "title": "Pilot feedback",
            "user": user,
            "feedback": PilotFeedbackStore(database.root_dir).list(limit=25),
            "is_admin": user.is_admin,
            "submitted": request.query_params.get("submitted") == "1",
        },
    )


@router.post("/dashboard/feedback/submit")
async def submit_dashboard_feedback(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    from molecule_ranker.pilot.feedback import submit_feedback

    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    text = str((form.get("text") or [""])[0])
    feedback_type = str((form.get("feedback_type") or ["usability_issue"])[0])
    severity = str((form.get("severity") or ["medium"])[0])
    page_or_command = str((form.get("page_or_command") or ["dashboard"])[0])
    raw_project_id = str((form.get("project_id") or [""])[0]).strip()
    artifact_refs = str((form.get("artifact_refs") or [""])[0])
    submit_feedback(
        root_dir=database.root_dir,
        user_id=user.user_id,
        project_id=raw_project_id or None,
        page_or_command=page_or_command,
        feedback_type=feedback_type,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        text=text,
        artifact_refs=[item.strip() for item in artifact_refs.split(",") if item.strip()],
    )
    return RedirectResponse(
        "/dashboard/feedback?submitted=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard/admin/feedback", response_class=HTMLResponse)
def admin_feedback_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.feedback import PilotFeedbackStore

    return _template(
        request,
        "admin_feedback.html",
        {
            "title": "Pilot feedback admin",
            "user": user,
            "feedback": PilotFeedbackStore(database.root_dir).list(limit=500),
        },
    )


@router.get("/dashboard/admin/feedback/export")
def admin_feedback_export(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    from molecule_ranker.pilot.feedback import PilotFeedbackStore

    feedback = PilotFeedbackStore(database.root_dir).list(limit=10_000)
    return {
        "feedback": [item.model_dump(mode="json") for item in feedback],
        "not_scientific_evidence": True,
        "excludes_cache_payloads": True,
        "excludes_artifact_payloads": True,
    }


@router.post("/dashboard/admin/support/jobs/{job_id}/retry")
def dashboard_admin_support_retry_job(
    job_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import retry_failed_job

    try:
        result = retry_failed_job(database, job_id=job_id, actor=user)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _admin_support_redirect(f"retry completed: {result.get('status', 'queued')}")


@router.post("/dashboard/admin/support/jobs/{job_id}/cancel")
def dashboard_admin_support_cancel_job(
    job_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import cancel_job

    try:
        result = cancel_job(database, job_id=job_id, actor=user)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _admin_support_redirect(f"cancel completed: {result.get('status', 'cancelled')}")


@router.post("/dashboard/admin/support/jobs/{job_id}/requeue-dead-letter")
def dashboard_admin_support_requeue_dead_letter(
    job_id: str,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import requeue_dead_letter_job

    try:
        result = requeue_dead_letter_job(database, job_id=job_id, actor=user)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _admin_support_redirect(
        f"dead-letter requeue completed: {result.get('status', 'queued')}"
    )


@router.post("/dashboard/admin/support/support-bundle")
def dashboard_admin_support_bundle(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import generate_admin_support_bundle

    generate_admin_support_bundle(database, root_dir=database.root_dir, actor=user)
    return _admin_support_redirect("support bundle created with manifest")


@router.post("/dashboard/admin/support/readiness-check")
def dashboard_admin_support_readiness(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import run_admin_readiness_check

    run_admin_readiness_check(database, root_dir=database.root_dir, actor=user)
    return _admin_support_redirect("readiness check completed with report")


@router.post("/dashboard/admin/support/migration-dry-run")
def dashboard_admin_support_migration_dry_run(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import run_admin_migration_dry_run

    run_admin_migration_dry_run(database, root_dir=database.root_dir, actor=user)
    return _admin_support_redirect("migration dry-run completed with report")


@router.post("/dashboard/admin/support/backup-verify")
def dashboard_admin_support_backup_verify(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import run_admin_backup_verification

    run_admin_backup_verification(database, root_dir=database.root_dir, actor=user)
    return _admin_support_redirect("backup verification completed with report")


@router.get("/dashboard/admin/support/redacted-logs")
def dashboard_admin_support_redacted_logs(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    require_platform_admin(user)
    from molecule_ranker.pilot.admin_support import view_redacted_logs

    return view_redacted_logs(database, root_dir=database.root_dir, actor=user)


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


STRUCTURE_DASHBOARD_SECTIONS: dict[str, dict[str, Any]] = {
    "target-structures": {
        "title": "Target structures",
        "filename": "structures.json",
        "key": "structures",
        "columns": ["structure_id", "source", "target_symbol", "structure_type"],
        "notice": "Structure reports cannot be interpreted as binding evidence.",
    },
    "selection": {
        "title": "Structure selection",
        "filename": "structure_selection.json",
        "key": "structure_selection",
        "columns": ["selection_id", "selected_structure_id", "confidence"],
        "notice": "Structure selection is provenance-aware computational triage.",
    },
    "receptor-preparation": {
        "title": "Receptor preparation",
        "filename": "receptor_preparation.json",
        "key": "receptor_preparation",
        "columns": ["receptor_prep_id", "structure_id", "preparation_method"],
        "notice": "Receptor preparation is a computational workflow.",
    },
    "binding-sites": {
        "title": "Binding sites",
        "filename": "binding_sites.json",
        "key": "binding_sites",
        "columns": ["binding_site_id", "method", "confidence"],
        "notice": "Binding-site boxes and residues require provenance.",
    },
    "docking-runs": {
        "title": "Docking runs",
        "filename": "docking_runs.json",
        "key": "docking_runs",
        "columns": ["docking_run_id", "docking_engine", "status"],
        "notice": "Docking is a computational workflow. Docking scores do not prove binding.",
    },
    "docking-poses": {
        "title": "Docking poses",
        "filename": "docking_poses.json",
        "key": "docking_poses",
        "columns": ["pose_id", "docking_score", "confidence"],
        "notice": "Docking poses are computational hypotheses.",
    },
    "interaction-profiles": {
        "title": "Interaction profiles",
        "filename": "interaction_profiles.json",
        "key": "interaction_profiles",
        "columns": ["profile_id", "interaction_counts", "confidence"],
        "notice": "Interactions are computational pose annotations, not experimental evidence.",
    },
    "assessments": {
        "title": "Structure-aware assessments",
        "filename": "structure_aware_assessments.json",
        "key": "structure_aware_assessments",
        "columns": ["assessment_id", "recommendation", "consensus_score"],
        "notice": "Structure-aware assessments are not binding evidence.",
    },
    "benchmarks": {
        "title": "Structure benchmark reports",
        "filename": "structure_benchmark_report.json",
        "key": "metrics",
        "columns": ["metric", "value"],
        "notice": "Benchmark reports audit workflow quality and do not validate binding.",
    },
}


def _structure_page(
    project_id: str,
    section: str,
    user: UserAccount,
    database: PlatformDatabase,
    store: ProjectWorkspaceStore,
) -> HTMLResponse:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "structure:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Structure permission denied.")
    spec = STRUCTURE_DASHBOARD_SECTIONS.get(section)
    if spec is None:
        raise HTTPException(status_code=404, detail="Structure dashboard section not found.")
    records = _structure_dashboard_records(workspace, spec)
    body = (
        _structure_nav(project_id)
        + "<p class=\"notice\">Structure reports cannot be interpreted as binding evidence. "
        "Docking scores do not prove binding. Poses are computational hypotheses.</p>"
        + f"<p class=\"muted\">{_h(spec['notice'])}</p>"
        + _table(
            [str(column).replace("_", " ").title() for column in spec["columns"]],
            [
                [
                    _compact_json(record.get(column))
                    if isinstance(record.get(column), (dict, list))
                    else record.get(column, "")
                    for column in spec["columns"]
                ]
                for record in records
            ],
        )
    )
    if not records:
        body += "<p>No structure artifacts are available for this section.</p>"
    body += (
        "<p class=\"muted\">No synthesis instructions, lab protocols, dosing guidance, "
        "or clinical claims are provided.</p>"
    )
    return _structure_dashboard_html(str(spec["title"]), workspace, body)


def _structure_nav(project_id: str) -> str:
    links = [
        ("Target structures", "target-structures"),
        ("Structure selection", "selection"),
        ("Receptor preparation", "receptor-preparation"),
        ("Binding sites", "binding-sites"),
        ("Docking runs", "docking-runs"),
        ("Docking poses", "docking-poses"),
        ("Interaction profiles", "interaction-profiles"),
        ("Structure-aware assessments", "assessments"),
        ("Structure benchmark reports", "benchmarks"),
    ]
    return "<nav class=\"nav\">" + " ".join(
        _link(f"/dashboard/projects/{project_id}/structure/{slug}", label)
        for label, slug in links
    ) + "</nav>"


def _structure_dashboard_html(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = workspace.workspace_id
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/structure/target-structures', 'Structure')}"
        f"{_link(f'/dashboard/projects/{project_id}/runs', 'Runs')}"
        f"{_link(f'/dashboard/projects/{project_id}/design/plans', 'Design')}"
        "</nav></div></header><main class=\"content\">"
        "<aside class=\"research-disclaimer\">Internal research use only. Source-backed "
        "evidence remains authoritative. Structure reports cannot be interpreted as binding "
        "evidence.</aside>"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(project_id)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _structure_dashboard_records(
    workspace: ProjectWorkspace,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run in workspace.runs:
        path = Path(run.run_dir) / str(spec["filename"])
        if not path.exists() or not safe_artifact_path(path, root_dir=Path(workspace.root_dir)):
            continue
        try:
            payload = load_json_file(path)
        except (OSError, json.JSONDecodeError, JsonArtifactTooLargeError):
            continue
        records.extend(_structure_records_from_payload(payload, str(spec["key"])))
    return records


def _structure_records_from_payload(payload: Any, key: str) -> list[dict[str, Any]]:
    if key == "metrics" and isinstance(payload, dict):
        metrics = payload.get("metrics", payload)
        if isinstance(metrics, dict):
            return [
                {"metric": metric, "value": value}
                for metric, value in sorted(metrics.items())
            ]
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    singular = payload.get(key.rstrip("s"))
    if isinstance(singular, dict):
        return [dict(singular)]
    return []


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
            payload = load_json_file(path)
        except (OSError, json.JSONDecodeError, JsonArtifactTooLargeError):
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
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


def _portfolio_dashboard_html(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = quote(workspace.workspace_id)
    nav = " ".join(
        _link(f"/dashboard/projects/{project_id}/portfolio/{slug}", label)
        for slug, label in _portfolio_dashboard_nav().items()
        if slug != "overview"
    )
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/portfolio', 'Portfolio')}"
        "</nav></div></header><main class=\"content\">"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(workspace.name)}</p></header>"
        "<nav class=\"section\">"
        f"{_link(f'/dashboard/projects/{project_id}/portfolio', 'Program overview')} "
        f"{nav}</nav>"
        "<aside class=\"research-disclaimer\">Portfolio optimization output is advisory until "
        "approved. Codex decision memos are assistant output and not final decisions. External "
        "exports of selected portfolios require explicit permission.</aside>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _campaign_workspace(
    database: PlatformDatabase,
    user: UserAccount,
    store: ProjectWorkspaceStore,
    project_id: str,
) -> tuple[ProjectWorkspace, CampaignStore]:
    workspace = _project_or_404(store=store, project_id=project_id)
    if not user.is_admin and not has_permission(
        user,
        "campaign:read",
        project_id=project_id,
        database=database,
    ):
        raise HTTPException(status_code=403, detail="Campaign permission denied.")
    return workspace, CampaignStore(_hosted_campaign_store_path(database.root_dir, project_id))


def _hosted_campaign_store_path(root_dir: Any, project_id: str) -> Path:
    return Path(root_dir) / ".molecule-ranker" / "campaigns" / project_id / "campaigns.sqlite"


def _campaign_dashboard_html(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = quote(workspace.workspace_id)
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/campaigns', 'Campaigns')}"
        "</nav></div></header><main class=\"content\">"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(workspace.name)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _campaign_nav(project_id: str) -> str:
    quoted = quote(project_id)
    return "<nav class=\"section\">" + " ".join(
        _link(f"/dashboard/projects/{quoted}/campaigns{path}", label)
        for path, label in [
            ("", "Campaign list"),
            ("/plan", "Campaign plan"),
            ("/work-packages", "Work packages"),
            ("/budget", "Budget/resource view"),
            ("/dependencies", "Dependency graph"),
            ("/stage-gates", "Stage gates"),
            ("/replan-triggers", "Replan triggers"),
            ("/memo", "Campaign memo"),
            ("/audit", "Campaign audit timeline"),
        ]
    ) + "</nav>"


def _campaign_boundary_notice() -> str:
    return (
        "<aside class=\"research-disclaimer\">Campaign plans are research-management guidance, "
        "not lab protocols. They do not contain synthesis instructions, dosing, clinical "
        "guidance, or claims that selected candidates are active, safe, effective, "
        "synthesizable, or clinically useful. Codex memos are labeled assistant output and "
        "kept separate from deterministic campaign plans.</aside>"
    )


def _campaign_section_slug(section: str) -> str:
    aliases = {
        "campaign-plan": "plan",
        "budget-resource-view": "budget",
        "dependency-graph": "dependencies",
        "replan": "replan-triggers",
        "triggers": "replan-triggers",
        "campaign-memo": "memo",
        "audit-timeline": "audit",
    }
    return aliases.get(section, section)


def _campaign_section_title(section: str) -> str | None:
    return {
        "plan": "Campaign plan",
        "work-packages": "Work packages",
        "budget": "Budget/resource view",
        "dependencies": "Dependency graph",
        "stage-gates": "Stage gates",
        "replan-triggers": "Replan triggers",
        "memo": "Campaign memo",
        "audit": "Campaign audit timeline",
    }.get(section)


def _campaign_section_body(
    *,
    campaign_store: CampaignStore,
    database: PlatformDatabase,
    project_id: str,
    section: str,
) -> str:
    campaign = _latest_campaign(campaign_store, project_id)
    if campaign is None:
        return (
            _campaign_nav(project_id)
            + _campaign_boundary_notice()
            + f"<h2>{_h(_campaign_section_title(section) or 'Campaigns')}</h2>"
            "<p>No campaign planning artifacts have been saved for this project.</p>"
        )
    plan = _latest_campaign_plan_or_none(campaign_store, campaign.campaign_id)
    body = _campaign_nav(project_id) + _campaign_boundary_notice()
    if section == "plan":
        if plan is None:
            return body + "<h2>Campaign plan</h2><p>No deterministic campaign plan is saved.</p>"
        return (
            body
            + "<h2>Campaign plan</h2>"
            "<p>Deterministic campaign plan artifact. Codex cannot create or approve this plan.</p>"
            + _definition_list(
                {
                    "Campaign plan ID": plan.campaign_plan_id,
                    "Campaign": plan.campaign_id,
                    "Expected learning value": plan.expected_learning_value,
                    "Human approval required": plan.human_approval_required,
                    "Recommended sequence": ", ".join(plan.recommended_sequence),
                    "Warnings": "; ".join(plan.warnings),
                }
            )
            + "<h3>Objectives</h3>"
            + _table(
                ["Objective", "Type", "Hypotheses", "Candidates", "Weight"],
                [
                    [
                        objective.objective_id,
                        objective.objective_type,
                        ", ".join(objective.linked_hypothesis_ids),
                        ", ".join(objective.linked_candidate_ids),
                        objective.priority_weight,
                    ]
                    for objective in plan.objectives
                ],
            )
        )
    if section == "work-packages":
        packages = _current_campaign_work_packages(campaign_store, plan)
        return (
            body
            + "<h2>Work packages</h2>"
            "<p>Campaign work packages are planning objects, not protocols.</p>"
            + _table(
                ["Package", "Type", "Status", "Approvals", "Candidates", "Warnings"],
                [
                    [
                        package.work_package_id,
                        package.package_type,
                        package.status,
                        ", ".join(package.required_approvals),
                        ", ".join(package.linked_candidate_ids),
                        "; ".join(package.warnings),
                    ]
                    for package in packages
                ],
            )
        )
    if section == "budget":
        if plan is None:
            return body + "<h2>Budget/resource view</h2><p>No budget artifact is saved.</p>"
        return (
            body
            + "<h2>Budget/resource view</h2>"
            "<p>Cost fields are planning estimates only and do not imply vendor or lab pricing.</p>"
            + _definition_list(
                {
                    "Budget": plan.budget.budget_id,
                    "Max assay slots": plan.budget.max_assay_slots,
                    "Max review hours": plan.budget.max_review_hours,
                    "Max compute units": plan.budget.max_compute_units,
                    "Max total cost": plan.budget.max_total_cost,
                    "Cost units": plan.budget.cost_units or "unknown",
                }
            )
            + "<h3>Budget summary</h3><pre>"
            + _h(_compact_json(plan.budget_summary))
            + "</pre>"
        )
    if section == "dependencies":
        if plan is None:
            return body + "<h2>Dependency graph</h2><p>No dependency graph is saved.</p>"
        return (
            body
            + "<h2>Dependency graph</h2>"
            "<p>This is planning order, not a lab protocol sequence.</p><pre>"
            + _h(_compact_json(plan.dependency_graph))
            + "</pre>"
        )
    if section == "stage-gates":
        gates = campaign_store.list_stage_gates(campaign.campaign_id)
        return (
            body
            + "<h2>Stage gates</h2>"
            "<p>Campaign and stage gate approvals require campaign approval permission. "
            "Generated molecule follow-up requires a review gate.</p>"
            + _table(
                ["Gate", "Type", "Status", "Work package", "Required permissions"],
                [
                    [
                        gate.get("gate_id", ""),
                        gate.get("gate_type", ""),
                        gate.get("approval_status", ""),
                        gate.get("work_package_id", ""),
                        ", ".join(str(item) for item in gate.get("required_permissions", [])),
                    ]
                    for gate in gates
                ],
            )
        )
    if section == "replan-triggers":
        triggers = campaign_store.list_replan_triggers(campaign.campaign_id)
        return (
            body
            + "<h2>Replan triggers</h2>"
            + _table(
                ["Trigger", "Type", "Severity", "Action", "Description"],
                [
                    [
                        trigger.trigger_id,
                        trigger.trigger_type,
                        trigger.severity,
                        trigger.recommended_action,
                        trigger.description,
                    ]
                    for trigger in triggers
                ],
            )
        )
    if section == "memo":
        memos = campaign_store.list_campaign_memos(campaign.campaign_id)
        return (
            body
            + "<h2>Campaign memo</h2>"
            "<p>Codex memo assistant output is separate from the deterministic campaign plan.</p>"
            + _table(
                ["Memo", "Title", "Assistant output", "Budget summary", "Limitations"],
                [
                    [
                        memo.memo_id,
                        memo.title,
                        bool(memo.metadata.get("assistant_output") or memo.metadata.get("codex")),
                        memo.budget_summary,
                        "; ".join(memo.limitations),
                    ]
                    for memo in memos
                ],
            )
        )
    if section == "audit":
        campaign_events = campaign_store.list_execution_events(campaign.campaign_id)
        platform_events = [
            event
            for event in database.list_audit_events(project_id=project_id, limit=100)
            if str(event.object_type).startswith("campaign")
            or str(event.metadata.get("campaign_id", "")) == campaign.campaign_id
        ]
        return (
            body
            + "<h2>Campaign audit timeline</h2>"
            + _table(
                ["Source", "Event", "Actor", "Summary"],
                [
                    ["campaign", event.event_type, event.actor or "", event.summary]
                    for event in campaign_events
                ]
                + [
                    ["platform", event.event_type, event.actor_user_id or "", event.summary]
                    for event in platform_events
                ],
            )
        )
    raise HTTPException(status_code=404, detail="Campaign dashboard page not found.")


def _latest_campaign(campaign_store: CampaignStore, project_id: str) -> Any | None:
    campaigns = campaign_store.list_campaigns(project_id=project_id)
    if campaigns:
        return campaigns[0]
    all_campaigns = campaign_store.list_campaigns()
    return all_campaigns[0] if all_campaigns else None


def _latest_campaign_plan_or_none(
    campaign_store: CampaignStore,
    campaign_id: str,
) -> CampaignPlan | None:
    try:
        return campaign_store.get_latest_campaign_plan(campaign_id)
    except ValueError:
        return None


def _current_campaign_work_packages(
    campaign_store: CampaignStore,
    plan: CampaignPlan | None,
) -> list[Any]:
    if plan is None:
        return []
    packages = []
    for package in plan.work_packages:
        try:
            packages.append(campaign_store.get_work_package(package.work_package_id))
        except ValueError:
            packages.append(package)
    return packages


def _portfolio_dashboard_nav() -> dict[str, str]:
    return {
        "overview": "Program overview",
        "candidates": "Portfolio candidates",
        "optimization-runs": "Optimization runs",
        "scenarios": "Scenario analysis",
        "selected": "Selected portfolio",
        "rejected-deferred": "Rejected/deferred candidates",
        "stage-gates": "Stage gates",
        "batches": "Portfolio batches",
        "memos": "Decision memos",
        "audit": "Portfolio audit log",
    }


def _portfolio_section_slug(section: str) -> str:
    aliases = {
        "program-overview": "overview",
        "scenario-analysis": "scenarios",
        "selected-portfolio": "selected",
        "rejected-deferred-candidates": "rejected-deferred",
        "portfolio-batches": "batches",
        "decision-memos": "memos",
        "audit-log": "audit",
        "portfolio-audit-log": "audit",
    }
    return aliases.get(section, section)


def _portfolio_section_title(section: str) -> str | None:
    return _portfolio_dashboard_nav().get(section)


def _portfolio_section_body(
    *,
    workspace: ProjectWorkspace,
    database: PlatformDatabase,
    section: str,
) -> str:
    project_id = workspace.workspace_id
    snapshots = _portfolio_run_snapshots(workspace)
    summary = _table(
        ["Metric", "Value"],
        [
            ["Runs", len(workspace.runs)],
            ["Candidate artifacts", sum(int(item.get("candidate_count", 0)) for item in snapshots)],
            [
                "Generated hypotheses",
                sum(int(item.get("generated_candidate_count", 0)) for item in snapshots),
            ],
        ],
    )
    if section == "overview":
        return (
            "<h2>Program overview</h2>"
            "<p>Program-level decision analytics summarize candidates, scenarios, "
            "stage gates, batches, memos, and audit events.</p>"
            f"{summary}"
        )
    if section == "candidates":
        return "<h2>Portfolio candidates</h2>" + _table(
            ["Run", "Disease", "Candidate count", "Generated count"],
            [
                [
                    item.get("run_id", "unknown"),
                    item.get("disease", "unknown"),
                    item.get("candidate_count", 0),
                    item.get("generated_candidate_count", 0),
                ]
                for item in snapshots
            ],
        )
    if section == "optimization-runs":
        return (
            "<h2>Optimization runs</h2><p>Optimization selections are deterministic "
            "research prioritization aids and remain advisory until approved.</p>"
            + _portfolio_artifact_table(workspace, database, "portfolio_optimization")
        )
    if section == "scenarios":
        return (
            "<h2>Scenario analysis</h2><p>Scenario comparisons test whether portfolio "
            "choices are robust under uncertainty, budget limits, and risk settings.</p>"
            + _portfolio_artifact_table(workspace, database, "scenario")
        )
    if section == "selected":
        return (
            "<h2>Selected portfolio</h2><p>Selected candidates are prioritized for "
            "review workflows only; no safety, activity, efficacy, or synthesizability "
            "claim is made.</p>"
            + _portfolio_artifact_table(workspace, database, "selection")
        )
    if section == "rejected-deferred":
        return (
            "<h2>Rejected/deferred candidates</h2><p>Rejected and deferred candidates "
            "retain rationale for auditability and future reassessment.</p>"
            + _portfolio_artifact_table(workspace, database, "deferred")
        )
    if section == "stage-gates":
        return (
            "<h2>Stage gates</h2><p>Stage-gate approval requires portfolio:approve_stage_gate "
            "permission and is recorded as an audit event.</p>"
            + _portfolio_audit_table(database, project_id, "portfolio_stage_gate")
        )
    if section == "batches":
        return (
            "<h2>Portfolio batches</h2><p>Batches are high-level planning artifacts, "
            "not lab protocols.</p>"
            + _portfolio_artifact_table(workspace, database, "portfolio_batch")
        )
    if section == "memos":
        return (
            "<h2>Decision memos</h2><p>Codex decision memos are assistant output and not "
            "final decisions.</p>"
            + _portfolio_artifact_table(workspace, database, "program_decision_memo")
        )
    if section == "audit":
        return (
            "<h2>Portfolio audit log</h2>"
            + _portfolio_audit_table(database, project_id, "portfolio")
        )
    return "<p>Portfolio dashboard page not found.</p>"


def _knowledge_graph_workspace(
    database: PlatformDatabase,
    user: UserAccount,
    store: ProjectWorkspaceStore,
    project_id: str,
) -> tuple[ProjectWorkspace, KnowledgeGraph]:
    workspace = _project_or_404(store=store, project_id=project_id)
    require_project_access(database, user, project_id=project_id, action="read")
    graph_store = KnowledgeGraphStore(Path(workspace.root_dir))
    graph_ids = graph_store.list_graphs()
    graph = (
        graph_store.load(graph_ids[-1])
        if graph_ids
        else KnowledgeGraph(
            graph_id=f"{workspace.workspace_id}-knowledge-graph",
            metadata={"empty_state": "No graph has been built for this project yet."},
        )
    )
    return workspace, graph


def _knowledge_graph_dashboard_html(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = quote(workspace.workspace_id)
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/knowledge-graph', 'Knowledge Graph')}"
        f"{_link(f'/dashboard/projects/{project_id}/portfolio', 'Portfolio')}"
        "</nav></div></header><main class=\"content\">"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(workspace.name)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _graph_nav(project_id: str) -> str:
    quoted = quote(project_id)
    links = {
        "Graph overview": f"/dashboard/projects/{quoted}/knowledge-graph",
        "Entity search": f"/dashboard/projects/{quoted}/knowledge-graph/search",
        "Contradictions": f"/dashboard/projects/{quoted}/knowledge-graph/contradictions",
        "Staleness": f"/dashboard/projects/{quoted}/knowledge-graph/staleness",
        "Recommendations": f"/dashboard/projects/{quoted}/knowledge-graph/recommendations",
        "Query explorer": f"/dashboard/projects/{quoted}/knowledge-graph/query",
        "Portfolio graph": f"/dashboard/projects/{quoted}/knowledge-graph/portfolio",
    }
    return "<nav class=\"section\">" + " ".join(
        _link(href, label) for label, href in links.items()
    ) + "</nav>"


def _graph_boundary_notice() -> str:
    return (
        "<aside class=\"research-disclaimer\">This graph is a memory and reasoning layer. It "
        "does not create biomedical truth, EvidenceItem records, assay results, causality, "
        "efficacy, safety, binding, or activity claims. Inferred graph relationships are "
        "hypotheses unless backed by source evidence.</aside>"
    )


def _graph_overview_body(
    workspace: ProjectWorkspace,
    graph: KnowledgeGraph,
    request: Request,
) -> str:
    del request
    analysis = analyze_cross_program_knowledge(graph)
    entity_counts = Counter(entity.entity_type for entity in graph.entities)
    relation_counts = Counter(relation.relation_type for relation in graph.relations)
    targets = [entity for entity in graph.entities if entity.entity_type == "target"][:8]
    molecules = [entity for entity in graph.entities if entity.entity_type == "molecule"][:8]
    generated = [
        entity for entity in graph.entities if entity.entity_type == "generated_molecule"
    ][:8]
    predictions = [
        entity for entity in graph.entities if entity.entity_type == "model_prediction"
    ][:8]
    body = (
        _graph_nav(workspace.workspace_id)
        + _graph_boundary_notice()
        + "<h2>Graph overview</h2>"
        + _table(
            ["Metric", "Value"],
            [
                ["Graph ID", graph.graph_id],
                ["Entities", len(graph.entities)],
                ["Relations", len(graph.relations)],
                ["Provenance records", len(graph.provenance)],
                ["Mechanism hypotheses", len(graph.mechanisms)],
            ],
        )
        + "<h2>Entity types</h2>"
        + _table(["Entity type", "Count"], [[key, value] for key, value in entity_counts.items()])
        + "<h2>Relation types</h2>"
        + _table(
            ["Relation type", "Count", "Label"],
            [
                [key, value, _graph_relation_type_badge(str(key))]
                for key, value in relation_counts.items()
            ],
        )
        + "<h2>Target pages</h2>"
        + _graph_entity_table(workspace.workspace_id, targets)
        + "<h2>Molecule pages</h2>"
        + _graph_entity_table(workspace.workspace_id, molecules)
        + "<h2>Generated molecule pages</h2>"
        + _graph_entity_table(workspace.workspace_id, generated)
        + "<h2>Model predictions</h2>"
        + "<p>Model predictions are predictions, not evidence or assay results.</p>"
        + _graph_entity_table(workspace.workspace_id, predictions)
        + "<h2>Mechanism hypotheses</h2>"
        + _table(
            ["Mechanism", "Status", "Summary", "Confidence"],
            [
                [
                    _link(
                        f"/dashboard/projects/{quote(workspace.workspace_id)}/knowledge-graph/"
                        f"mechanisms/{quote(item.mechanism_id)}",
                        item.mechanism_id,
                    ),
                    item.status,
                    item.summary,
                    item.confidence,
                ]
                for item in graph.mechanisms
            ],
        )
        + "<h2>Recent graph edges</h2>"
        + _graph_relation_table(workspace.workspace_id, graph, graph.relations[:12])
        + "<h2>Codex graph summaries</h2>"
        + _graph_codex_summary_table(graph)
        + "<h2>Cross-program patterns</h2>"
        + _table(
            ["Pattern", "Count", "Rationale"],
            [
                [pattern.name, pattern.count, pattern.rationale]
                for pattern in [
                    *analysis.recurring_mechanisms,
                    *analysis.scaffold_patterns,
                    *analysis.repeated_developability_risks,
                ]
            ],
        )
    )
    if not graph.entities:
        empty_state = graph.metadata.get(
            "empty_state",
            "The current graph has no entities yet. "
            "Build or import source artifacts to populate it.",
        )
        body += f"<p>{_h(empty_state)}</p>"
    return body


def _graph_entities_matching(
    graph: KnowledgeGraph,
    *,
    query: str,
    entity_type: str,
) -> list[GraphEntity]:
    normalized_query = query.lower().strip()
    normalized_type = entity_type.lower().strip()
    entities = graph.entities
    if normalized_type:
        entities = [
            entity for entity in entities if entity.entity_type.lower() == normalized_type
        ]
    if normalized_query:
        entities = [
            entity
            for entity in entities
            if normalized_query in entity.name.lower()
            or normalized_query in entity.entity_id.lower()
            or any(normalized_query in value.lower() for value in entity.identifiers.values())
        ]
    return sorted(entities, key=lambda entity: (entity.entity_type, entity.name, entity.entity_id))


def _graph_entity_table(project_id: str, entities: list[GraphEntity]) -> str:
    return _table(
        ["Entity", "Type", "Canonical ID", "Identifiers", "Labels", "Provenance"],
        [
            [
                _graph_entity_link(project_id, entity),
                entity.entity_type,
                entity.canonical_id or "",
                _compact_json(entity.identifiers),
                _graph_entity_badges(entity),
                ", ".join(entity.provenance_refs or entity.source_artifact_ids),
            ]
            for entity in entities
        ],
    )


def _graph_entity_detail_body(
    project_id: str,
    graph: KnowledgeGraph,
    entity: GraphEntity,
) -> str:
    incident = [
        relation
        for relation in graph.relations
        if entity.entity_id in {relation.subject_entity_id, relation.object_entity_id}
    ]
    return (
        _graph_nav(project_id)
        + _graph_boundary_notice()
        + f"<h2>{_h(entity.entity_type.replace('_', ' ').title())}</h2>"
        + _graph_entity_badges(entity)
        + _definition_list(
            {
                "Entity ID": entity.entity_id,
                "Name": entity.name,
                "Type": entity.entity_type,
                "Canonical ID": entity.canonical_id or "",
                "Identifiers": _compact_json(entity.identifiers),
                "Source artifacts": ", ".join(entity.source_artifact_ids),
                "Provenance refs": ", ".join(entity.provenance_refs),
            }
        )
        + "<h2>Graph edges</h2>"
        + _graph_relation_table(project_id, graph, incident)
        + "<h2>Metadata</h2>"
        + f"<pre>{_h(_compact_json(entity.metadata))}</pre>"
    )


def _graph_entity_or_404(
    graph: KnowledgeGraph,
    entity_id: str,
    allowed_types: set[str],
) -> GraphEntity:
    entity = graph.entity_map().get(entity_id)
    if entity is None or entity.entity_type not in allowed_types:
        raise HTTPException(status_code=404, detail="Graph entity not found.")
    return entity


def _graph_entity_link(project_id: str, entity: GraphEntity) -> str:
    path = _graph_entity_path(project_id, entity)
    if path is None:
        return _h(entity.name)
    return _link(path, entity.name)


def _graph_entity_path(project_id: str, entity: GraphEntity) -> str | None:
    quoted_project = quote(project_id)
    quoted_entity = quote(entity.entity_id, safe="")
    if entity.entity_type == "target":
        return f"/dashboard/projects/{quoted_project}/knowledge-graph/targets/{quoted_entity}"
    if entity.entity_type == "molecule":
        return f"/dashboard/projects/{quoted_project}/knowledge-graph/molecules/{quoted_entity}"
    if entity.entity_type == "generated_molecule":
        return (
            f"/dashboard/projects/{quoted_project}/knowledge-graph/generated-molecules/"
            f"{quoted_entity}"
        )
    return None


def _graph_entity_badges(entity: GraphEntity) -> str:
    badges: list[str] = []
    if entity.entity_type == "generated_molecule":
        badges.append("Generated hypothesis")
    if entity.entity_type == "model_prediction":
        badges.append("Model prediction, not evidence")
    if entity.metadata.get("codex_summary") or entity.entity_type == "codex_summary":
        badges.append("Codex graph summary, assistant output")
    if not badges:
        return ""
    return " ".join(_graph_badge(label, css="dry-run") for label in badges)


def _graph_relation_table(
    project_id: str,
    graph: KnowledgeGraph,
    relations: list[Any],
) -> str:
    entities = graph.entity_map()
    return _table(
        [
            "Subject",
            "Predicate",
            "Object",
            "Relation type",
            "Confidence",
            "Direction",
            "Provenance links",
            "Labels",
        ],
        [
            [
                _graph_entity_ref(project_id, entities.get(relation.subject_entity_id)),
                relation.predicate,
                _graph_entity_ref(project_id, entities.get(relation.object_entity_id)),
                _graph_relation_type_badge(relation.relation_type),
                relation.confidence,
                relation.direction or "",
                _graph_provenance_links(project_id, relation),
                _graph_relation_badges(relation),
            ]
            for relation in relations
        ],
    )


def _graph_entity_ref(project_id: str, entity: GraphEntity | None) -> str:
    if entity is None:
        return ""
    return _graph_entity_link(project_id, entity)


def _graph_relation_type_badge(relation_type: str) -> str:
    if relation_type == "inferred":
        return _graph_badge("Inferred relation", css="dry-run")
    if relation_type == "model_prediction":
        return _graph_badge("Prediction only", css="dry-run")
    if relation_type == "experimental":
        return _graph_badge("Experimental record", css="live")
    if relation_type == "evidence_backed":
        return _graph_badge("Source-backed relation", css="live")
    return _graph_badge(relation_type.replace("_", " "), css="dry-run")


def _graph_relation_badges(relation: Any) -> str:
    badges: list[str] = []
    if relation.relation_type == "inferred":
        badges.append("Inferred edge: graph hypothesis, not EvidenceItem")
    if relation.relation_type == "model_prediction" or relation.predicate == "predicted_by_model":
        badges.append("Model prediction distinct from evidence")
    if relation.metadata.get("codex_summary") or relation.metadata.get("assistant_output"):
        badges.append("Codex graph summary, assistant output")
    if relation.predicate in {"contradicts", "stale_due_to"}:
        badges.append("Advisory review signal")
    return " ".join(_graph_badge(label, css="dry-run") for label in badges)


def _graph_provenance_links(project_id: str, relation: Any) -> str:
    links = [
        _link(
            f"/dashboard/projects/{quote(project_id)}/artifacts/{quote(artifact_id)}/download",
            artifact_id,
        )
        for artifact_id in relation.source_artifact_ids
    ]
    labels = [
        _h(record_id) for record_id in [*relation.source_record_ids, *relation.evidence_item_ids]
    ]
    if not links and not labels and relation.relation_type == "inferred":
        return _graph_badge("explicit inferred label; no evidence created", css="dry-run")
    return ", ".join([*links, *labels])


def _graph_finding_table(findings: list[Any]) -> str:
    return _table(
        ["Finding", "Status", "Reason"],
        [[finding.name, finding.status, finding.reason] for finding in findings],
    )


def _graph_codex_summary_table(graph: KnowledgeGraph) -> str:
    rows: list[list[Any]] = []
    for provenance in graph.provenance:
        if provenance.source_type == "codex_summary":
            rows.append(
                [
                    provenance.provenance_id,
                    "Codex graph summary, assistant output",
                    provenance.source_artifact_id or "",
                    provenance.source_record_id or "",
                    provenance.transformation,
                ]
            )
    for entity in graph.entities:
        if entity.metadata.get("codex_summary") or entity.entity_type == "codex_summary":
            rows.append(
                [
                    entity.entity_id,
                    "Codex graph summary, assistant output",
                    ", ".join(entity.source_artifact_ids),
                    "",
                    entity.name,
                ]
            )
    return _table(
        ["Record", "Label", "Source artifact", "Source record", "Summary"],
        rows,
    )


def _run_graph_dashboard_query(graph: KnowledgeGraph, query_name: str) -> list[Any]:
    reasoner = GraphReasoner(graph)
    if query_name == "generated_molecules_without_direct_evidence":
        return reasoner.generated_molecules_without_direct_evidence()
    if query_name == "candidates_with_contradictory_evidence":
        return reasoner.candidates_with_contradictory_evidence()
    if query_name == "scaffolds_with_positive_assay_history":
        return reasoner.scaffolds_with_positive_assay_history()
    if query_name == "targets_with_repeated_developability_failures":
        return reasoner.targets_with_repeated_developability_failures()
    if query_name == "mechanisms_supported_across_programs":
        return reasoner.mechanisms_supported_across_programs()
    if query_name == "molecules_with_safety_concerns_across_programs":
        return reasoner.molecules_with_safety_concerns_across_programs()
    if query_name == "portfolios_reusing_same_scaffold_risk":
        return reasoner.portfolios_reusing_same_scaffold_risk()
    if query_name == "projects_with_stale_model_predictions":
        return reasoner.projects_with_stale_model_predictions()
    return reasoner.generated_molecules_without_direct_evidence()


def _graph_dashboard_queries() -> list[str]:
    return [
        "generated_molecules_without_direct_evidence",
        "candidates_with_contradictory_evidence",
        "scaffolds_with_positive_assay_history",
        "targets_with_repeated_developability_failures",
        "mechanisms_supported_across_programs",
        "molecules_with_safety_concerns_across_programs",
        "portfolios_reusing_same_scaffold_risk",
        "projects_with_stale_model_predictions",
    ]


def _graph_badge(label: str, *, css: str) -> str:
    return f"<span class=\"mode-badge {css}\">{_h(label)}</span>"


def _portfolio_run_snapshots(workspace: ProjectWorkspace) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for run in workspace.runs:
        dashboard_run = load_dashboard_run(workspace, run.run_id)
        if dashboard_run is None:
            continue
        disease_name = getattr(getattr(dashboard_run, "disease", None), "canonical_name", None)
        snapshots.append(
            {
                "run_id": run.run_id,
                "disease": disease_name or "unknown",
                "candidate_count": len(dashboard_run.candidates),
                "generated_candidate_count": len(dashboard_run.generated_molecules),
            }
        )
    return snapshots


def _portfolio_artifact_table(
    workspace: ProjectWorkspace,
    database: PlatformDatabase,
    artifact_hint: str,
) -> str:
    rows: list[list[Any]] = [
        [
            artifact.artifact_id,
            artifact.artifact_type,
            artifact.run_id or "",
            _link(
                f"/dashboard/projects/{quote(workspace.workspace_id)}/artifacts/"
                f"{quote(artifact.artifact_id)}/download",
                "Download",
            ),
        ]
        for artifact in workspace.artifacts
        if _portfolio_artifact_matches(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            artifact_hint=artifact_hint,
        )
    ]
    rows.extend(
        [
            artifact["artifact_id"],
            artifact["artifact_type"],
            artifact["run_id"],
            _link(
                f"/dashboard/projects/{quote(workspace.workspace_id)}/artifacts/"
                f"{quote(artifact['artifact_id'])}/download",
                "Download",
            ),
        ]
        for artifact in _platform_portfolio_artifacts(
            database,
            project_id=workspace.workspace_id,
            artifact_hint=artifact_hint,
        )
    )
    if not rows:
        return "<p>No matching portfolio artifacts are available yet.</p>"
    return _table(["Artifact", "Type", "Run", "Action"], rows)


def _platform_portfolio_artifacts(
    database: PlatformDatabase,
    *,
    project_id: str,
    artifact_hint: str,
) -> list[dict[str, str]]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(artifact_records)
                .where(artifact_records.c.project_id == project_id)
                .order_by(artifact_records.c.created_at.desc())
            )
            .mappings()
            .all()
        )
    artifacts: list[dict[str, str]] = []
    for row in rows:
        artifact_id = str(row["artifact_id"])
        artifact_type = str(row["artifact_type"])
        path = str(row["path"])
        if not _portfolio_artifact_matches(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_hint=artifact_hint,
        ):
            continue
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "run_id": str(row["run_id"] or ""),
                "path": path,
            }
        )
    return artifacts


def _evaluation_dashboard_html(
    request: Request,
    *,
    user: UserAccount,
    workspace: ProjectWorkspace,
    database: PlatformDatabase,
    section: str,
) -> Response:
    del request, user
    quoted = quote(workspace.workspace_id)
    sections = {
        "overview": "Evaluation overview",
        "benchmark-suites": "Benchmark suites",
        "benchmark-tasks": "Benchmark tasks",
        "prospective-validation-runs": "Prospective validation runs",
        "frozen-prediction-sets": "Frozen prediction sets",
        "guardrail-benchmark-reports": "Guardrail benchmark reports",
        "reproducibility-reports": "Reproducibility reports",
        "longitudinal-trends": "Longitudinal trends",
        "decision-quality": "Decision quality",
    }
    title = sections.get(section, "Evaluation overview")
    nav = " ".join(
        _link(f"/dashboard/projects/{quoted}/evaluation/{slug}", label)
        if slug != "overview"
        else _link(f"/dashboard/projects/{quoted}/evaluation", label)
        for slug, label in sections.items()
    )
    artifacts = _evaluation_artifacts(database, project_id=workspace.workspace_id)
    artifact_rows = [
        [
            artifact["artifact_id"],
            artifact["artifact_type"],
            _link(
                f"/dashboard/projects/{quoted}/artifacts/"
                f"{quote(artifact['artifact_id'])}/download",
                "Download",
            ),
        ]
        for artifact in artifacts
    ]
    if not artifact_rows:
        artifact_rows = [["No evaluation artifacts are available yet.", "", ""]]
    body = (
        f"<p>{_link(f'/dashboard/projects/{quoted}', 'Project')} "
        f"{_link(f'/dashboard/projects/{quoted}/evaluation', 'Evaluation')}</p>"
        "<p class=\"notice\">Evaluation reports are not evidence. Benchmark results are "
        "evaluation artifacts, not clinical validation or proof of efficacy, safety, "
        "activity, or synthesizability.</p>"
        "<p class=\"notice\">Only authorized users can view imported outcomes.</p>"
        f"<nav class=\"nav\">{nav}</nav>"
        f"{_evaluation_section_summary(section)}"
        + _table(["Artifact", "Type", "Download"], artifact_rows)
    )
    return _evaluation_dashboard_shell(title, workspace, body)


def _evaluation_dashboard_shell(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = quote(workspace.workspace_id)
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/evaluation', 'Evaluation')}"
        f"{_link(f'/dashboard/projects/{project_id}/campaigns', 'Campaigns')}"
        "</nav></div></header><main class=\"content\">"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(workspace.name)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _evaluation_section_summary(section: str) -> str:
    summaries = {
        "overview": "Evaluation overview for benchmark and validation artifacts.",
        "benchmark-suites": "Benchmark suites organize frozen evaluation tasks.",
        "benchmark-tasks": "Benchmark tasks define objectives, inputs, labels, and metrics.",
        "prospective-validation-runs": (
            "Prospective validation runs show freeze/outcome status for authorized users."
        ),
        "frozen-prediction-sets": "Frozen prediction sets are immutable after creation.",
        "guardrail-benchmark-reports": "Guardrail benchmark reports surface failures.",
        "reproducibility-reports": "Reproducibility reports summarize hashes and seeds.",
        "longitudinal-trends": "Longitudinal trends compare metrics across versions.",
        "decision-quality": "Decision quality summarizes selections and outcomes.",
    }
    return f"<section><h2>{_h(summaries.get(section, summaries['overview']))}</h2></section>"


def _evaluation_artifacts(
    database: PlatformDatabase,
    *,
    project_id: str,
) -> list[dict[str, str]]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(artifact_records)
                .where(artifact_records.c.project_id == project_id)
                .order_by(artifact_records.c.created_at.desc())
            )
            .mappings()
            .all()
        )
    artifacts: list[dict[str, str]] = []
    for row in rows:
        artifact_type = str(row["artifact_type"])
        artifact_id = str(row["artifact_id"])
        if not _evaluation_artifact_matches(artifact_id, artifact_type):
            continue
        artifacts.append({"artifact_id": artifact_id, "artifact_type": artifact_type})
    return artifacts


def _evaluation_artifact_matches(artifact_id: str, artifact_type: str) -> bool:
    haystack = f"{artifact_id} {artifact_type}".lower()
    return any(
        marker in haystack
        for marker in (
            "evaluation",
            "benchmark",
            "prospective",
            "frozen_prediction",
            "guardrail",
            "reproducibility",
            "trend",
            "decision_quality",
        )
    )


def _portfolio_artifact_matches(
    *,
    artifact_id: str,
    artifact_type: str,
    artifact_hint: str,
) -> bool:
    aliases = {
        "portfolio_optimization": {"portfolio_optimize", "optimization"},
        "scenario": {"portfolio_scenario_analysis"},
        "selection": {"portfolio_optimize", "portfolio_optimization"},
        "portfolio_batch": {"portfolio_batch_build"},
        "program_decision_memo": {"portfolio_memo"},
    }
    tokens = {artifact_hint, *aliases.get(artifact_hint, set())}
    return any(token in artifact_type or token in artifact_id for token in tokens)


def _platform_artifact_by_id(
    database: PlatformDatabase,
    *,
    project_id: str,
    artifact_id: str,
) -> dict[str, str] | None:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(artifact_records).where(
                    artifact_records.c.project_id == project_id,
                    artifact_records.c.artifact_id == artifact_id,
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return {
        "artifact_id": str(row["artifact_id"]),
        "type": str(row["artifact_type"]),
        "path": str(row["path"]),
    }


def _portfolio_audit_table(
    database: PlatformDatabase,
    project_id: str,
    event_hint: str,
) -> str:
    events = [
        event
        for event in database.list_audit_events(project_id=project_id, limit=100)
        if event_hint in event.event_type
    ]
    if not events:
        return "<p>No matching portfolio audit events are available yet.</p>"
    return _table(
        ["Event", "Actor", "Summary", "Created"],
        [
            [
                event.event_type,
                event.actor_user_id or "",
                event.summary,
                event.created_at.isoformat(),
            ]
            for event in events
        ],
    )


def _require_portfolio_read(
    database: PlatformDatabase,
    user: UserAccount,
    *,
    project_id: str,
) -> None:
    if user.is_admin:
        return
    if not has_permission(user, "portfolio:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Portfolio permission denied.")


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


def _hypothesis_workspace(
    database: PlatformDatabase,
    user: UserAccount,
    store: ProjectWorkspaceStore,
    project_id: str,
) -> tuple[ProjectWorkspace, Any]:
    from molecule_ranker.hypotheses.store import HypothesisStore

    workspace = _project_or_404(store=store, project_id=project_id)
    if not has_permission(user, "hypothesis:read", project_id=project_id, database=database):
        raise HTTPException(status_code=403, detail="Hypothesis permission denied.")
    return workspace, HypothesisStore(_hypothesis_store_path(database.root_dir, project_id))


def _hypothesis_store_path(root_dir: Path, project_id: str) -> Path:
    return root_dir / ".molecule-ranker" / "hypotheses" / project_id / "hypotheses.sqlite"


def _hypothesis_dashboard_html(
    title: str,
    workspace: ProjectWorkspace,
    body: str,
) -> HTMLResponse:
    project_id = quote(workspace.workspace_id)
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.1.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.1</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        f"{_link('/dashboard', 'Projects')}"
        f"{_link(f'/dashboard/projects/{project_id}', 'Project')}"
        f"{_link(f'/dashboard/projects/{project_id}/hypotheses', 'Hypotheses')}"
        f"{_link(f'/dashboard/projects/{project_id}/knowledge-graph', 'Knowledge Graph')}"
        f"{_link(f'/dashboard/projects/{project_id}/review', 'Review')}"
        "</nav></div></header><main class=\"content\">"
        f"<header class=\"page-heading\"><h1>{_h(title)}</h1>"
        f"<p class=\"muted\">Project: {_h(workspace.name)}</p></header>"
        f"{body}</main></div></body></html>\n"
    )
    return HTMLResponse(html)


def _hypothesis_nav(project_id: str) -> str:
    quoted = quote(project_id)
    links = {
        "Hypothesis overview": f"/dashboard/projects/{quoted}/hypotheses",
        "Evidence gaps": f"/dashboard/projects/{quoted}/hypotheses/evidence-gaps",
        "Research questions": f"/dashboard/projects/{quoted}/hypotheses/research-questions",
        "Falsification criteria": (
            f"/dashboard/projects/{quoted}/hypotheses/falsification-criteria"
        ),
        "Contradictions": f"/dashboard/projects/{quoted}/hypotheses/contradictions",
        "Review queue": f"/dashboard/projects/{quoted}/hypotheses/review-queue",
        "Lifecycle timeline": f"/dashboard/projects/{quoted}/hypotheses/lifecycle",
    }
    return "<nav class=\"section\">" + " ".join(
        _link(href, label) for label, href in links.items()
    ) + "</nav>"


def _hypothesis_boundary_notice() -> str:
    return (
        "<aside class=\"research-disclaimer\">Hypotheses are not evidence. Research "
        "questions are not lab protocols. Validation plans are not experimental "
        "procedures. No synthesis instructions, lab protocols, dosing, or clinical "
        "claims are provided. Codex-drafted wording requires deterministic validation.</aside>"
    )


def _hypothesis_table(project_id: str, hypotheses: list[Any]) -> str:
    return _table(
        ["Hypothesis", "Type", "Status", "Priority", "Confidence", "Warnings"],
        [
            [
                _hypothesis_link(project_id, hypothesis.hypothesis_id),
                hypothesis.hypothesis_type,
                hypothesis.status,
                f"{hypothesis.priority_score:.3f}",
                f"{hypothesis.confidence:.3f}",
                _hypothesis_warning_labels(hypothesis),
            ]
            for hypothesis in hypotheses
        ],
    )


def _hypothesis_link(project_id: str, hypothesis_id: str) -> str:
    return _link(
        f"/dashboard/projects/{quote(project_id)}/hypotheses/{quote(hypothesis_id, safe='')}",
        hypothesis_id,
    )


def _hypothesis_warning_labels(hypothesis: Any) -> str:
    warnings = list(hypothesis.warnings)
    if _requires_generated_review(hypothesis):
        warnings.append("Generated hypothesis warning visible: human review required")
    if hypothesis.metadata.get("codex_draft"):
        warnings.append("Codex draft deterministically validated or rejected")
    if hypothesis.metadata.get("hypothesis_is_not_evidence") or hypothesis.metadata.get(
        "not_evidence"
    ):
        warnings.append("not evidence")
    return "; ".join(warnings)


def _hypothesis_generated_warning(hypotheses: list[Any]) -> str:
    if not any(_requires_generated_review(hypothesis) for hypothesis in hypotheses):
        return ""
    return (
        "<p class=\"warning\"><strong>Generated hypothesis warning visible:</strong> "
        "generated-molecule hypotheses remain computational hypotheses and require human "
        "review before follow-up planning.</p>"
    )


def _requires_generated_review(hypothesis: Any) -> bool:
    if hypothesis.hypothesis_type != "generated_molecule":
        return False
    ranking = hypothesis.metadata.get("ranking")
    if isinstance(ranking, dict) and ranking.get("requires_review_before_follow_up") is True:
        return True
    return not hypothesis.review_decision_ids or hypothesis.status in {"proposed", "under_review"}


def _project_hypothesis_children(
    records: list[Any],
    hypothesis_store: Any,
    project_id: str,
) -> list[Any]:
    allowed_ids = {
        hypothesis.hypothesis_id
        for hypothesis in hypothesis_store.list_hypotheses(project_id=project_id)
    }
    return [record for record in records if record.hypothesis_id in allowed_ids]


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ": "))


def _admin_role_matrix(source: dict[str, set[str]]) -> list[dict[str, Any]]:
    return [
        {"role": role, "permissions": sorted(permissions)}
        for role, permissions in sorted(source.items())
    ]


def _admin_project_permissions(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(project_permissions).order_by(
                    project_permissions.c.project_id,
                    project_permissions.c.principal_type,
                    project_permissions.c.principal_id,
                )
            )
            .mappings()
            .fetchall()
        )
    return [redact_for_log(dict(row)) for row in rows]


def _admin_audit_events(database: PlatformDatabase) -> list[dict[str, Any]]:
    return [
        {
            **event.model_dump(),
            "summary": redact_for_log(event.summary),
            "metadata": redact_for_log(event.metadata),
        }
        for event in database.list_audit_events(limit=100)
    ]


def _query_int(request: Request, name: str, *, default: int) -> int:
    try:
        return int(str(request.query_params.get(name, default)))
    except (TypeError, ValueError):
        return default


def _project_or_404(*, store: ProjectWorkspaceStore, project_id: str) -> ProjectWorkspace:
    workspace = load_project(store=store, project_id=project_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return workspace


def _agent_store(request: Request) -> RuntimeAgentHostedStore:
    return RuntimeAgentHostedStore(request.app.state.root_dir)


def _agent_session_or_404(store: RuntimeAgentHostedStore, session_id: str) -> Any:
    try:
        return store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent session not found.") from exc


def _agent_session_project(store: RuntimeAgentHostedStore, session_id: str) -> str | None:
    try:
        return store.get_session(session_id).project_id
    except KeyError:
        return None


def _can_view_agent_session(
    database: PlatformDatabase,
    user: UserAccount,
    project_id: str | None,
) -> bool:
    return has_permission(user, "agent:read", project_id=project_id, database=database)


def _agent_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>"
        + escape(title)
        + " · molecule-ranker</title>"
        + '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.1.0-dashboard-1">'
        + "</head><body><div class=\"shell\"><header class=\"topbar\">"
        + "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.1</div>"
        + "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/agent/sessions", "Agent sessions")
        + _link("/dashboard/agent/approvals", "Approval queue")
        + _link("/dashboard/agent/audit", "Runtime audit")
        + _link("/dashboard/admin", "Admin")
        + "</nav></div></header><main class=\"content\">"
        + "<aside class=\"research-disclaimer\">Internal research use only. "
        + "Codex runtime actions are orchestrated tool calls and are not biomedical "
        + "evidence. Risky actions require approval.</aside>"
        + "<header class=\"page-heading\"><h1>"
        + escape(title)
        + "</h1></header>"
        + body
        + "</main></div></body></html>"
    )


def _agent_nav() -> str:
    return (
        "<nav class=\"section\">"
        + _link("/dashboard/agent/sessions", "Agent sessions")
        + _link("/dashboard/agent/approvals", "Approval queue")
        + _link("/dashboard/agent/audit", "Runtime audit log")
        + "</nav>"
    )


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


def _is_pose_artifact(artifact_type: str) -> bool:
    normalized = artifact_type.lower().replace("-", "_")
    return normalized in {"docking_pose", "pose_file", "structure_pose"} or (
        "pose" in normalized and "docking" in normalized
    )


def _is_hypothesis_artifact(artifact_type: str) -> bool:
    return "hypothesis" in artifact_type.lower().replace("-", "_")


def _mode_badge(mode: Any) -> str:
    normalized = str(mode or "dry_run")
    label = normalized.replace("_", " ")
    css = normalized.replace("_", "-")
    return f"<span class=\"mode-badge {css}\">{_h(label)}</span>"


def _h(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


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
