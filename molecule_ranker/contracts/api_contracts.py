from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

API_CONTRACT_VERSION = "api.v1"
API_ROUTE_VERSION = "v1"

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
StabilityLevel = Literal["stable", "beta", "internal"]


@dataclass(frozen=True)
class ApiRouteContract:
    route: str
    method: HttpMethod
    auth_required: bool
    permission_required: str | None
    request_schema: str
    response_schema: str
    error_schema: str
    stability_level: StabilityLevel
    version: str = API_ROUTE_VERSION
    tags: tuple[str, ...] = ()
    compatibility_notes: str = (
        "V1.0 allows additive response fields and optional request fields. "
        "Existing required fields, status semantics, and error envelopes are stable."
    )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.route.startswith("/api/v1/"):
            errors.append(f"{self.route}: route must start with /api/v1/.")
        if self.version != API_ROUTE_VERSION:
            errors.append(f"{self.route}: version must be {API_ROUTE_VERSION}.")
        if self.stability_level not in {"stable", "beta", "internal"}:
            errors.append(f"{self.route}: invalid stability level.")
        if not self.request_schema:
            errors.append(f"{self.route}: request_schema is required.")
        if not self.response_schema:
            errors.append(f"{self.route}: response_schema is required.")
        if not self.error_schema:
            errors.append(f"{self.route}: error_schema is required.")
        return errors


def _contract(
    key: str,
    route: str,
    method: HttpMethod,
    *,
    auth_required: bool,
    permission_required: str | None,
    request_schema: str,
    response_schema: str,
    stability_level: StabilityLevel = "stable",
    tags: tuple[str, ...],
    compatibility_notes: str | None = None,
) -> tuple[str, ApiRouteContract]:
    return (
        key,
        ApiRouteContract(
            route=route,
            method=method,
            auth_required=auth_required,
            permission_required=permission_required,
            request_schema=request_schema,
            response_schema=response_schema,
            error_schema="ErrorResponse",
            stability_level=stability_level,
            tags=tags,
            compatibility_notes=compatibility_notes or ApiRouteContract.compatibility_notes,
        ),
    )


_CONTRACT_ITEMS: tuple[tuple[str, ApiRouteContract], ...] = (
    _contract(
        "health",
        "/api/v1/health",
        "GET",
        auth_required=False,
        permission_required=None,
        request_schema="EmptyRequest",
        response_schema="HealthResponse",
        tags=("v1-health",),
        compatibility_notes=(
            "Unauthenticated liveness fields are stable; deployment checks may be additive."
        ),
    ),
    _contract(
        "ready",
        "/api/v1/ready",
        "GET",
        auth_required=False,
        permission_required=None,
        request_schema="EmptyRequest",
        response_schema="ReadyResponse",
        tags=("v1-health",),
    ),
    _contract(
        "version",
        "/api/v1/version",
        "GET",
        auth_required=False,
        permission_required=None,
        request_schema="EmptyRequest",
        response_schema="VersionResponse",
        tags=("v1-health",),
    ),
    _contract(
        "metrics",
        "/api/v1/metrics",
        "GET",
        auth_required=False,
        permission_required=None,
        request_schema="EmptyRequest",
        response_schema="PrometheusTextResponse",
        stability_level="internal",
        tags=("v1-health",),
    ),
    _contract(
        "auth_login",
        "/api/v1/auth/login",
        "POST",
        auth_required=False,
        permission_required=None,
        request_schema="LoginRequest",
        response_schema="TokenPairResponse",
        tags=("auth",),
    ),
    _contract(
        "auth_refresh",
        "/api/v1/auth/refresh",
        "POST",
        auth_required=False,
        permission_required=None,
        request_schema="RefreshRequest",
        response_schema="AccessTokenResponse",
        tags=("auth",),
    ),
    _contract(
        "auth_logout",
        "/api/v1/auth/logout",
        "POST",
        auth_required=True,
        permission_required=None,
        request_schema="LogoutRequest",
        response_schema="LogoutResponse",
        tags=("auth",),
    ),
    _contract(
        "auth_me",
        "/api/v1/auth/me",
        "GET",
        auth_required=True,
        permission_required=None,
        request_schema="EmptyRequest",
        response_schema="CurrentUserResponse",
        tags=("auth",),
    ),
    _contract(
        "auth_token_create",
        "/api/v1/auth/token/create",
        "POST",
        auth_required=True,
        permission_required="admin:manage_users",
        request_schema="ServiceTokenCreateRequest",
        response_schema="ServiceTokenCreateResponse",
        stability_level="internal",
        tags=("auth", "platform"),
    ),
    _contract(
        "auth_token_revoke",
        "/api/v1/auth/token/revoke",
        "POST",
        auth_required=True,
        permission_required="admin:manage_users",
        request_schema="ServiceTokenRevokeRequest",
        response_schema="ServiceTokenRevokeResponse",
        stability_level="internal",
        tags=("auth", "platform"),
    ),
    _contract(
        "projects_list",
        "/api/v1/projects",
        "GET",
        auth_required=True,
        permission_required="project:read",
        request_schema="ProjectListQuery",
        response_schema="ProjectListResponse",
        tags=("projects",),
    ),
    _contract(
        "projects_create",
        "/api/v1/projects",
        "POST",
        auth_required=True,
        permission_required="project:create",
        request_schema="ProjectCreateRequest",
        response_schema="ProjectWorkspace",
        tags=("projects",),
    ),
    _contract(
        "projects_get",
        "/api/v1/projects/{project_id}",
        "GET",
        auth_required=True,
        permission_required="project:read",
        request_schema="ProjectPathRequest",
        response_schema="ProjectWorkspace",
        tags=("projects",),
    ),
    _contract(
        "project_share",
        "/api/v1/projects/{project_id}/share",
        "POST",
        auth_required=True,
        permission_required="project:update",
        request_schema="ProjectShareRequest",
        response_schema="ProjectShareResponse",
        tags=("projects", "platform"),
    ),
    _contract(
        "project_permissions",
        "/api/v1/projects/{project_id}/permissions",
        "GET",
        auth_required=True,
        permission_required="project:read",
        request_schema="ProjectPathRequest",
        response_schema="ProjectPermissionsResponse",
        tags=("projects", "platform"),
    ),
    _contract(
        "runs_codex_explain",
        "/api/v1/runs/{run_id}/codex/explain",
        "POST",
        auth_required=True,
        permission_required="codex:read",
        request_schema="RunCodexExplainRequest",
        response_schema="CodexTaskResult",
        stability_level="beta",
        tags=("codex", "runs"),
    ),
    _contract(
        "artifacts_list",
        "/api/v1/projects/{project_id}/artifacts",
        "GET",
        auth_required=True,
        permission_required="artifact:read",
        request_schema="ProjectPathRequest",
        response_schema="ArtifactListResponse",
        tags=("artifacts",),
    ),
    _contract(
        "artifacts_download",
        "/api/v1/projects/{project_id}/artifacts/{artifact_id}/download",
        "GET",
        auth_required=True,
        permission_required="artifact:export",
        request_schema="ArtifactDownloadRequest",
        response_schema="ArtifactDownloadResponse",
        tags=("artifacts",),
    ),
    _contract(
        "review_health",
        "/api/v1/review/health",
        "GET",
        auth_required=True,
        permission_required="review:read",
        request_schema="EmptyRequest",
        response_schema="ComponentHealthResponse",
        stability_level="beta",
        tags=("review",),
    ),
    _contract(
        "experiments_health",
        "/api/v1/experiments/health",
        "GET",
        auth_required=True,
        permission_required="experiment:read",
        request_schema="EmptyRequest",
        response_schema="ComponentHealthResponse",
        stability_level="beta",
        tags=("experiments",),
    ),
    _contract(
        "active_learning_health",
        "/api/v1/active-learning/health",
        "GET",
        auth_required=True,
        permission_required="experiment:read",
        request_schema="EmptyRequest",
        response_schema="ComponentHealthResponse",
        stability_level="beta",
        tags=("v1-active-learning",),
    ),
    _contract(
        "integrations_catalog",
        "/api/v1/integrations/catalog",
        "GET",
        auth_required=True,
        permission_required="integration:read",
        request_schema="IntegrationCatalogQuery",
        response_schema="IntegrationCatalogResponse",
        tags=("integrations",),
    ),
    _contract(
        "integrations_systems",
        "/api/v1/integrations/systems",
        "GET",
        auth_required=True,
        permission_required="integration:read",
        request_schema="IntegrationSystemsQuery",
        response_schema="IntegrationSystemsResponse",
        tags=("integrations",),
    ),
    _contract(
        "integrations_system_create",
        "/api/v1/integrations/systems",
        "POST",
        auth_required=True,
        permission_required="integration:manage",
        request_schema="ExternalSystemCreateRequest",
        response_schema="ExternalSystemResponse",
        tags=("integrations",),
    ),
    _contract(
        "integrations_sync_preview",
        "/api/v1/integrations/connectors/{connector_id}/sync/preview",
        "POST",
        auth_required=True,
        permission_required="integration:sync",
        request_schema="ConnectorSyncPreviewRequest",
        response_schema="ConnectorSyncPreviewResponse",
        stability_level="beta",
        tags=("integrations",),
    ),
    _contract(
        "integrations_sync_jobs",
        "/api/v1/integrations/connectors/{connector_id}/sync/jobs",
        "POST",
        auth_required=True,
        permission_required="integration:sync",
        request_schema="ConnectorSyncJobRequest",
        response_schema="ConnectorSyncJobResponse",
        stability_level="beta",
        tags=("integrations",),
    ),
    _contract(
        "integrations_mapping_approve",
        "/api/v1/integrations/mappings/{mapping_id}/approve",
        "POST",
        auth_required=True,
        permission_required="integration:approve_mapping",
        request_schema="MappingDecisionRequest",
        response_schema="MappingDecisionResponse",
        tags=("integrations",),
    ),
    _contract(
        "jobs_get",
        "/api/v1/jobs/{job_id}",
        "GET",
        auth_required=True,
        permission_required="codex:read",
        request_schema="JobPathRequest",
        response_schema="JobStatusResponse",
        stability_level="internal",
        tags=("codex", "jobs"),
    ),
    _contract(
        "jobs_run_next",
        "/api/v1/jobs/run-next",
        "POST",
        auth_required=True,
        permission_required="codex:run",
        request_schema="JobRunNextRequest",
        response_schema="JobRunNextResponse",
        stability_level="internal",
        tags=("codex", "jobs"),
    ),
    _contract(
        "codex_project_summarize",
        "/api/v1/projects/{project_id}/codex/summarize",
        "POST",
        auth_required=True,
        permission_required="codex:read",
        request_schema="ProjectCodexSummarizeRequest",
        response_schema="CodexTaskResult",
        stability_level="beta",
        tags=("codex", "projects"),
    ),
    _contract(
        "codex_run_task",
        "/api/v1/codex/run-task",
        "POST",
        auth_required=True,
        permission_required="codex:run",
        request_schema="CodexTask",
        response_schema="CodexTaskResult",
        stability_level="internal",
        tags=("codex",),
    ),
    _contract(
        "admin_health",
        "/api/v1/admin/health",
        "GET",
        auth_required=True,
        permission_required="admin:view_audit",
        request_schema="EmptyRequest",
        response_schema="AdminHealthResponse",
        stability_level="internal",
        tags=("platform", "admin"),
    ),
    _contract(
        "admin_audit",
        "/api/v1/admin/audit",
        "GET",
        auth_required=True,
        permission_required="admin:view_audit",
        request_schema="AuditQuery",
        response_schema="AuditEventListResponse",
        stability_level="internal",
        tags=("platform", "admin"),
    ),
    _contract(
        "audit_events",
        "/api/v1/audit/events",
        "GET",
        auth_required=True,
        permission_required="admin:view_audit",
        request_schema="AuditQuery",
        response_schema="AuditEventListResponse",
        stability_level="internal",
        tags=("platform", "admin"),
    ),
)

API_CONTRACTS: dict[str, dict[str, Any]] = {
    key: contract.as_dict() for key, contract in _CONTRACT_ITEMS
}


def list_api_contracts() -> list[ApiRouteContract]:
    return [contract for _key, contract in _CONTRACT_ITEMS]


def validate_api_contracts() -> dict[str, list[str]]:
    return {
        key: errors
        for key, contract in _CONTRACT_ITEMS
        if (errors := contract.validate())
    }


def export_api_contracts() -> dict[str, object]:
    return {
        "api_contract_version": API_CONTRACT_VERSION,
        "route_version": API_ROUTE_VERSION,
        "contracts": API_CONTRACTS,
    }
