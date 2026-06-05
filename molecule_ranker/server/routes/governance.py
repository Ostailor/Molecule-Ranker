from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.audits import AgentGovernanceAuditAnalyticsBuilder
from molecule_ranker.agent_governance.capability_grants import (
    AuthorizationActorType,
    CapabilityGrantAuthorization,
    CapabilityGrantError,
    CapabilityGrantManager,
    CapabilityGrantStore,
)
from molecule_ranker.agent_governance.certification import (
    AgentCertificationAuthorization,
    AgentCertificationManager,
    AgentCertificationStore,
    CertificationActorType,
    CertificationEvaluationResult,
)
from molecule_ranker.agent_governance.incidents import (
    AgentIncidentError,
    AgentIncidentManager,
    IncidentStore,
)
from molecule_ranker.agent_governance.run_control import (
    AgentRunControlManager,
    RunControlRequest,
    RunControlStore,
)
from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentCapabilityGrantStatus,
    AgentCapabilityScopeType,
    AgentCertificationType,
    AgentGovernanceAutonomyLevel,
    AgentGovernancePolicy,
    AgentGovernanceReport,
    AgentRunControlType,
    AgentType,
)
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.server.dependencies import current_user, platform_database

router = APIRouter(prefix="/governance", tags=["governance"])

ModelT = TypeVar("ModelT", bound=BaseModel)
SECRET_KEY_RE = re.compile(r"(password|secret|token|api[_-]?key|credential)", re.I)


class CapabilityGrantCreateRequest(BaseModel):
    agent_id: str
    agent_type: AgentType
    granted_capability: str
    scope_type: AgentCapabilityScopeType
    scope_id: str | None = None
    expires_at: datetime | None = None
    status: AgentCapabilityGrantStatus = "active"
    grant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CertificationCreateRequest(BaseModel):
    agent_id: str
    certification_type: AgentCertificationType
    certified_autonomy_level: AgentGovernanceAutonomyLevel
    passed: bool = True
    score: float = Field(default=1.0, ge=0, le=1)
    evaluation_artifact_ids: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    limitations: list[str] = Field(default_factory=list)
    certification_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentResolveRequest(BaseModel):
    rationale: str
    false_positive: bool = False


class RunControlApplyRequest(BaseModel):
    control_type: AgentRunControlType
    reason: str
    org_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    expires_at: datetime | None = None
    control_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/policies")
def list_policies(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    policies = _load_state_models(request, "policies.json", "policies", AgentGovernancePolicy)
    return {"policies": _redact_json([policy.model_dump(mode="json") for policy in policies])}


@router.post("/policies")
def create_policy(
    policy: AgentGovernancePolicy,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(
        user,
        database,
        "governance:write",
        org_id=policy.org_id,
        project_id=policy.project_id,
    )
    policies = [
        item
        for item in _load_state_models(request, "policies.json", "policies", AgentGovernancePolicy)
        if item.policy_id != policy.policy_id
    ]
    policies.append(policy)
    _save_state_models(request, "policies.json", "policies", policies)
    return {"policy": _redact_json(policy.model_dump(mode="json"))}


@router.get("/grants")
def list_grants(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    agent_id: str | None = None,
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    manager = _grant_manager(request)
    grants = manager.list_grants(agent_id=agent_id)
    return {"grants": _redact_json([grant.model_dump(mode="json") for grant in grants])}


@router.post("/grants")
def create_grant(
    body: CapabilityGrantCreateRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:approve")
    decision = _grant_manager(request).create_grant(
        agent_id=body.agent_id,
        agent_type=body.agent_type,
        granted_capability=body.granted_capability,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        authorization=CapabilityGrantAuthorization(
            actor_id=user.user_id,
            actor_type=_actor_type(user),
            permission_scope={"*"},
        ),
        expires_at=body.expires_at,
        status=body.status,
        grant_id=body.grant_id,
        metadata=body.metadata,
    )
    if not decision.allowed or decision.grant is None:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {
        "grant": _redact_json(decision.grant.model_dump(mode="json")),
        "audit_event": _redact_json(decision.audit_event.model_dump(mode="json")),
    }


@router.delete("/grants/{grant_id}")
def revoke_grant(
    grant_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:admin")
    try:
        grant = _grant_manager(request).revoke_grant(grant_id, revoked_by=user.user_id)
    except CapabilityGrantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"grant": _redact_json(grant.model_dump(mode="json"))}


@router.get("/budgets")
def list_budgets(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    budgets = _load_state_models(request, "budgets.json", "budgets", AgentAutonomyBudget)
    return {"budgets": _redact_json([budget.model_dump(mode="json") for budget in budgets])}


@router.post("/budgets")
def create_budget(
    budget: AgentAutonomyBudget,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(
        user,
        database,
        "governance:write",
        org_id=budget.org_id,
        project_id=budget.project_id,
    )
    budgets = [
        item
        for item in _load_state_models(request, "budgets.json", "budgets", AgentAutonomyBudget)
        if item.budget_id != budget.budget_id
    ]
    budgets.append(budget)
    _save_state_models(request, "budgets.json", "budgets", budgets)
    return {"budget": _redact_json(budget.model_dump(mode="json"))}


@router.get("/certifications")
def list_certifications(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
    agent_id: str | None = None,
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    certifications = _certification_manager(request).list_certifications(agent_id=agent_id)
    return {
        "certifications": _redact_json(
            [certification.model_dump(mode="json") for certification in certifications]
        )
    }


@router.post("/certifications")
def create_certification(
    body: CertificationCreateRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:approve")
    evaluation_results = [
        CertificationEvaluationResult(
            suite_name="hosted-governance-api",
            passed=body.passed,
            score=body.score,
            artifact_id=artifact_id,
        )
        for artifact_id in body.evaluation_artifact_ids
    ] or [
        CertificationEvaluationResult(
            suite_name="hosted-governance-api",
            passed=body.passed,
            score=body.score,
        )
    ]
    decision = _certification_manager(request).certify_agent(
        agent_id=body.agent_id,
        certification_type=body.certification_type,
        certified_autonomy_level=body.certified_autonomy_level,
        authorization=AgentCertificationAuthorization(
            actor_id=user.user_id,
            actor_type=_certification_actor_type(user),
            permission_scope={"*"},
        ),
        evaluation_results=evaluation_results,
        expires_at=body.expires_at,
        limitations=body.limitations,
        certification_id=body.certification_id,
        metadata=body.metadata,
    )
    if not decision.allowed or decision.certification is None:
        raise HTTPException(status_code=403, detail=decision.reason)
    return {
        "certification": _redact_json(decision.certification.model_dump(mode="json")),
        "audit_event": _redact_json(decision.audit_event.model_dump(mode="json")),
    }


@router.get("/incidents")
def list_incidents(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    incidents = _incident_manager(request).list_incidents()
    return {"incidents": _redact_json([incident.model_dump(mode="json") for incident in incidents])}


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: str,
    body: IncidentResolveRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:incident_manage")
    try:
        incident = _incident_manager(request).resolve_incident(
            incident_id,
            resolved_by=user.user_id,
            rationale=body.rationale,
            false_positive=body.false_positive,
        )
    except AgentIncidentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"incident": _redact_json(incident.model_dump(mode="json"))}


@router.get("/reports")
def list_reports(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(user, database, "governance:read")
    reports = _load_state_models(request, "reports.json", "reports", AgentGovernanceReport)
    if not reports:
        analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
            events=[],
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
        )
        reports = [analytics.report]
    return {"reports": _redact_json([report.model_dump(mode="json") for report in reports])}


@router.post("/run-controls")
def apply_run_control(
    body: RunControlApplyRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_governance_permission(
        user,
        database,
        "governance:admin",
        org_id=body.org_id,
        project_id=body.project_id,
    )
    manager = _run_control_manager(request)
    control = manager.apply_control(
        control_type=body.control_type,
        reason=body.reason,
        applied_by=user.user_id,
        org_id=body.org_id,
        project_id=body.project_id,
        agent_id=body.agent_id,
        expires_at=body.expires_at,
        control_id=body.control_id,
        metadata=body.metadata,
    )
    decision = manager.evaluate(
        RunControlRequest(
            agent_id=body.agent_id,
            org_id=body.org_id,
            project_id=body.project_id,
            metadata={"source": "hosted-governance-api"},
        )
    )
    return {
        "control": _redact_json(control.model_dump(mode="json")),
        "decision": _redact_json(decision.model_dump(mode="json")),
    }


def _require_governance_permission(
    user: UserAccount,
    database: PlatformDatabase,
    permission: str,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
) -> None:
    metadata_permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "*" in metadata_permissions
        or "governance:admin" in metadata_permissions
        or permission in metadata_permissions
        or has_permission(user, permission, org_id=org_id, project_id=project_id, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"Governance permission denied: {permission}")


def _actor_type(user: UserAccount) -> AuthorizationActorType:
    if user.auth_provider == "service_account":
        return "service_account"
    if user.is_admin:
        return "admin"
    return "human"


def _certification_actor_type(user: UserAccount) -> CertificationActorType:
    return cast(CertificationActorType, _actor_type(user))


def _state_dir(request: Request) -> Path:
    path = Path(request.app.state.root_dir) / ".molecule-ranker" / "agent-governance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _grant_manager(request: Request) -> CapabilityGrantManager:
    return CapabilityGrantManager(store=CapabilityGrantStore(_state_dir(request) / "grants.json"))


def _certification_manager(request: Request) -> AgentCertificationManager:
    return AgentCertificationManager(
        store=AgentCertificationStore(_state_dir(request) / "certifications.json")
    )


def _incident_manager(request: Request) -> AgentIncidentManager:
    return AgentIncidentManager(store=IncidentStore(_state_dir(request) / "incidents.json"))


def _run_control_manager(request: Request) -> AgentRunControlManager:
    return AgentRunControlManager(
        store=RunControlStore(_state_dir(request) / "run-controls.json")
    )


def _load_state(request: Request, filename: str, key: str) -> list[Any]:
    path = _state_dir(request) / filename
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    values = raw.get(key, [])
    return values if isinstance(values, list) else []


def _save_state_models(
    request: Request,
    filename: str,
    key: str,
    values: Sequence[BaseModel],
) -> None:
    path = _state_dir(request) / filename
    payload = {key: [value.model_dump(mode="json") for value in values]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_state_models(
    request: Request,
    filename: str,
    key: str,
    model: type[ModelT],
) -> list[ModelT]:
    return [
        model.model_validate(item)
        for item in _load_state(request, filename, key)
        if isinstance(item, dict)
    ]


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value
