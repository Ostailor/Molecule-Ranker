from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import parse_qs, quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette import status

from molecule_ranker.agent_governance.capability_grants import (
    CapabilityGrantManager,
    CapabilityGrantStore,
)
from molecule_ranker.agent_governance.certification import (
    AgentCertificationManager,
    AgentCertificationStore,
)
from molecule_ranker.agent_governance.incidents import AgentIncidentManager, IncidentStore
from molecule_ranker.agent_governance.run_control import (
    AgentRunControlManager,
    RunControlStore,
)
from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentGovernancePolicy,
    AgentGovernanceReport,
    AgentRiskProfile,
)
from molecule_ranker.agent_governance.simulator import (
    AgentPolicySimulationRequest,
    simulate_agent_action,
)
from molecule_ranker.agent_repair.hosted import RepairHostedStore
from molecule_ranker.autonomy_validation.dashboard import (
    V3ReadinessDashboardSnapshot,
    build_v3_readiness_dashboard_snapshot,
)
from molecule_ranker.campaigns import CampaignPlan, CampaignStore
from molecule_ranker.codex_backbone.guardrails import redact_secrets
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
from molecule_ranker.subagents.hosted import SubagentHostedStore
from molecule_ranker.subagents.messaging import check_message_safety
from molecule_ranker.tool_ecosystem.dashboard import (
    ToolDashboardSnapshot,
    approval_for_package,
    codex_visible_tools,
    dashboard_snapshot,
    manifest_for_package,
    package_by_id,
    sanitize_for_dashboard,
    scan_for_package,
    state_for_package,
    tool_package_for_tool,
    usage_analytics_for_package,
)
from molecule_ranker.utils.json_io import JsonArtifactTooLargeError, load_json_file
from molecule_ranker.utils.pagination import normalize_limit_offset
from molecule_ranker.v3.product_contract import get_v3_product_contract
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
SUBAGENT_SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk|pk|token|secret|api[_-]?key)[-_][A-Za-z0-9._-]+\b",
    re.I,
)


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


@router.get("/dashboard/v3", response_class=HTMLResponse)
def v3_dashboard_home_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_dashboard_permission(database, user, "v3_dashboard:read")
    contract = get_v3_product_contract()
    readiness = _v3_readiness_dashboard_snapshot(request)
    body = (
        "<section class=\"section\" id=\"start-new-discovery-workflow\">"
        + "<h2>Start new discovery workflow</h2>"
        + "<p class=\"muted\">Configure a governed V3 full_discovery_loop run. "
        + "Defaults keep generation, antibody generation, external writes, and campaign "
        + "activation off until a human explicitly approves them.</p>"
        + _v3_discovery_wizard_html()
        + "</section>"
        + "<section class=\"section\" id=\"recent-discovery-workflows\">"
        + "<h2>Recent discovery workflows</h2>"
        + _table(
            ["Workflow", "Mode", "Status", "Human gate"],
            [
                [
                    "parkinson-v3-demo",
                    _mode_badge(contract.default_mode),
                    "certified bundle ready",
                    "generated hypothesis review required",
                ],
                [
                    "full_discovery_loop template",
                    _mode_badge("dry_run"),
                    "ready to run",
                    "approval required before external writes",
                ],
            ],
            empty_message="No V3 discovery workflows yet.",
        )
        + "</section>"
        + "<section class=\"section\" id=\"workflow-status\">"
        + "<h2>Workflow status</h2>"
        + _definition_list(
            {
                "Default workflow": contract.default_workflow,
                "Default mode": contract.default_mode,
                "Default Codex autonomy": contract.default_codex_autonomy,
                "External writes": "disabled by default",
                "Campaign activation": "disabled until human approval",
            }
        )
        + "</section>"
        + "<section class=\"section\" id=\"result-bundles\">"
        + "<h2>Result bundles</h2>"
        + _table(
            ["Artifact", "Role", "Validation boundary"],
            [
                [
                    "v3_result_bundle.json",
                    "machine-readable result bundle",
                    "research-planning result, not biomedical evidence",
                ],
                [
                    "v3_result_bundle.md",
                    "human-readable result bundle",
                    "not clinical validation",
                ],
                [
                    "v3_result_bundle.zip",
                    "portable artifact package",
                    "includes product contract and lineage",
                ],
                [
                    "v3_result_certification.json",
                    "platform/workflow certification",
                    "blocks success when failed",
                ],
            ],
        )
        + "</section>"
        + _v3_dashboard_status_sections(readiness)
    )
    return _v3_dashboard_html("V3 discovery operating system", body)


@router.get("/dashboard/e2e", response_class=HTMLResponse)
def e2e_workflow_list_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    """Render hosted V2.9 end-to-end workflow list."""
    _require_e2e_dashboard_permission(database, user, "e2e:read")
    rows = [
        [
            _link(f"/dashboard/e2e/{record.workflow_id}", record.workflow_id),
            record.result.workflow.workflow_type,
            _mode_badge(record.result.workflow.mode),
            record.result.workflow.status,
            record.result.workflow.disease_name or "",
            record.result.external_writes_performed,
            "pass" if record.validation.passed else "fail",
        ]
        for record in _e2e_dashboard_store(request).list()
    ]
    body = (
        _e2e_nav()
        + "<h2>E2E workflow list</h2>"
        + "<p>Hosted V2.9 governed discovery workflows are dry-run/read-only by default. "
        + "External writes and generated advancement require explicit approval.</p>"
        + _table(
            [
                "Workflow",
                "Type",
                "Mode",
                "Status",
                "Disease",
                "External writes",
                "Validation",
            ],
            rows,
            empty_message="No end-to-end workflows have been run yet.",
        )
    )
    return _e2e_html("E2E workflows", body)


@router.get("/dashboard/e2e/{workflow_id}", response_class=HTMLResponse)
def e2e_workflow_detail_dashboard_page(
    workflow_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    """Render hosted V2.9 end-to-end workflow detail."""
    _require_e2e_dashboard_permission(database, user, "e2e:read")
    record = _e2e_dashboard_record_or_404(request, workflow_id)
    workflow = record.result.workflow
    body = (
        _e2e_nav()
        + f"<h2>E2E workflow detail: {_h(workflow.workflow_id)}</h2>"
        + _definition_list(
            {
                "Workflow type": workflow.workflow_type,
                "Status": workflow.status,
                "Mode": workflow.mode,
                "Project": workflow.project_id or "",
                "Disease": workflow.disease_name or "",
                "Autonomy": workflow.autonomy_level,
                "External writes performed": record.result.external_writes_performed,
                "Planned external writes": record.result.planned_external_writes,
            }
        )
        + "<h2>Step timeline</h2>"
        + _e2e_step_timeline(record)
        + "<h2>Approvals</h2>"
        + _e2e_approval_summary(record)
        + "<h2>Lineage</h2>"
        + _e2e_lineage_table(record)
        + "<h2>Result bundle</h2>"
        + _e2e_bundle_summary(record)
        + "<h2>Validation report</h2>"
        + _e2e_validation_summary(record)
        + "<h2>External sync summary</h2>"
        + _e2e_external_sync_summary(record)
        + "<h2>Partial success/failure remediation</h2>"
        + _e2e_remediation_summary(record)
    )
    return _e2e_html("E2E workflow detail", body)


@router.get("/dashboard/v3-readiness", response_class=HTMLResponse)
def v3_readiness_overview_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    report = snapshot.readiness_report
    body = (
        _v3_readiness_nav()
        + _v3_readiness_escape_banner(snapshot)
        + "<h2>V3 readiness overview</h2>"
        + _definition_list(
            {
                "Version": snapshot.version,
                "Readiness status": report.overall_status,
                "Passed scenarios": report.passed_scenarios,
                "Failed scenarios": report.failed_scenarios,
                "Boundary tests passed": report.boundary_tests_passed,
                "Boundary tests failed": report.boundary_tests_failed,
                "Unsafe escapes": snapshot.metrics["unsafe_escape_count"],
                "Status source": "computed from validation artifacts",
            }
        )
        + "<p class=\"notice\"><strong>Readiness status is immutable here.</strong> "
        + "Manual status changes require v3_readiness:admin and are audit-logged; "
        + "Codex can summarize this dashboard but cannot change readiness status.</p>"
        + _v3_readiness_summary_cards(snapshot)
    )
    return _v3_readiness_html("V3 readiness overview", body)


@router.post("/dashboard/v3-readiness/run", response_class=HTMLResponse)
async def v3_readiness_run_dashboard_action(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:run")
    if not _csrf_token_valid(request, await request.body()):
        raise HTTPException(status_code=403, detail="CSRF token required.")
    request.app.state.v3_readiness_dashboard_snapshot = (
        build_v3_readiness_dashboard_snapshot()
    )
    database.write_audit(
        "v3_readiness_run",
        actor_user_id=user.user_id,
        summary="Regenerated hosted V3 readiness dashboard snapshot.",
        object_type="v3_readiness",
        object_id="dashboard_snapshot",
        metadata={"permission": "v3_readiness:run"},
    )
    return RedirectResponse(
        "/dashboard/v3-readiness",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/v3-readiness/status", response_class=HTMLResponse)
async def v3_readiness_status_dashboard_action(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:admin")
    body = await request.body()
    if not _csrf_token_valid(request, body):
        raise HTTPException(status_code=403, detail="CSRF token required.")
    form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    requested_status = str((form.get("status") or [""])[0])
    database.write_audit(
        "v3_readiness_manual_status_change_attempt",
        actor_user_id=user.user_id,
        summary="Admin attempted V3 readiness status change; status remained computed.",
        object_type="v3_readiness",
        object_id="readiness_status",
        metadata={
            "requested_status": requested_status,
            "status_changed": False,
            "reason": "readiness_status_is_computed_from_validation_artifacts",
        },
    )
    snapshot = _v3_readiness_dashboard_snapshot(request)
    body_html = (
        _v3_readiness_nav()
        + "<h2>Readiness status is computed</h2>"
        + "<p class=\"warning\"><strong>Status was not changed.</strong> "
        + "The requested manual status change was audit-logged. V3 readiness status "
        + "must be regenerated from validation artifacts.</p>"
        + _definition_list(
            {
                "Requested status": requested_status,
                "Current computed status": snapshot.readiness_report.overall_status,
                "Codex role": "summarize only; cannot change readiness status",
            }
        )
    )
    return _v3_readiness_html("V3 readiness status immutable", body_html)


@router.get("/dashboard/v3-readiness/runs", response_class=HTMLResponse)
def v3_readiness_runs_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            result.validation_run.scenario_id,
            result.validation_run.status,
            result.validation_run.workflow_id or "",
            ", ".join(result.validation_run.approval_ids),
            len(result.validation_run.artifact_ids),
            ", ".join(str(failure.get("check", "")) for failure in result.validation_run.failures),
        ]
        for result in snapshot.autonomy_validation_runs
    ]
    return _v3_readiness_html(
        "Autonomy validation runs",
        _v3_readiness_nav()
        + _v3_readiness_escape_banner(snapshot)
        + "<h2>Autonomy validation runs</h2>"
        + _table(
            ["Scenario", "Status", "Workflow", "Approval gates", "Artifacts", "Failures"],
            rows,
            empty_message="No autonomy validation runs available.",
        ),
    )


@router.get("/dashboard/v3-readiness/boundaries", response_class=HTMLResponse)
def v3_readiness_boundaries_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = []
    for test in snapshot.boundary_tests:
        prominent = (
            "BOUNDARY FAILURE - unsafe escape cannot be hidden"
            if test.passed is False
            else "blocked/contained"
        )
        rows.append(
            [
                prominent,
                test.boundary_test_id,
                test.boundary_type,
                test.expected_outcome,
                "pass" if test.passed is True else "fail",
                "; ".join(test.findings),
            ]
        )
    return _v3_readiness_html(
        "Boundary test results",
        _v3_readiness_nav()
        + _v3_readiness_escape_banner(snapshot)
        + "<h2>Boundary test results</h2>"
        + (
            "<p class=\"warning\"><strong>Boundary failures and unsafe escapes cannot "
            "be hidden.</strong></p>"
        )
        + _table(
            ["Prominence", "Test", "Boundary type", "Expected outcome", "Status", "Findings"],
            rows,
            empty_message="No boundary tests available.",
        ),
    )


@router.get("/dashboard/v3-readiness/reliability", response_class=HTMLResponse)
def v3_readiness_reliability_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            card.agent_type,
            card.risk_level,
            f"{card.reliability_score:.3f}",
            f"{card.tool_success_rate:.3f}",
            card.guardrail_failures,
            card.policy_violations,
            card.unsafe_action_attempts,
            "; ".join(card.recommendations),
        ]
        for card in snapshot.agent_reliability_scorecards
    ]
    return _v3_readiness_html(
        "Agent reliability scorecards",
        _v3_readiness_nav()
        + "<h2>Agent reliability scorecards</h2>"
        + _table(
            [
                "Agent type",
                "Risk",
                "Reliability score",
                "Tool success",
                "Guardrail failures",
                "Policy violations",
                "Unsafe attempts",
                "Recommendations",
            ],
            rows,
            empty_message="No reliability scorecards available.",
        ),
    )


@router.get("/dashboard/v3-readiness/certifications", response_class=HTMLResponse)
def v3_readiness_certifications_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            certification.certification_id,
            certification.workflow_id,
            certification.scenario_id,
            "yes" if certification.certified else "no",
            certification.certification_level,
            "yes" if certification.lineage_complete else "no",
            "yes" if certification.guardrails_passed else "no",
            "; ".join(certification.findings),
        ]
        for certification in snapshot.result_certifications
    ]
    return _v3_readiness_html(
        "Result certifications",
        _v3_readiness_nav()
        + "<h2>Result certifications</h2>"
        + "<p>Certifications are platform/workflow certifications, not scientific validation.</p>"
        + _table(
            [
                "Certification",
                "Workflow",
                "Scenario",
                "Certified",
                "Level",
                "Lineage",
                "Guardrails",
                "Findings",
            ],
            rows,
            empty_message="No result certifications available.",
        ),
    )


@router.get("/dashboard/v3-readiness/safety-case", response_class=HTMLResponse)
def v3_readiness_safety_case_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            claim.get("claim_id", ""),
            "supported" if claim.get("supported") else "not supported",
            ", ".join(claim.get("supporting_validation_artifacts", [])),
            ", ".join(claim.get("boundary_tests", [])),
            ", ".join(claim.get("residual_risks", [])),
        ]
        for claim in snapshot.safety_case.claims
    ]
    return _v3_readiness_html(
        "Safety case",
        _v3_readiness_nav()
        + "<h2>Safety case</h2>"
        + (
            "<p>This is autonomy/platform safety evidence, not a regulatory safety "
            "case or clinical validation.</p>"
        )
        + _table(
            ["Claim", "Status", "Supporting artifacts", "Boundary tests", "Residual risks"],
            rows,
            empty_message="No safety claims available.",
        ),
    )


@router.get("/dashboard/v3-readiness/residual-risks", response_class=HTMLResponse)
def v3_readiness_residual_risks_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            risk.risk_id,
            risk.risk_type,
            risk.severity,
            risk.likelihood,
            risk.status,
            risk.owner_role or "",
            risk.mitigation,
        ]
        for risk in snapshot.residual_risk_register.risks
    ]
    return _v3_readiness_html(
        "Residual risk register",
        _v3_readiness_nav()
        + "<h2>Residual risk register</h2>"
        + _table(
            ["Risk", "Type", "Severity", "Likelihood", "Status", "Owner", "Mitigation"],
            rows,
            empty_message="No residual risks available.",
        ),
    )


@router.get("/dashboard/v3-readiness/rc-manifest", response_class=HTMLResponse)
def v3_readiness_rc_manifest_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    manifest = snapshot.v3_rc_manifest
    rows = [
        [step.get("step_id", ""), step.get("status", "")]
        for step in manifest.get("steps", [])
        if isinstance(step, dict)
    ]
    return _v3_readiness_html(
        "V3 RC manifest",
        _v3_readiness_nav()
        + "<h2>V3 RC manifest</h2>"
        + _definition_list(
            {
                "Status": manifest.get("status", ""),
                "Readiness status": manifest.get("readiness_status", ""),
                "Unsafe escapes": manifest.get("unsafe_escape_count", 0),
                "Status immutability": manifest.get("status_immutability", ""),
            }
        )
        + _table(["Step", "Status"], rows, empty_message="No RC steps available.")
        + "<h2>Manifest JSON</h2><pre>"
        + _h(_compact_json(manifest))
        + "</pre>",
    )


@router.get("/dashboard/v3-readiness/blockers", response_class=HTMLResponse)
def v3_readiness_blockers_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [[issue] for issue in snapshot.readiness_report.blocking_issues]
    return _v3_readiness_html(
        "Failing blockers",
        _v3_readiness_nav()
        + _v3_readiness_escape_banner(snapshot)
        + "<h2>Failing blockers</h2>"
        + _table(["Blocking issue"], rows, empty_message="No blocking issues."),
    )


@router.get("/dashboard/v3-readiness/required-before-v3", response_class=HTMLResponse)
def v3_readiness_required_before_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [[item] for item in snapshot.readiness_report.required_before_v3]
    return _v3_readiness_html(
        "Required before V3",
        _v3_readiness_nav()
        + "<h2>Required before V3</h2>"
        + _table(["Required item"], rows, empty_message="No required items remain."),
    )


@router.get("/dashboard/v3-readiness/demo-workflows", response_class=HTMLResponse)
def v3_readiness_demo_workflows_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_v3_readiness_dashboard_permission(database, user, "v3_readiness:read")
    snapshot = _v3_readiness_dashboard_snapshot(request)
    rows = [
        [
            result["workflow"],
            result["scenario_id"],
            result["status"],
            result.get("workflow_id") or "",
            ", ".join(result.get("approval_gates", [])),
            len(result.get("artifact_ids", [])),
            "yes" if result.get("certified") is True else "",
        ]
        for result in snapshot.demo_workflow_results
    ]
    return _v3_readiness_html(
        "Demo workflow results",
        _v3_readiness_nav()
        + "<h2>Demo workflow results</h2>"
        + _table(
            [
                "Workflow",
                "Scenario",
                "Status",
                "Workflow ID",
                "Approval gates",
                "Artifacts",
                "Certified",
            ],
            rows,
            empty_message="No demo workflow results available.",
        ),
    )


@router.get("/dashboard/tools", response_class=HTMLResponse)
def tool_marketplace_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    rows = []
    for package in snapshot.packages:
        scan = scan_for_package(snapshot, package)
        state = state_for_package(snapshot, package)
        rows.append(
            [
                _link(
                    _tool_package_href(package.package_id, package.version),
                    package.display_name,
                ),
                package.version,
                _tool_status_badge(package.status),
                _tool_status_badge(scan.risk_level if scan else "not scanned"),
                state.lifecycle_state
                if state
                else package.metadata.get("marketplace_lifecycle", ""),
                "QUARANTINED until scan and approval"
                if package.status != "approved"
                else "Approved for governed catalog",
            ]
        )
    body = (
        _tool_nav()
        + "<h2>Tool marketplace</h2>"
        + "<p>Local/internal registry only. External marketplace network access is disabled.</p>"
        + _table(
            ["Package", "Version", "Status", "Risk", "Lifecycle", "Governance"],
            rows,
            empty_message="No local/internal tool packages installed yet.",
        )
        + "<h2>Codex-visible tools</h2>"
        + _codex_visible_tool_table(snapshot, user, database, request)
    )
    return _tool_dashboard_html("Tool marketplace", body)


@router.get("/dashboard/tools/installed", response_class=HTMLResponse)
def installed_tool_packages_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    rows = [
        [
            _link(_tool_package_href(package.package_id, package.version), package.name),
            package.version,
            _tool_status_badge(package.status),
            state.lifecycle_state if state else "",
            ", ".join(state.enabled_project_ids) if state else "",
            package.tool_count,
            package.skill_count,
            package.workflow_count,
        ]
        for package in snapshot.packages
        if (state := state_for_package(snapshot, package)) is not None
    ]
    return _tool_dashboard_html(
        "Installed packages",
        _tool_nav()
        + _table(
            [
                "Package",
                "Version",
                "Status",
                "Lifecycle",
                "Enabled projects",
                "Tools",
                "Skills",
                "Workflows",
            ],
            rows,
            empty_message="No installed packages.",
        ),
    )


@router.get("/dashboard/tools/packages/{package_id}", response_class=HTMLResponse)
def tool_package_detail_dashboard_page(
    package_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    package = _tool_package_or_404(snapshot, package_id, request)
    manifest = manifest_for_package(snapshot, package)
    scan = scan_for_package(snapshot, package)
    approval = approval_for_package(snapshot, package)
    state = state_for_package(snapshot, package)
    tool_rows = []
    if manifest:
        for tool in manifest.tools:
            tool_rows.append(
                [
                    _link(f"/dashboard/tools/tool/{quote(tool.tool_name)}", tool.tool_name),
                    tool.category,
                    _tool_side_effect_label(tool.side_effect_level),
                    ", ".join(tool.required_permissions),
                    _validator_label(tool.metadata),
                    _codex_visible_label(tool),
                ]
            )
    body = (
        _tool_nav()
        + f"<h2>{_h(package.display_name)}</h2>"
        + _definition_list(
            {
                "Package ID": package.package_id,
                "Version": package.version,
                "Publisher": package.publisher,
                "Status": package.status,
                "Lifecycle": state.lifecycle_state if state else "",
                "Manifest hash": package.manifest_hash,
                "Package hash": package.package_hash or "",
                "Quarantine": "Yes - unapproved packages are not Codex-visible"
                if package.status != "approved"
                else "No",
            }
        )
        + "<h2>Security and approval</h2>"
        + _definition_list(
            {
                "Scan status": scan.status if scan else "not scanned",
                "Risk level": scan.risk_level if scan else "unknown",
                "Approval status": approval.approval_status if approval else "none",
                "Approved permissions": ", ".join(approval.approved_permissions)
                if approval
                else "",
            }
        )
        + "<h2>Tool side effects</h2>"
        + _table(
            ["Tool", "Category", "Side effect", "Permissions", "Validators", "Codex visible"],
            tool_rows,
            empty_message="No tools declared in this manifest.",
        )
        + "<h2>Sanitized manifest</h2>"
        + _safe_json(manifest.model_dump(mode="json") if manifest else {})
    )
    return _tool_dashboard_html("Package detail", body)


@router.get("/dashboard/tools/tool/{tool_name:path}", response_class=HTMLResponse)
def tool_detail_dashboard_page(
    tool_name: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    found = tool_package_for_tool(snapshot, unquote(tool_name))
    if found is None:
        raise HTTPException(status_code=404, detail="Tool not found.")
    package, _manifest, tool = found
    body = (
        _tool_nav()
        + f"<h2>{_h(tool.tool_name)}</h2>"
        + _definition_list(
            {
                "Package": package.package_id,
                "Version": package.version,
                "Category": tool.category,
                "Description": tool.description,
                "Side effects": tool.side_effect_level,
                "External write": "Requires approval"
                if tool.side_effect_level == "external_write"
                else "No",
                "Approval by default": tool.requires_approval_by_default,
                "Required permissions": ", ".join(tool.required_permissions),
                "Policy tags": ", ".join(tool.policy_tags),
                "Validators": ", ".join(_tool_validators(tool.metadata)),
                "Creates": ", ".join(_tool_creates(tool.metadata)),
            }
        )
        + "<h2>Input schema</h2>"
        + _safe_json(tool.input_schema)
        + "<h2>Output schema</h2>"
        + _safe_json(tool.output_schema)
        + "<h2>Sanitized metadata</h2>"
        + _safe_json(tool.metadata)
    )
    return _tool_dashboard_html("Tool detail", body)


@router.get("/dashboard/tools/packages/{package_id}/security", response_class=HTMLResponse)
def tool_security_scan_dashboard_page(
    package_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    package = _tool_package_or_404(snapshot, package_id, request)
    scan = scan_for_package(snapshot, package)
    finding_rows = []
    if scan:
        finding_rows = [
            [
                _tool_status_badge(str(finding.get("severity", ""))),
                finding.get("code", ""),
                finding.get("message", ""),
                json.dumps(sanitize_for_dashboard(finding.get("metadata", {})), sort_keys=True),
            ]
            for finding in scan.findings
        ]
    body = (
        _tool_nav()
        + f"<h2>Security scan result for {_h(package.display_name)}</h2>"
        + _definition_list(
            {
                "Scan status": scan.status if scan else "not scanned",
                "Risk level": scan.risk_level if scan else "unknown",
                "Scanner version": scan.scanner_version if scan else "",
                "Critical findings block approval": "Yes",
            }
        )
        + _table(
            ["Severity", "Finding", "Message", "Sanitized metadata"],
            finding_rows,
            empty_message="No scan findings recorded.",
        )
    )
    return _tool_dashboard_html("Security scan result", body)


@router.get("/dashboard/tools/approvals", response_class=HTMLResponse)
def tool_approval_queue_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:approve")
    snapshot = _tool_dashboard_snapshot(request)
    rows = [
        [
            approval.approval_id,
            _link(
                _tool_package_href(approval.package_id, approval.package_version),
                approval.package_id,
            ),
            approval.package_version,
            _tool_status_badge(approval.approval_status),
            approval.rationale,
            ", ".join(approval.approved_permissions),
        ]
        for approval in snapshot.approvals
    ]
    return _tool_dashboard_html(
        "Approval queue",
        _tool_nav()
        + "<p>Codex cannot approve tools, packages, campaigns, stage gates, "
        + "evidence, or assays.</p>"
        + _table(
            ["Approval", "Package", "Version", "Status", "Rationale", "Permissions"],
            rows,
            empty_message="No packages pending approval.",
        ),
    )


@router.get("/dashboard/tools/packages/{package_id}/usage", response_class=HTMLResponse)
def tool_usage_analytics_dashboard_page(
    package_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    package = _tool_package_or_404(snapshot, package_id, request)
    analytics = usage_analytics_for_package(snapshot, package)
    body = (
        _tool_nav()
        + f"<h2>Usage analytics for {_h(package.display_name)}</h2>"
        + _definition_list(
            {
                "Total invocations": analytics.total_invocations,
                "Artifacts produced": analytics.artifact_count,
                "Warnings": analytics.warning_count,
            }
        )
        + "<h2>Status counts</h2>"
        + _table(
            ["Status", "Count"],
            [[key, value] for key, value in analytics.status_counts.items()],
            empty_message="No usage recorded.",
        )
        + "<h2>Tool counts</h2>"
        + _table(
            ["Tool", "Count"],
            [[key, value] for key, value in analytics.tool_counts.items()],
            empty_message="No tool invocations recorded.",
        )
    )
    return _tool_dashboard_html("Usage analytics", body)


@router.get("/dashboard/tools/project-allowlist", response_class=HTMLResponse)
def project_tool_allowlist_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:enable")
    snapshot = _tool_dashboard_snapshot(request)
    project_id = str(request.query_params.get("project_id") or "workspace-a")
    rows = [
        [
            _link(_tool_package_href(package.package_id, package.version), package.package_id),
            package.version,
            _tool_status_badge(package.status),
            "enabled" if state and project_id in state.enabled_project_ids else "not enabled",
            ", ".join(state.disabled_project_ids) if state else "",
        ]
        for package in snapshot.packages
        for state in [state_for_package(snapshot, package)]
    ]
    return _tool_dashboard_html(
        "Project tool allowlist",
        _tool_nav()
        + f"<h2>Project tool allowlist for {_h(project_id)}</h2>"
        + _table(
            ["Package", "Version", "Status", "Project allowlist", "Disabled projects"],
            rows,
            empty_message="No packages available for this project allowlist yet.",
        ),
    )


@router.get("/dashboard/tools/skills", response_class=HTMLResponse)
def skill_packs_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    rows = [
        [
            pack.name,
            pack.version,
            len(pack.skills),
            ", ".join(pack.required_tools),
            ", ".join(pack.guardrails),
        ]
        for pack in snapshot.skill_packs
    ]
    return _tool_dashboard_html(
        "Skill packs",
        _tool_nav()
        + "<p>Codex may select a skill; deterministic runtime code expands it to a plan.</p>"
        + _table(["Skill pack", "Version", "Skills", "Required tools", "Guardrails"], rows),
    )


@router.get("/dashboard/tools/workflows", response_class=HTMLResponse)
def workflow_templates_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    rows = [
        [
            workflow.name,
            workflow.version,
            workflow.package_id,
            ", ".join(workflow.required_tools),
            ", ".join(workflow.required_permissions),
            ", ".join(workflow.approval_requirements),
            ", ".join(workflow.forbidden_outputs),
        ]
        for workflow in snapshot.workflow_templates
    ]
    return _tool_dashboard_html(
        "Workflow templates",
        _tool_nav()
        + _table(
            [
                "Workflow",
                "Version",
                "Package",
                "Tools",
                "Permissions",
                "Approvals",
                "Forbidden outputs",
            ],
            rows,
            empty_message="No workflow templates installed yet.",
        ),
    )


@router.get("/dashboard/tools/mcp-gateway", response_class=HTMLResponse)
def mcp_gateway_status_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_tool_dashboard_permission(database, user, "tool:read")
    snapshot = _tool_dashboard_snapshot(request)
    visible = codex_visible_tools(
        snapshot,
        user_permissions=_tool_runtime_permissions(snapshot, user),
        project_id=str(request.query_params.get("project_id") or "workspace-a"),
    )
    rows = [
        [
            tool.tool_name,
            tool.category,
            _tool_side_effect_label(tool.side_effect_level),
            ", ".join(tool.required_permissions),
        ]
        for tool in visible
    ]
    return _tool_dashboard_html(
        "MCP gateway status",
        _tool_nav()
        + _definition_list(
            {
                "Gateway": "internal MCP-compatible",
                "Approved tools exposed": len(visible),
                "Secrets exposed": "No",
                "Cache files exposed": "No",
                "External writes": "Approval required",
            }
        )
        + "<h2>Codex-visible tools</h2>"
        + _table(
            ["Tool", "Category", "Side effect", "Permissions"],
            rows,
            empty_message="No Codex-visible tools approved for this project.",
        ),
    )


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


@router.get("/dashboard/agent/reliability", response_class=HTMLResponse)
def agent_reliability_dashboard_page(
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
) -> Response:
    del user
    from molecule_ranker.runtime_agents.repair import run_repair_eval_suite

    eval_result = run_repair_eval_suite()
    metric_rows = [
        [key, f"{value:.2f}"] for key, value in sorted(eval_result.metrics.items())
    ]
    task_rows = [
        [
            result.task_id,
            result.status,
            result.report_status,
            "yes" if result.auto_executed else "no",
            "yes" if result.approval_required else "no",
            "yes" if result.blocked_scientific_repair else "no",
        ]
        for result in eval_result.task_results
    ]
    body = (
        _agent_nav()
        + "<h2>Agent reliability dashboard</h2>"
        + "<p>V2.6 self-evaluation, failure diagnosis, autonomous repair, "
        + "regression checks, and auditable repair reports.</p>"
        + "<p>Agents may repair workflows. Agents may not repair scientific truth "
        + "by inventing missing data.</p>"
        + "<h2>Repair eval metrics</h2>"
        + _table(["Metric", "Value"], metric_rows)
        + "<h2>Repair eval tasks</h2>"
        + _table(
            [
                "Task",
                "Status",
                "Repair status",
                "Auto executed",
                "Approval required",
                "Scientific repair blocked",
            ],
            task_rows,
        )
    )
    return _agent_dashboard_html("Agent reliability", body)


@router.get("/dashboard/governance", response_class=HTMLResponse)
def governance_overview_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    policies = _governance_policies(request)
    grants = _governance_grant_manager(request).list_grants()
    budgets = _governance_budgets(request)
    certifications = _governance_certification_manager(request).list_certifications()
    risk_profiles = _governance_risk_profiles(request)
    incidents = _governance_incident_manager(request).list_incidents()
    controls = _governance_run_control_manager(request).list_controls(active_only=True)
    reports = _governance_reports(request)
    rows = [
        ["Active policies", len([policy for policy in policies if policy.enabled])],
        ["Capability grants", len(grants)],
        ["Autonomy budgets", len(budgets)],
        ["Certifications", len(certifications)],
        ["Agent risk profiles", len(risk_profiles)],
        [
            "Open incidents",
            len([item for item in incidents if item.status not in {"resolved", "false_positive"}]),
        ],
        ["Active run controls", len(controls)],
        ["Governance reports", len(reports)],
    ]
    body = (
        _governance_nav()
        + "<h2>Governance overview</h2>"
        + "<p>Enterprise controls for Codex runtime agents, subagents, tools, "
        + "campaigns, and autonomous actions.</p>"
        + _table(["Area", "Count"], rows)
    )
    return _governance_dashboard_html("Governance overview", body)


@router.get("/dashboard/governance/policies", response_class=HTMLResponse)
def governance_policies_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            policy.policy_id,
            policy.policy_name,
            policy.policy_version,
            policy.max_autonomy_level,
            ", ".join(policy.allowed_tool_categories),
            ", ".join(policy.denied_tool_categories),
            "enabled" if policy.enabled else "disabled",
        ]
        for policy in _governance_policies(request)
    ]
    return _governance_dashboard_html(
        "Active policies",
        _governance_nav()
        + _table(
            [
                "Policy",
                "Name",
                "Version",
                "Autonomy cap",
                "Allowed tools",
                "Denied tools",
                "Status",
            ],
            rows,
            empty_message="No governance policies configured.",
        ),
    )


@router.get("/dashboard/governance/grants", response_class=HTMLResponse)
def governance_grants_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            grant.grant_id,
            grant.agent_id,
            grant.agent_type,
            grant.granted_capability,
            f"{grant.scope_type}:{grant.scope_id or '*'}",
            grant.status,
            grant.granted_by,
        ]
        for grant in _governance_grant_manager(request).list_grants()
    ]
    return _governance_dashboard_html(
        "Capability grants",
        _governance_nav()
        + _table(
            ["Grant", "Agent", "Type", "Capability", "Scope", "Status", "Granted by"],
            rows,
            empty_message="No capability grants recorded.",
        ),
    )


@router.get("/dashboard/governance/budgets", response_class=HTMLResponse)
def governance_budgets_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            budget.budget_id,
            budget.agent_id or budget.campaign_id or budget.project_id or budget.org_id or "*",
            budget.period,
            budget.max_tool_calls if budget.max_tool_calls is not None else "",
            budget.max_external_writes if budget.max_external_writes is not None else 0,
            budget.max_cost_units if budget.max_cost_units is not None else "",
            "enabled" if budget.enabled else "disabled",
        ]
        for budget in _governance_budgets(request)
    ]
    return _governance_dashboard_html(
        "Autonomy budgets",
        _governance_nav()
        + _table(
            [
                "Budget",
                "Scope",
                "Period",
                "Tool calls",
                "External writes",
                "Cost units",
                "Status",
            ],
            rows,
            empty_message="No autonomy budgets configured.",
        ),
    )


@router.get("/dashboard/governance/certifications", response_class=HTMLResponse)
def governance_certifications_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            certification.certification_id,
            certification.agent_id,
            certification.certification_type,
            certification.certified_autonomy_level,
            f"{certification.score:.2f}",
            "passed" if certification.passed else "failed",
            certification.certified_by,
        ]
        for certification in _governance_certification_manager(request).list_certifications()
    ]
    return _governance_dashboard_html(
        "Certifications",
        _governance_nav()
        + _table(
            ["Certification", "Agent", "Type", "Autonomy", "Score", "Status", "Certified by"],
            rows,
            empty_message="No agent certifications recorded.",
        ),
    )


@router.get("/dashboard/governance/risk", response_class=HTMLResponse)
def governance_risk_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            profile.agent_id,
            profile.risk_level,
            ", ".join(profile.risk_factors),
            profile.recent_guardrail_failures,
            profile.recent_policy_violations,
            profile.recent_human_overrides,
            f"{profile.confidence:.2f}",
        ]
        for profile in _governance_risk_profiles(request)
    ]
    return _governance_dashboard_html(
        "Agent risk profiles",
        _governance_nav()
        + _table(
            [
                "Agent",
                "Risk",
                "Factors",
                "Guardrails",
                "Violations",
                "Overrides",
                "Confidence",
            ],
            rows,
            empty_message="No agent risk profiles computed.",
        ),
    )


@router.get("/dashboard/governance/incidents", response_class=HTMLResponse)
def governance_incidents_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            incident.incident_id,
            incident.severity,
            incident.incident_type,
            incident.status,
            _governance_text(incident.summary),
            incident.assigned_to or "",
        ]
        for incident in _governance_incident_manager(request).list_incidents()
    ]
    return _governance_dashboard_html(
        "Incidents",
        _governance_nav()
        + _table(
            ["Incident", "Severity", "Type", "Status", "Summary", "Owner"],
            rows,
            empty_message="No governance incidents opened.",
        ),
    )


@router.get("/dashboard/governance/run-controls", response_class=HTMLResponse)
def governance_run_controls_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = _governance_run_control_rows(
        _governance_run_control_manager(request).list_controls()
    )
    return _governance_dashboard_html(
        "Run controls",
        _governance_nav()
        + _table(
            ["Control", "Type", "Scope", "Reason", "Applied by", "Active"],
            rows,
            empty_message="No run controls recorded.",
        ),
    )


@router.get("/dashboard/governance/kill-switches", response_class=HTMLResponse)
def governance_kill_switches_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    controls = [
        control
        for control in _governance_run_control_manager(request).list_controls(active_only=True)
        if control.control_type == "kill_switch"
    ]
    return _governance_dashboard_html(
        "Kill switches",
        _governance_nav()
        + _table(
            ["Control", "Type", "Scope", "Reason", "Applied by", "Active"],
            _governance_run_control_rows(controls),
            empty_message="No active kill switches.",
        ),
    )


@router.get("/dashboard/governance/reports", response_class=HTMLResponse)
def governance_reports_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    rows = [
        [
            report.report_id,
            report.project_id or report.org_id or "*",
            report.agent_count,
            report.guardrail_failures,
            report.policy_violations,
            report.incidents_opened,
            report.budget_violations,
        ]
        for report in _governance_reports(request)
    ]
    return _governance_dashboard_html(
        "Governance reports",
        _governance_nav()
        + _table(
            [
                "Report",
                "Scope",
                "Agents",
                "Guardrails",
                "Violations",
                "Incidents",
                "Budget violations",
            ],
            rows,
            empty_message="No governance reports saved.",
        ),
    )


@router.get("/dashboard/governance/policy-simulator", response_class=HTMLResponse)
def governance_policy_simulator_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    agent_id: str | None = None,
    tool: str | None = None,
    action: str | None = None,
    agent_type: str = "runtime_agent",
    role: str | None = None,
    autonomy_level: str = "execute_safe_tools",
    tool_category: str | None = None,
    side_effect_level: str | None = None,
    org_id: str | None = None,
    project_id: str | None = None,
    campaign_id: str | None = None,
    budget_tool_calls: int = 0,
    budget_external_writes: int = 0,
    budget_cost_units: float = 0.0,
    generated_molecule_advancement: bool = False,
) -> Response:
    _require_governance_dashboard_permission(database, user, "governance:read")
    policies = _governance_policies(request)
    controls = _governance_run_control_manager(request).list_controls(active_only=True)
    simulation_body = ""
    if agent_id and tool:
        from molecule_ranker.agent_governance.budgets import BudgetImpact

        simulation = simulate_agent_action(
            AgentPolicySimulationRequest(
                agent_id=agent_id,
                agent_type=cast(Any, agent_type),
                role=role,
                autonomy_level=cast(Any, autonomy_level),
                tool=tool,
                action=action,
                tool_category=tool_category,
                side_effect_level=side_effect_level,
                org_id=org_id,
                project_id=project_id,
                campaign_id=campaign_id,
                budget_impact=BudgetImpact(
                    tool_calls=budget_tool_calls,
                    external_writes=budget_external_writes,
                    cost_units=budget_cost_units,
                ),
                budgets=_governance_budgets(request),
                active_policies=policies,
                run_controls=controls,
                metadata={
                    "generated_molecule_advancement": generated_molecule_advancement,
                },
            )
        )
        simulation_body = (
            "<h2>Simulation result</h2>"
            + _table(
                ["Field", "Value"],
                [
                    ["Status", simulation.status],
                    ["Allowed", "yes" if simulation.allowed else "no"],
                    [
                        "Approval required",
                        "yes" if simulation.approval_required else "no",
                    ],
                    ["Required approvals", ", ".join(simulation.required_approvals)],
                    ["Required permissions", ", ".join(simulation.required_permissions)],
                    ["Blocked reasons", "; ".join(simulation.blocked_reasons)],
                ],
            )
            + "<h2>Trace</h2>"
            + _safe_json(simulation.model_dump(mode="json"))
        )
    body = (
        _governance_nav()
        + "<h2>Policy simulator</h2>"
        + _governance_simulator_form(request)
        + _table(
            ["Input", "Current value"],
            [
                ["Enabled policies", len([policy for policy in policies if policy.enabled])],
                ["Active emergency controls", len(controls)],
                [
                    "Autonomy guard",
                    "Codex cannot approve autonomy increases, policy overrides, or self-certify.",
                ],
            ],
        )
        + simulation_body
    )
    return _governance_dashboard_html("Policy simulator", body)


@router.get("/dashboard/repair", response_class=HTMLResponse)
def repair_overview_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    store = _repair_store(request)
    diagnoses = store.list_diagnoses()
    plans = store.list_plans()
    executions = store.list_executions()
    approvals = store.list_approvals()
    pending_approvals = [item for item in approvals if item.get("status") == "pending"]
    body = (
        _repair_nav()
        + "<h2>Repair overview</h2>"
        + "<p>Hosted V2.6 workflow repair. Agents may repair workflows; agents may not "
        + "repair scientific truth by inventing missing data.</p>"
        + _table(
            ["Metric", "Count"],
            [
                ["Diagnoses", len(diagnoses)],
                ["Repair plans", len(plans)],
                ["Executions", len(executions)],
                ["Pending approvals", len(pending_approvals)],
            ],
        )
        + "<h2>Recent executions</h2>"
        + _repair_execution_table(executions)
    )
    return _repair_dashboard_html("Repair overview", body)


@router.get("/dashboard/repair/failed-jobs", response_class=HTMLResponse)
def repair_failed_jobs_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:diagnose")
    diagnoses = _repair_store(request).list_diagnoses()
    rows = [
        [
            _link(f"/dashboard/repair/diagnoses/{quote(item.diagnosis_id)}", item.diagnosis_id),
            item.failure_object_type,
            item.failure_object_id,
            item.failure_category,
            _repair_text(item.root_cause_summary),
        ]
        for item in diagnoses
        if item.failure_object_type in {"job", "tool_call", "workflow", "validation"}
    ]
    return _repair_dashboard_html(
        "Failed jobs needing diagnosis",
        _repair_nav()
        + _table(
            ["Diagnosis", "Object type", "Object ID", "Category", "Summary"],
            rows,
            empty_message="No failed jobs are awaiting diagnosis.",
        ),
    )


@router.get("/dashboard/repair/diagnoses/{diagnosis_id}", response_class=HTMLResponse)
def repair_diagnosis_detail_dashboard_page(
    diagnosis_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    diagnosis = _repair_diagnosis_or_404(_repair_store(request), diagnosis_id)
    body = (
        _repair_nav()
        + "<h2>Diagnosis detail</h2>"
        + _definition_list(
            {
                "Diagnosis ID": diagnosis.diagnosis_id,
                "Object": f"{diagnosis.failure_object_type}:{diagnosis.failure_object_id}",
                "Category": diagnosis.failure_category,
                "Repairability": diagnosis.repairability,
                "Recoverable": diagnosis.recoverable,
                "Confidence": diagnosis.confidence,
                "Summary": _repair_text(diagnosis.root_cause_summary),
            }
        )
        + "<h2>Evidence</h2>"
        + _repair_safe_json(diagnosis.evidence)
    )
    return _repair_dashboard_html("Diagnosis detail", body)


@router.get("/dashboard/repair/plans/{repair_plan_id}", response_class=HTMLResponse)
def repair_plan_detail_dashboard_page(
    repair_plan_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    plan = _repair_plan_or_404(_repair_store(request), repair_plan_id)
    rows = [
        [
            action.repair_action_id,
            action.action_type,
            action.target_object_type,
            action.target_object_id,
            action.side_effect_level,
            action.requires_approval,
            action.risk_level,
        ]
        for action in plan.actions
    ]
    body = (
        _repair_nav()
        + "<h2>Repair plan detail</h2>"
        + _definition_list(
            {
                "Plan ID": plan.repair_plan_id,
                "Diagnosis ID": plan.diagnosis_id,
                "Summary": _repair_text(plan.plan_summary),
                "Requires approval": plan.requires_human_approval,
                "Validated": plan.validated,
            }
        )
        + _table(
            [
                "Action",
                "Type",
                "Target type",
                "Target ID",
                "Side effect",
                "Approval",
                "Risk",
            ],
            rows,
        )
    )
    return _repair_dashboard_html("Repair plan detail", body)


@router.get("/dashboard/repair/approvals", response_class=HTMLResponse)
def repair_approval_queue_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:approve")
    approvals = _repair_store(request).list_approvals()
    return _repair_dashboard_html(
        "Repair approval queue",
        _repair_nav()
        + _table(
            ["Approval", "Plan", "Action", "Status", "Reason"],
            [
                [
                    item.get("approval_id", ""),
                    _link(
                        f"/dashboard/repair/plans/{quote(str(item.get('repair_plan_id', '')))}",
                        item.get("repair_plan_id", ""),
                    ),
                    item.get("repair_action_id", ""),
                    item.get("status", ""),
                    _repair_text(item.get("reason", "")),
                ]
                for item in approvals
            ],
            empty_message="No repair approvals are pending.",
        ),
    )


@router.get("/dashboard/repair/executions", response_class=HTMLResponse)
def repair_execution_list_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    return _repair_dashboard_html(
        "Repair execution timeline",
        _repair_nav() + _repair_execution_table(_repair_store(request).list_executions()),
    )


@router.get("/dashboard/repair/executions/{execution_id}", response_class=HTMLResponse)
def repair_execution_timeline_dashboard_page(
    execution_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    execution = _repair_execution_or_404(_repair_store(request), execution_id)
    body = (
        _repair_nav()
        + "<h2>Repair execution timeline</h2>"
        + _definition_list(
            {
                "Execution ID": execution.repair_execution_id,
                "Plan ID": execution.repair_plan_id,
                "Status": execution.status,
                "Started": execution.started_at,
                "Completed": execution.completed_at or "",
            }
        )
        + "<h2>Actions</h2>"
        + _repair_safe_json(execution.executed_actions)
        + "<h2>Regression result</h2>"
        + _table(
            ["Regression check ID"],
            [[check_id] for check_id in execution.regression_check_ids],
            empty_message="No regression checks are linked to this execution.",
        )
    )
    return _repair_dashboard_html("Repair execution timeline", body)


@router.get("/dashboard/repair/regression-checks", response_class=HTMLResponse)
def repair_regression_checks_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    checks = _repair_store(request).list_regression_checks()
    return _repair_dashboard_html(
        "Regression checks",
        _repair_nav()
        + _table(
            ["Check", "Execution", "Type", "Passed", "Findings"],
            [
                [
                    check.regression_check_id,
                    check.repair_execution_id or "",
                    check.check_type,
                    check.passed,
                    _repair_text("; ".join(check.findings)),
                ]
                for check in checks
            ],
            empty_message="No regression checks have been recorded.",
        ),
    )


@router.get("/dashboard/repair/memory", response_class=HTMLResponse)
def repair_memory_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    memory = _repair_store(request).list_memory()
    return _repair_dashboard_html(
        "Repair memory",
        _repair_nav()
        + _table(
            ["Memory", "Category", "Success rate", "Occurrences", "Strategy"],
            [
                [
                    item.memory_id,
                    item.failure_category,
                    item.repair_success_rate,
                    item.occurrence_count,
                    _repair_text(item.recommended_repair_strategy),
                ]
                for item in memory
            ],
            empty_message="No repair memory has been recorded.",
        ),
    )


@router.get("/dashboard/repair/guardrail-failures", response_class=HTMLResponse)
def repair_guardrail_failures_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    _require_repair_dashboard_permission(database, user, "repair:read")
    rows = [
        [
            _link(f"/dashboard/repair/diagnoses/{quote(item.diagnosis_id)}", item.diagnosis_id),
            item.failure_object_id,
            _repair_text(item.root_cause_summary),
            _repair_text("; ".join(item.warnings)),
        ]
        for item in _repair_store(request).list_diagnoses()
        if item.failure_category in {"guardrail_failed", "unsafe_output"}
        or item.failure_object_type == "guardrail"
    ]
    return _repair_dashboard_html(
        "Guardrail failures",
        _repair_nav()
        + _table(
            ["Diagnosis", "Object", "Summary", "Warnings"],
            rows,
            empty_message="No guardrail repair failures are visible.",
        ),
    )


@router.get("/dashboard/subagents/sessions", response_class=HTMLResponse)
def subagent_sessions_dashboard_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _subagent_store(request)
    sessions = [
        session
        for session in store.list_sessions()
        if _can_view_subagent_session(database, user, _subagent_project_id(session))
    ]
    return _subagent_dashboard_html(
        "Subagent sessions",
        _subagent_nav()
        + _table(
            ["Session", "Project", "Skill", "Status", "Goal"],
            [
                [
                    _link(
                        "/dashboard/subagents/sessions/"
                        + quote(session.multi_agent_session_id),
                        session.multi_agent_session_id,
                    ),
                    _subagent_project_id(session) or "",
                    session.metadata.get("skill_name", ""),
                    session.status,
                    session.user_goal,
                ]
                for session in sessions
            ],
            empty_message="No subagent sessions are visible.",
        ),
    )


@router.get("/dashboard/subagents/sessions/{session_id}", response_class=HTMLResponse)
def subagent_session_detail_dashboard_page(
    session_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    store = _subagent_store(request)
    session = _subagent_session_or_404(store, session_id)
    if not _can_view_subagent_session(database, user, _subagent_project_id(session)):
        raise HTTPException(status_code=403, detail="Subagent permission denied.")
    messages = store.list_messages(session_id)
    results = store.list_results(session_id)
    critiques = store.list_critiques(session_id)
    consensus = store.list_consensus(session_id)
    approval_rows = [
        [
            task.task_id,
            task.assigned_subagent_id,
            task.risk_level,
            "required" if task.requires_human_approval else "",
        ]
        for task in session.tasks
        if task.requires_human_approval
    ]
    guardrail_rows = [
        [
            critique.critique_id,
            critique.critic_subagent_id,
            "passed" if critique.passed else "failed",
            "; ".join(critique.findings),
        ]
        for critique in critiques
        if critique.critique_type == "scientific_guardrail"
        or critique.metadata.get("required_for_high_risk")
        or critique.metadata.get("non_overridable")
    ]
    body = (
        _subagent_nav()
        + f"<h2>Session timeline</h2><p>{_h(session.user_goal)}</p>"
        + _table(
            ["Task", "Subagent", "Status", "Risk", "Dependencies"],
            [
                [
                    task.task_id,
                    task.assigned_subagent_id,
                    task.status,
                    task.risk_level,
                    ", ".join(task.metadata.get("dependencies", [])),
                ]
                for task in session.tasks
            ],
        )
        + "<h2>Task graph</h2>"
        + _table(
            ["From", "To", "Tools", "Artifacts"],
            [
                [
                    ", ".join(task.metadata.get("dependencies", [])) or "root",
                    task.task_id,
                    ", ".join(task.allowed_tool_names),
                    ", ".join(task.input_artifact_ids),
                ]
                for task in session.tasks
            ],
        )
        + "<h2>Subagent messages</h2>"
        + _table(
            ["From", "To", "Type", "Content", "Artifacts"],
            [
                [
                    message.from_subagent_id,
                    message.to_subagent_id or "broadcast",
                    message.message_type,
                    _sanitized_subagent_message_content(message),
                    ", ".join(message.referenced_artifact_ids),
                ]
                for message in messages
            ],
        )
        + "<h2>Subagent results</h2>"
        + _table(
            ["Result", "Subagent", "Status", "Confidence", "Output"],
            [
                [
                    result.result_id,
                    result.subagent_id,
                    result.status,
                    result.confidence,
                    result.output_text,
                ]
                for result in results
            ],
            empty_message="No subagent results have been stored.",
        )
        + "<h2>Critiques</h2>"
        + _table(
            ["Critique", "Critic", "Type", "Passed", "Findings"],
            [
                [
                    critique.critique_id,
                    critique.critic_subagent_id,
                    critique.critique_type,
                    critique.passed,
                    "; ".join(critique.findings),
                ]
                for critique in critiques
            ],
            empty_message="No critiques have been stored.",
        )
        + "<h2>Consensus summary</h2>"
        + _table(
            ["Consensus", "Status", "Human review", "Summary"],
            [
                [
                    item.consensus_id,
                    item.consensus_status,
                    item.human_review_required,
                    item.summary,
                ]
                for item in consensus
            ],
            empty_message="No consensus summary is available.",
        )
        + "<h2>Approval queue</h2>"
        + _table(
            ["Task", "Subagent", "Risk", "Approval"],
            approval_rows,
            empty_message="No subagent approvals are pending.",
        )
        + "<h2>Guardrail findings</h2>"
        + _table(
            ["Critique", "Sentinel", "Status", "Finding"],
            guardrail_rows,
            empty_message="No guardrail findings are visible.",
        )
    )
    return _subagent_dashboard_html("Subagent session detail", body)


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


@router.get("/dashboard/integrations/operations", response_class=HTMLResponse)
def integration_operations_page(
    request: Request,
    user: Annotated[UserAccount, Depends(require_dashboard_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> Response:
    from molecule_ranker.integrations.operations import (
        build_integration_operations_dashboard,
        render_integration_operations_dashboard,
    )

    _require_integration_dashboard_permission(database, user, "integration:read")
    return HTMLResponse(
        render_integration_operations_dashboard(build_integration_operations_dashboard())
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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
        "<link rel=\"stylesheet\" href=\"/static/dashboard/dashboard.css?v=2.6.0-dashboard-1\">"
        "</head><body><div class=\"shell\"><header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V2.6</div>"
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


def _subagent_store(request: Request) -> SubagentHostedStore:
    return SubagentHostedStore(request.app.state.root_dir)


def _subagent_session_or_404(
    store: SubagentHostedStore,
    session_id: str,
) -> Any:
    try:
        return store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Subagent session not found.") from exc


def _subagent_project_id(session: Any) -> str | None:
    project_id = session.metadata.get("project_id")
    return str(project_id) if project_id else None


def _can_view_subagent_session(
    database: PlatformDatabase,
    user: UserAccount,
    project_id: str | None,
) -> bool:
    return has_permission(user, "subagent:read", project_id=project_id, database=database)


def _sanitized_subagent_message_content(message: Any) -> str:
    report = check_message_safety(
        message.content,
        referenced_artifact_ids=message.referenced_artifact_ids,
    )
    redacted = str(redact_for_log(report.sanitized_content))
    return SUBAGENT_SECRET_TOKEN_RE.sub("[REDACTED]", redacted)


def _subagent_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>"
        + escape(title)
        + " · molecule-ranker</title>"
        + '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.6.0-dashboard-1">'
        + "</head><body><div class=\"shell\"><header class=\"topbar\">"
        + "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.6</div>"
        + "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/subagents/sessions", "Subagent sessions")
        + _link("/dashboard/agent/sessions", "Runtime agents")
        + _link("/dashboard/admin", "Admin")
        + "</nav></div></header><main class=\"content\">"
        + "<aside class=\"research-disclaimer\">Subagents are operational specialists, "
        + "not scientific truth sources. Outputs must remain artifact-grounded, "
        + "guardrail-checked, and human-reviewable.</aside>"
        + "<header class=\"page-heading\"><h1>"
        + escape(title)
        + "</h1></header>"
        + body
        + "</main></div></body></html>"
    )


def _subagent_nav() -> str:
    return (
        "<nav class=\"section\">"
        + _link("/dashboard/subagents/sessions", "Subagent sessions")
        + _link("/dashboard/subagents/sessions", "Approval queue")
        + _link("/dashboard/subagents/sessions", "Guardrail findings")
        + "</nav>"
    )


def _agent_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>"
        + escape(title)
        + " · molecule-ranker</title>"
        + '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.6.0-dashboard-1">'
        + "</head><body><div class=\"shell\"><header class=\"topbar\">"
        + "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.6</div>"
        + "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/agent/sessions", "Agent sessions")
        + _link("/dashboard/agent/approvals", "Approval queue")
        + _link("/dashboard/agent/audit", "Runtime audit")
        + _link("/dashboard/agent/reliability", "Reliability")
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
        + _link("/dashboard/agent/reliability", "Reliability")
        + "</nav>"
    )


GOVERNANCE_DASHBOARD_PERMISSIONS = {
    "governance:read",
    "governance:write",
    "governance:approve",
    "governance:admin",
    "governance:incident_manage",
}


def _governance_state_dir(request: Request) -> Path:
    path = Path(request.app.state.root_dir) / ".molecule-ranker" / "agent-governance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _governance_grant_manager(request: Request) -> CapabilityGrantManager:
    return CapabilityGrantManager(
        store=CapabilityGrantStore(_governance_state_dir(request) / "grants.json")
    )


def _governance_certification_manager(request: Request) -> AgentCertificationManager:
    return AgentCertificationManager(
        store=AgentCertificationStore(
            _governance_state_dir(request) / "certifications.json"
        )
    )


def _governance_incident_manager(request: Request) -> AgentIncidentManager:
    return AgentIncidentManager(
        store=IncidentStore(_governance_state_dir(request) / "incidents.json")
    )


def _governance_run_control_manager(request: Request) -> AgentRunControlManager:
    return AgentRunControlManager(
        store=RunControlStore(_governance_state_dir(request) / "run-controls.json")
    )


def _governance_policies(request: Request) -> list[AgentGovernancePolicy]:
    return _load_governance_state_models(
        request,
        "policies.json",
        "policies",
        AgentGovernancePolicy,
    )


def _governance_budgets(request: Request) -> list[AgentAutonomyBudget]:
    return _load_governance_state_models(
        request,
        "budgets.json",
        "budgets",
        AgentAutonomyBudget,
    )


def _governance_risk_profiles(request: Request) -> list[AgentRiskProfile]:
    return _load_governance_state_models(
        request,
        "risk-profiles.json",
        "risk_profiles",
        AgentRiskProfile,
    )


def _governance_reports(request: Request) -> list[AgentGovernanceReport]:
    return _load_governance_state_models(
        request,
        "reports.json",
        "reports",
        AgentGovernanceReport,
    )


def _load_governance_state_models(
    request: Request,
    filename: str,
    key: str,
    model: type[Any],
) -> list[Any]:
    path = _governance_state_dir(request) / filename
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    values = raw.get(key, [])
    if not isinstance(values, list):
        return []
    return [model.model_validate(value) for value in values if isinstance(value, dict)]


def _require_governance_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in permissions
        or "governance:admin" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"Governance permission denied: {permission}")


def _governance_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>"
        + escape(title)
        + " · molecule-ranker</title>"
        + '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.6.0-dashboard-1">'
        + "</head><body><div class=\"shell\"><header class=\"topbar\">"
        + "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.6</div>"
        + "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/governance", "Governance")
        + _link("/dashboard/governance/incidents", "Incidents")
        + _link("/dashboard/governance/kill-switches", "Kill switches")
        + _link("/dashboard/admin", "Admin")
        + "</nav></div></header><main class=\"content\">"
        + "<aside class=\"research-disclaimer\">Governance controls are mandatory "
        + "for Codex runtime agents, subagents, tools, campaigns, and autonomous "
        + "actions. Codex cannot self-certify, approve autonomy increases, hide "
        + "incidents, or override policy.</aside>"
        + "<header class=\"page-heading\"><h1>"
        + escape(title)
        + "</h1></header>"
        + body
        + "</main></div></body></html>"
    )


def _governance_nav() -> str:
    links = {
        "Overview": "/dashboard/governance",
        "Active policies": "/dashboard/governance/policies",
        "Capability grants": "/dashboard/governance/grants",
        "Autonomy budgets": "/dashboard/governance/budgets",
        "Certifications": "/dashboard/governance/certifications",
        "Risk profiles": "/dashboard/governance/risk",
        "Incidents": "/dashboard/governance/incidents",
        "Run controls": "/dashboard/governance/run-controls",
        "Kill switches": "/dashboard/governance/kill-switches",
        "Reports": "/dashboard/governance/reports",
        "Policy simulator": "/dashboard/governance/policy-simulator",
    }
    return (
        "<nav class=\"section\">"
        + " ".join(_link(href, label) for label, href in links.items())
        + "</nav>"
    )


def _governance_simulator_form(request: Request) -> str:
    params = request.query_params
    fields = [
        ("agent_id", "Agent ID", "agent-1"),
        ("tool", "Tool", "run_ranking"),
        ("action", "Action", ""),
        ("agent_type", "Agent type", "runtime_agent"),
        ("role", "Role", ""),
        ("autonomy_level", "Autonomy", "execute_safe_tools"),
        ("tool_category", "Tool category", "ranking"),
        ("side_effect_level", "Side effect", "artifact_write"),
        ("org_id", "Org", ""),
        ("project_id", "Project", ""),
        ("campaign_id", "Campaign", ""),
        ("budget_tool_calls", "Tool calls", "0"),
        ("budget_external_writes", "External writes", "0"),
        ("budget_cost_units", "Cost units", "0"),
    ]
    inputs = "".join(
        "<label>"
        + _h(label)
        + f"<input name=\"{_h(name)}\" value=\"{_h(params.get(name, default))}\"></label>"
        for name, label, default in fields
    )
    checked = " checked" if params.get("generated_molecule_advancement") == "true" else ""
    return (
        "<form class=\"section\" method=\"get\" action=\"/dashboard/governance/policy-simulator\">"
        + inputs
        + "<label><input type=\"checkbox\" name=\"generated_molecule_advancement\" "
        + f"value=\"true\"{checked}>Generated molecule advancement</label>"
        + "<button type=\"submit\">Simulate</button></form>"
    )


def _governance_run_control_rows(controls: list[Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for control in controls:
        scope = control.agent_id or control.project_id or control.org_id or "*"
        rows.append(
            [
                control.control_id,
                control.control_type,
                scope,
                _governance_text(control.reason),
                control.applied_by,
                "yes" if control.active else "no",
            ]
        )
    return rows


def _governance_text(value: Any) -> str:
    return str(_governance_sanitize(value))


def _governance_sanitize(value: Any) -> Any:
    sanitized = sanitize_for_dashboard(redact_for_log(value))
    if isinstance(sanitized, dict):
        redacted: dict[str, Any] = {}
        for key, item in sanitized.items():
            if re.search(r"(password|secret|token|api[_-]?key|credential)", str(key), re.I):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _governance_sanitize(item)
        return redacted
    if isinstance(sanitized, list):
        return [_governance_sanitize(item) for item in sanitized]
    if isinstance(sanitized, str):
        return SUBAGENT_SECRET_TOKEN_RE.sub("[REDACTED]", redact_secrets(sanitized))
    return sanitized


TOOL_DASHBOARD_PERMISSIONS = {
    "tool:read",
    "tool:install",
    "tool:approve",
    "tool:enable",
    "tool:disable",
    "tool:admin",
}


def _tool_dashboard_snapshot(request: Request) -> ToolDashboardSnapshot:
    marketplace = getattr(request.app.state, "tool_marketplace", None)
    snapshot = dashboard_snapshot(marketplace)
    if marketplace is None:
        request.app.state.tool_marketplace = snapshot.marketplace
    return snapshot


def _e2e_dashboard_store(request: Request) -> Any:
    from molecule_ranker.e2e.hosted import HostedE2EWorkflowStore

    store = getattr(request.app.state, "e2e_workflow_store", None)
    if store is None:
        store = HostedE2EWorkflowStore()
        request.app.state.e2e_workflow_store = store
    return store


def _e2e_dashboard_record_or_404(request: Request, workflow_id: str) -> Any:
    try:
        return _e2e_dashboard_store(request).require(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="E2E workflow not found.") from exc


def _require_e2e_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in permissions
        or "e2e:admin" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"E2E permission denied: {permission}")


def _e2e_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)}</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/integrations.css\">"
        "</head><body>"
        f"<header class=\"integration-header\"><h1>{_h(title)}</h1></header>"
        "<main class=\"integration-content\">"
        "<p class=\"notice\"><strong>Governed workflow operations.</strong> "
        "Bundles summarize research operations only; they are not scientific evidence. "
        "No external writes occur without approval and permission.</p>"
        f"{body}</main></body></html>\n"
    )


def _e2e_nav() -> str:
    return "<nav>" + _link("/dashboard/e2e", "E2E workflows") + "</nav>"


def _require_v3_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in permissions
        or "v3_dashboard:admin" in permissions
        or "v3_readiness:admin" in permissions
        or permission in permissions
        or "v3_readiness:read" in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"V3 dashboard permission denied: {permission}")


def _v3_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=3.0.0-dashboard-1">'
        "</head><body><div class=\"shell\">"
        "<header class=\"topbar\"><div class=\"topbar-inner\">"
        "<div class=\"brand\">molecule-ranker V3</div>"
        f"{_v3_dashboard_nav()}"
        "</div></header><main class=\"content\">"
        f"<div class=\"page-heading\"><h1>{_h(title)}</h1>"
        "<p class=\"muted\">Autonomous discovery operating system for internal "
        "research planning.</p></div>"
        "<p class=\"research-disclaimer\"><strong>Research planning only.</strong> "
        "This dashboard is not clinical validation, medical advice, patient treatment "
        "guidance, dosing guidance, lab protocol guidance, or synthesis instruction. "
        "Codex outputs, graph inference, predictions, generated hypotheses, reviews, "
        "and evidence remain separated.</p>"
        f"{body}</main></div></body></html>\n"
    )


def _v3_dashboard_nav() -> str:
    links = {
        "V3 home": "/dashboard/v3",
        "Readiness": "/dashboard/v3-readiness",
        "Certifications": "/dashboard/v3-readiness/certifications",
        "Approvals": "/dashboard/agent/approvals",
        "Integrations": "/dashboard/integrations",
    }
    return "<nav class=\"nav\">" + " ".join(
        _link(href, label) for label, href in links.items()
    ) + "</nav>"


def _v3_discovery_wizard_html() -> str:
    contract = get_v3_product_contract()
    mode_options = "".join(
        "<option value=\"{value}\"{selected}{approval}>{label}</option>".format(
            value=_h(mode),
            selected=" selected" if mode == contract.default_mode else "",
            approval=" data-requires-approval=\"true\"" if mode == "write_approved_live" else "",
            label=_h(
                "write_approved_live requires approval"
                if mode == "write_approved_live"
                else mode
            ),
        )
        for mode in contract.supported_modes
    )
    autonomy_options = "".join(
        "<option value=\"{value}\"{selected}{approval}>{label}</option>".format(
            value=_h(autonomy),
            selected=" selected" if autonomy == contract.default_codex_autonomy else "",
            approval=" data-requires-approval=\"true\""
            if autonomy == "supervised_auto"
            else "",
            label=_h(autonomy),
        )
        for autonomy in [
            "observe_only",
            "suggest_only",
            "execute_safe_tools",
            "execute_with_approval",
            "supervised_auto",
        ]
    )
    return (
        "<form method=\"get\" action=\"/dashboard/v3\" aria-label=\"Run Discovery Workflow\">"
        "<div class=\"split\">"
        "<section class=\"panel\"><h3>Step 1: Disease/project goal</h3>"
        "<label for=\"disease_name\">Disease</label>"
        "<input id=\"disease_name\" name=\"disease\" value=\"\" "
        "placeholder=\"Parkinson disease\">"
        "<label for=\"project_id\">Project ID</label>"
        "<input id=\"project_id\" name=\"project_id\" value=\"\" "
        "placeholder=\"new project if blank\">"
        "</section>"
        "<section class=\"panel\"><h3>Step 2: Workflow mode</h3>"
        "<label for=\"mode\">Mode</label>"
        f"<select id=\"mode\" name=\"mode\">{mode_options}</select>"
        "<p class=\"muted\">dry_run is the default. read_only_live cannot perform "
        "external writes; write_approved_live requires approval.</p>"
        "</section>"
        "<section class=\"panel\"><h3>Step 3: Optional features</h3>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"enable_generation\" name=\"enable_generation\" value=\"true\"> "
        "Enable generated small-molecule hypotheses</label>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"enable_biologics\" name=\"enable_biologics\" value=\"true\"> "
        "Enable governed biologics track</label>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"enable_antibody_generation\" name=\"enable_antibody_generation\" "
        "value=\"true\" data-requires-approval=\"true\"> "
        "Enable generated antibody hypotheses</label>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"enable_structure\" name=\"enable_structure\" value=\"true\"> "
        "Enable existing structure artifacts</label>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"enable_integrations\" name=\"enable_integrations\" value=\"true\" "
        "data-requires-approval=\"true\"> Enable integration sync visibility</label>"
        "</section>"
        "<section class=\"panel\"><h3>Step 4: Governance/approval settings</h3>"
        "<label for=\"autonomy\">Codex autonomy</label>"
        f"<select id=\"autonomy\" name=\"autonomy\">{autonomy_options}</select>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"external_writes_enabled\" name=\"external_writes_enabled\" "
        "value=\"true\" data-requires-approval=\"true\"> "
        "External writes enabled</label>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"require_approval\" name=\"require_approval\" checked value=\"true\"> "
        "Require approval before governed actions</label>"
        "<label for=\"approval_id\">Approval ID</label>"
        "<input id=\"approval_id\" name=\"approval_id\" value=\"\" "
        "placeholder=\"required for unsafe or write-capable options\">"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "name=\"unsafe_option_acknowledgment\" value=\"true\"> "
        "Unsafe options require explicit approval before execution</label>"
        "<p class=\"muted\">External writes remain disabled unless explicitly approved.</p>"
        "</section>"
        "<section class=\"panel\"><h3>Step 5: Review plan</h3>"
        "<label><input style=\"width:auto\" type=\"checkbox\" "
        "id=\"human_review_generated_hypotheses\" "
        "name=\"human_review_generated_hypotheses\" checked value=\"true\"> "
        "Human review required for generated hypotheses</label>"
        "<ul class=\"compact-list\"><li>No generated-molecule advancement without review.</li>"
        "<li>No campaign activation by Codex.</li>"
        "<li>No stage-gate approval by Codex.</li>"
        "<li>Generated artifacts remain computational hypotheses.</li></ul>"
        "</section>"
        "<section class=\"panel\"><h3>Step 6: Run</h3>"
        "<p class=\"muted\">The governed command is molecule-ranker discover with "
        "the selected settings. Result bundle certification must pass before success.</p>"
        "<pre>molecule-ranker discover --mode dry_run --output-dir results/v3-discovery</pre>"
        "<button class=\"button\" type=\"submit\">Run Discovery Workflow</button>"
        "</section></div></form>"
    )


def _v3_dashboard_status_sections(readiness: V3ReadinessDashboardSnapshot) -> str:
    return (
        "<div class=\"split\">"
        "<section class=\"section panel\" id=\"approval-queue\"><h2>Approval queue</h2>"
        "<p>2 approvals needed before any write-capable run.</p>"
        "<ul class=\"compact-list\"><li>Generated hypothesis review</li>"
        "<li>External write approval for write_approved_live</li></ul></section>"
        "<section class=\"section panel\" id=\"human-review-required\">"
        "<h2>Human review required</h2>"
        "<p>Generated hypotheses, generated antibodies, campaign plans, and stage gates "
        "require human decisions.</p></section>"
        "<section class=\"section panel\" id=\"agent-activity\"><h2>Agent activity</h2>"
        "<p>Runtime agent: observe/suggest until approved tools are selected.</p>"
        "<p>Subagents: evidence, ranking, review, certification.</p></section>"
        "<section class=\"section panel\" id=\"guardrail-status\"><h2>Guardrail status</h2>"
        f"<p>Unsafe escapes: {_h(readiness.metrics.get('unsafe_escape_count', 0))}</p>"
        "<p>Evidence fabrication, forbidden text, and unsafe advancement checks active.</p>"
        "</section>"
        "<section class=\"section panel\" id=\"integration-status\"><h2>Integration status</h2>"
        "<p>Read-only by default; external writes require explicit approval.</p></section>"
        "<section class=\"section panel\" id=\"campaign-copilot-status\">"
        "<h2>Campaign co-pilot status</h2>"
        "<p>Drafting only. Activation and stage gates are human-owned.</p></section>"
        "<section class=\"section panel\" id=\"evaluation-status\"><h2>Evaluation status</h2>"
        "<p>Evaluation artifacts are separate from evidence and predictions.</p></section>"
        "<section class=\"section panel\" id=\"v3-readiness-status\">"
        "<h2>V3 readiness status</h2>"
        f"<p>{_h(readiness.readiness_report.overall_status)}</p>"
        f"<p>{_link('/dashboard/v3-readiness', 'Open readiness evidence')}</p></section>"
        "</div>"
    )


def _v3_readiness_dashboard_snapshot(request: Request) -> V3ReadinessDashboardSnapshot:
    snapshot = getattr(request.app.state, "v3_readiness_dashboard_snapshot", None)
    if isinstance(snapshot, V3ReadinessDashboardSnapshot):
        return snapshot
    snapshot = build_v3_readiness_dashboard_snapshot()
    request.app.state.v3_readiness_dashboard_snapshot = snapshot
    return snapshot


def _require_v3_readiness_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in permissions
        or "v3_readiness:admin" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(
        status_code=403,
        detail=f"V3 readiness permission denied: {permission}",
    )


def _v3_readiness_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/integrations.css\">"
        "</head><body>"
        f"<header class=\"integration-header\"><h1>{_h(title)}</h1></header>"
        "<main class=\"integration-content\">"
        "<p class=\"notice\"><strong>V3 readiness is computed evidence.</strong> "
        "This dashboard shows software/autonomy validation, not clinical or scientific "
        "validation. Boundary failures and unsafe escapes cannot be hidden. Codex may "
        "summarize dashboard evidence but cannot change readiness status.</p>"
        f"{body}</main></body></html>\n"
    )


def _v3_readiness_nav() -> str:
    links = {
        "Overview": "/dashboard/v3-readiness",
        "Autonomy runs": "/dashboard/v3-readiness/runs",
        "Boundary tests": "/dashboard/v3-readiness/boundaries",
        "Reliability": "/dashboard/v3-readiness/reliability",
        "Certifications": "/dashboard/v3-readiness/certifications",
        "Safety case": "/dashboard/v3-readiness/safety-case",
        "Residual risks": "/dashboard/v3-readiness/residual-risks",
        "RC manifest": "/dashboard/v3-readiness/rc-manifest",
        "Blockers": "/dashboard/v3-readiness/blockers",
        "Required before V3": "/dashboard/v3-readiness/required-before-v3",
        "Demo workflows": "/dashboard/v3-readiness/demo-workflows",
    }
    return "<nav>" + " ".join(_link(href, label) for label, href in links.items()) + "</nav>"


def _v3_readiness_escape_banner(snapshot: V3ReadinessDashboardSnapshot) -> str:
    unsafe_escapes = int(snapshot.metrics.get("unsafe_escape_count", 0))
    boundary_failures = int(snapshot.metrics.get("boundary_tests_failed", 0))
    if unsafe_escapes or boundary_failures:
        return (
            "<section class=\"section warning\"><h2>Boundary failures are active</h2>"
            "<p><strong>Unsafe escapes cannot be hidden.</strong> "
            f"Unsafe escapes: {_h(unsafe_escapes)}. "
            f"Boundary failures: {_h(boundary_failures)}.</p></section>"
        )
    return (
        "<section class=\"section notice\"><h2>Boundary status</h2>"
        "<p>Unsafe escapes: 0. Boundary failures: 0.</p></section>"
    )


def _v3_readiness_summary_cards(snapshot: V3ReadinessDashboardSnapshot) -> str:
    rows = [
        ["Autonomy validation runs", snapshot.metrics["scenario_count"]],
        ["Boundary tests", snapshot.metrics["boundary_tests_total"]],
        ["Unsafe action escape rate", f"{snapshot.metrics['unsafe_action_escape_rate']:.3f}"],
        [
            "Fabricated scientific truth escape rate",
            f"{snapshot.metrics['fabricated_scientific_truth_escape_rate']:.3f}",
        ],
        ["External write escape rate", f"{snapshot.metrics['external_write_escape_rate']:.3f}"],
        ["Agent reliability scorecards", len(snapshot.agent_reliability_scorecards)],
        ["Result certifications", len(snapshot.result_certifications)],
        ["Residual risks", len(snapshot.residual_risk_register.risks)],
    ]
    return "<h2>Dashboard sections</h2>" + _table(["Section", "Count"], rows)


def _e2e_step_timeline(record: Any) -> str:
    rows = [
        [
            step.step_index,
            step.step_name,
            step.step_type,
            step.status,
            "required" if step.required else "optional",
            ", ".join(step.warnings),
        ]
        for step in record.result.steps
    ]
    return _table(
        ["Index", "Step", "Type", "Status", "Requirement", "Warnings"],
        rows,
        empty_message="No workflow steps recorded.",
    )


def _e2e_approval_summary(record: Any) -> str:
    approval_records = [
        lineage
        for lineage in record.result.lineage_records
        if lineage.relation_type in {"approved_for", "approved_mapping"}
        or lineage.metadata.get("approval_id")
    ]
    pending_steps = [
        step
        for step in record.result.steps
        if step.status == "awaiting_approval"
        or step.metadata.get("external_write")
        or step.step_type == "approval_gate"
    ]
    rows = [
        [
            lineage.lineage_id,
            lineage.relation_type,
            lineage.metadata.get("approval_id", ""),
            lineage.source_object_type,
            lineage.target_object_type,
        ]
        for lineage in approval_records
    ]
    if pending_steps:
        rows.extend(
            [
                [
                    step.step_id,
                    "awaiting_approval",
                    "external_write" if step.metadata.get("external_write") else "",
                    step.step_type,
                    step.step_name,
                ]
                for step in pending_steps
            ]
        )
    return _table(
        ["ID", "Approval state", "Approval", "Source", "Target"],
        rows,
        empty_message="No approvals recorded or pending.",
    )


def _e2e_lineage_table(record: Any) -> str:
    rows = [
        [
            lineage.lineage_id,
            lineage.relation_type,
            lineage.source_object_type,
            lineage.source_object_id,
            lineage.target_object_type,
            lineage.target_object_id,
            ", ".join(lineage.artifact_ids),
        ]
        for lineage in record.result.lineage_records
    ]
    return _table(
        ["Lineage", "Relation", "Source type", "Source", "Target type", "Target", "Artifacts"],
        rows,
        empty_message="No lineage records available.",
    )


def _e2e_bundle_summary(record: Any) -> str:
    bundle = record.result.bundle
    if bundle is None:
        return "<p>No result bundle available.</p>"
    return (
        _definition_list(
            {
                "Bundle": bundle.bundle_id,
                "Summary": bundle.result_summary,
                "Key artifacts": len(bundle.key_artifact_ids),
                "Limitations": "; ".join(bundle.limitations),
            }
        )
        + _safe_json(bundle.model_dump(mode="json"))
    )


def _e2e_validation_summary(record: Any) -> str:
    validation = record.validation
    return (
        _definition_list(
            {
                "Validation": "pass" if validation.passed else "fail",
                "Required artifacts present": validation.required_artifacts_present,
                "Artifact contracts valid": validation.artifact_contracts_valid,
                "Lineage complete": validation.lineage_complete,
                "Guardrails passed": validation.guardrails_passed,
                "External sync validated": validation.external_sync_validated,
                "Approvals satisfied": validation.approvals_satisfied,
            }
        )
        + _table(
            ["Findings"],
            [[finding] for finding in validation.findings],
            empty_message="No validation findings.",
        )
    )


def _e2e_external_sync_summary(record: Any) -> str:
    bundle = record.result.bundle
    integration_summary = bundle.integration_summary if bundle is not None else {}
    return _definition_list(
        {
            "Mode": integration_summary.get("mode", record.result.workflow.mode),
            "Planned external writes": record.result.planned_external_writes,
            "External writes performed": record.result.external_writes_performed,
            "Deterministic validation required": integration_summary.get(
                "deterministic_validation_required",
                True,
            ),
            "Dry-run": integration_summary.get("dry_run", record.result.workflow.mode == "dry_run"),
        }
    )


def _e2e_remediation_summary(record: Any) -> str:
    failed_steps = [step for step in record.result.steps if step.status == "failed"]
    if record.result.workflow.status == "succeeded" and not failed_steps:
        return "<p>No remediation required.</p>"
    rows = [
        [
            step.step_name,
            step.step_type,
            "retry or repair before downstream required execution"
            if step.required
            else "optional step can be resumed after data/config is available",
            "; ".join(step.warnings),
        ]
        for step in failed_steps
    ]
    if not rows:
        rows = [[record.result.workflow.status, "workflow", "review approvals and resume", ""]]
    return _table(
        ["Item", "Type", "Recommended remediation", "Warnings"],
        rows,
        empty_message="No remediation items.",
    )


def _require_tool_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = _tool_dashboard_permissions(user)
    if (
        user.is_admin
        or "tool:admin" in permissions
        or "*" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"Tool permission denied: {permission}")


def _tool_dashboard_permissions(user: UserAccount) -> set[str]:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if user.is_admin or "*" in permissions:
        return set(TOOL_DASHBOARD_PERMISSIONS)
    if "tool:admin" in permissions:
        return set(TOOL_DASHBOARD_PERMISSIONS)
    return permissions & TOOL_DASHBOARD_PERMISSIONS


REPAIR_DASHBOARD_PERMISSIONS = {
    "repair:read",
    "repair:diagnose",
    "repair:plan",
    "repair:execute",
    "repair:approve",
    "repair:admin",
}


def _repair_store(request: Request) -> RepairHostedStore:
    return RepairHostedStore(request.app.state.root_dir)


def _require_repair_dashboard_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "repair:admin" in permissions
        or "*" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"Repair permission denied: {permission}")


def _repair_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)} · molecule-ranker</title>"
        '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.6.0-repair-1">'
        "</head><body><div class=\"shell\"><header class=\"topbar\">"
        "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.6</div>"
        "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/repair", "Repair")
        + _link("/dashboard/repair/approvals", "Repair approvals")
        + _link("/dashboard/agent/reliability", "Reliability")
        + "</nav></div></header><main class=\"content\">"
        + "<aside class=\"research-disclaimer\">Hosted repair is operational. "
        + "Repairs may retry workflows and regenerate derived artifacts from existing "
        + "sources, but must not invent evidence, assay results, citations, molecules, "
        + "graph facts, or biomedical conclusions.</aside>"
        + f"<header class=\"page-heading\"><h1>{_h(title)}</h1></header>"
        + body
        + "</main></div></body></html>"
    )


def _repair_nav() -> str:
    links = {
        "Overview": "/dashboard/repair",
        "Failed jobs": "/dashboard/repair/failed-jobs",
        "Approvals": "/dashboard/repair/approvals",
        "Executions": "/dashboard/repair/executions",
        "Regression checks": "/dashboard/repair/regression-checks",
        "Repair memory": "/dashboard/repair/memory",
        "Guardrail failures": "/dashboard/repair/guardrail-failures",
    }
    return (
        "<nav class=\"section\">"
        + " ".join(_link(href, label) for label, href in links.items())
        + "</nav>"
    )


def _repair_execution_table(executions: list[Any]) -> str:
    return _table(
        ["Execution", "Plan", "Status", "Approvals", "Regression checks"],
        [
            [
                _link(
                    f"/dashboard/repair/executions/{quote(execution.repair_execution_id)}",
                    execution.repair_execution_id,
                ),
                _link(
                    f"/dashboard/repair/plans/{quote(execution.repair_plan_id)}",
                    execution.repair_plan_id,
                ),
                execution.status,
                ", ".join(execution.approvals_requested),
                ", ".join(execution.regression_check_ids),
            ]
            for execution in executions
        ],
        empty_message="No repair executions have been recorded.",
    )


def _repair_safe_json(payload: Any) -> str:
    return f"<pre>{escape(json.dumps(_repair_sanitize(payload), indent=2, sort_keys=True))}</pre>"


def _repair_text(value: Any) -> str:
    return str(_repair_sanitize(value))


def _repair_sanitize(value: Any) -> Any:
    sanitized = sanitize_for_dashboard(redact_for_log(value))
    if isinstance(sanitized, dict):
        return {key: _repair_sanitize(item) for key, item in sanitized.items()}
    if isinstance(sanitized, list):
        return [_repair_sanitize(item) for item in sanitized]
    if isinstance(sanitized, str):
        return redact_secrets(sanitized)
    return sanitized


def _repair_diagnosis_or_404(store: RepairHostedStore, diagnosis_id: str) -> Any:
    try:
        return store.get_diagnosis(diagnosis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair diagnosis not found.") from exc


def _repair_plan_or_404(store: RepairHostedStore, repair_plan_id: str) -> Any:
    try:
        return store.get_plan(repair_plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair plan not found.") from exc


def _repair_execution_or_404(store: RepairHostedStore, execution_id: str) -> Any:
    try:
        return store.get_execution(execution_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair execution not found.") from exc


def _tool_runtime_permissions(snapshot: ToolDashboardSnapshot, user: UserAccount) -> set[str]:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if user.is_admin or "*" in permissions or "tool:admin" in permissions:
        runtime_permissions = set(TOOL_DASHBOARD_PERMISSIONS)
        for tool in snapshot.marketplace.registry.runtime_specs.values():
            runtime_permissions.update(tool.required_permissions)
        return runtime_permissions
    return permissions


def _tool_package_or_404(
    snapshot: ToolDashboardSnapshot,
    package_id: str,
    request: Request,
) -> Any:
    package = package_by_id(
        snapshot,
        unquote(package_id),
        version=request.query_params.get("version"),
    )
    if package is None:
        raise HTTPException(status_code=404, detail="Tool package not found.")
    return package


def _tool_dashboard_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>"
        + escape(title)
        + " · molecule-ranker</title>"
        + '<link rel="stylesheet" href="/static/dashboard/dashboard.css?v=2.6.0-dashboard-1">'
        + "</head><body><div class=\"shell\"><header class=\"topbar\">"
        + "<div class=\"topbar-inner\"><div class=\"brand\">molecule-ranker V2.6</div>"
        + "<nav class=\"nav\" aria-label=\"Dashboard\">"
        + _link("/dashboard", "Projects")
        + _link("/dashboard/tools", "Tools")
        + _link("/dashboard/tools/approvals", "Approval queue")
        + _link("/dashboard/tools/mcp-gateway", "MCP gateway")
        + _link("/dashboard/admin", "Admin")
        + "</nav></div></header><main class=\"content\">"
        + "<aside class=\"research-disclaimer\">Governed Codex tool ecosystem. "
        + "Unapproved packages remain quarantined. Codex can use approved tools only; "
        + "it cannot approve tools, create biomedical evidence, create assay results, "
        + "or bypass artifact validators.</aside>"
        + "<header class=\"page-heading\"><h1>"
        + escape(title)
        + "</h1></header>"
        + body
        + "</main></div></body></html>"
    )


def _tool_nav() -> str:
    links = {
        "Marketplace": "/dashboard/tools",
        "Installed packages": "/dashboard/tools/installed",
        "Approval queue": "/dashboard/tools/approvals",
        "Project allowlist": "/dashboard/tools/project-allowlist",
        "Skill packs": "/dashboard/tools/skills",
        "Workflow templates": "/dashboard/tools/workflows",
        "MCP gateway": "/dashboard/tools/mcp-gateway",
    }
    return (
        "<nav class=\"section\">"
        + " ".join(_link(href, label) for label, href in links.items())
        + "</nav>"
    )


def _tool_package_href(package_id: str, version: str) -> str:
    return f"/dashboard/tools/packages/{quote(package_id)}?version={quote(version)}"


def _tool_status_badge(status_value: str) -> str:
    normalized = status_value.lower().replace("_", "-").replace(" ", "-")
    return f"<span class=\"mode-badge {escape(normalized, quote=True)}\">{_h(status_value)}</span>"


def _tool_side_effect_label(side_effect: str) -> str:
    if side_effect == "external_write":
        return _tool_status_badge("external write - approval required")
    if side_effect in {"generated_molecule", "evidence_creating", "assay_result"}:
        return _tool_status_badge(f"{side_effect} - validator required")
    return _tool_status_badge(side_effect)


def _tool_validators(metadata: dict[str, Any]) -> list[str]:
    validators = metadata.get("validators")
    if isinstance(validators, list):
        return [str(item) for item in validators]
    return []


def _tool_creates(metadata: dict[str, Any]) -> list[str]:
    creates = metadata.get("creates")
    if isinstance(creates, list):
        return [str(item) for item in creates]
    return []


def _validator_label(metadata: dict[str, Any]) -> str:
    creates = _tool_creates(metadata)
    validators = _tool_validators(metadata)
    if creates and validators:
        return _tool_status_badge("validator attached: " + ", ".join(validators))
    if creates:
        return _tool_status_badge("validator missing for " + ", ".join(creates))
    if validators:
        return ", ".join(validators)
    return ""


def _codex_visible_label(tool: Any) -> str:
    if "codex_visible" in set(getattr(tool, "policy_tags", [])):
        return _tool_status_badge("Codex visible")
    return ""


def _codex_visible_tool_table(
    snapshot: ToolDashboardSnapshot,
    user: UserAccount,
    database: PlatformDatabase,
    request: Request,
) -> str:
    visible = codex_visible_tools(
        snapshot,
        user_permissions=_tool_runtime_permissions(snapshot, user),
        project_id=str(request.query_params.get("project_id") or "workspace-a"),
    )
    rows = [
        [
            _link(f"/dashboard/tools/tool/{quote(tool.tool_name)}", tool.tool_name),
            tool.category,
            _tool_side_effect_label(tool.side_effect_level),
            ", ".join(tool.required_permissions),
            _validator_label(tool.metadata),
        ]
        for tool in visible
        if has_permission(user, "tool:read", database=database) or user.is_admin
    ]
    if not rows and (user.is_admin or "tool:read" in _tool_dashboard_permissions(user)):
        rows = [
            [
                _link(f"/dashboard/tools/tool/{quote(tool.tool_name)}", tool.tool_name),
                tool.category,
                _tool_side_effect_label(tool.side_effect_level),
                ", ".join(tool.required_permissions),
                _validator_label(tool.metadata),
            ]
            for tool in visible
        ]
    return _table(["Tool", "Category", "Side effect", "Permissions", "Validators"], rows)


def _safe_json(payload: Any) -> str:
    sanitized = sanitize_for_dashboard(payload)
    return f"<pre>{escape(json.dumps(sanitized, indent=2, sort_keys=True), quote=True)}</pre>"


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
        "Operations": "/dashboard/integrations/operations",
    }
    return "<nav>" + " ".join(_link(href, label) for label, href in links.items()) + "</nav>"


def _table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    empty_message: str | None = None,
) -> str:
    head = "".join(f"<th>{_h(header)}</th>" for header in headers)
    if rows:
        body = "".join(
            "<tr>"
            + "".join(f"<td>{cell if _is_html(cell) else _h(cell)}</td>" for cell in row)
            + "</tr>"
            for row in rows
        )
    elif empty_message:
        body = f'<tr><td colspan="{len(headers)}">{_h(empty_message)}</td></tr>'
    else:
        body = ""
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
