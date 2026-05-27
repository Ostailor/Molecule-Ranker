from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.observability import ObservabilityMiddleware, metrics
from molecule_ranker.server.dependencies import require_api_key
from molecule_ranker.server.routes import (
    artifacts,
    auth,
    codex,
    experiments,
    integrations,
    platform,
    projects,
    review,
    webhooks,
)
from molecule_ranker.server.security import (
    HealthResponse,
    ReadyResponse,
    SecurityConfig,
    VersionResponse,
    http_exception_response,
    install_security_middleware,
    normalized_cors_origins,
    public_bind_allowed,
    unhandled_exception_response,
    validation_exception_response,
)
from molecule_ranker.web import router as web_router
from molecule_ranker.workspace.store import ProjectWorkspaceStore

OPENAPI_TAGS = [
    {"name": "health", "description": "Operational health and version endpoints."},
    {"name": "auth", "description": "Hosted authentication and service account tokens."},
    {"name": "projects", "description": "Project workspaces and sharing."},
    {"name": "artifacts", "description": "Permission-checked artifact metadata and downloads."},
    {"name": "codex", "description": "Guarded Codex job endpoints."},
    {"name": "integrations", "description": "V0.9 external research-system integrations."},
    {"name": "webhooks", "description": "Signed external integration webhook ingestion."},
    {"name": "platform", "description": "Hosted platform administration and audit."},
    {"name": "web", "description": "Server-rendered hosted dashboard."},
]


def _http_exception_handler(request: Request, exc: Exception) -> Response:
    if isinstance(exc, HTTPException):
        return http_exception_response(request, exc)
    return unhandled_exception_response(request, exc)


def _validation_exception_handler(request: Request, exc: Exception) -> Response:
    if isinstance(exc, RequestValidationError):
        return validation_exception_response(request, exc)
    return unhandled_exception_response(request, exc)


def create_app(
    *,
    root_dir: Path = Path("."),
    enable_codex_backbone: bool = False,
    codex_config: CodexBackboneConfig | None = None,
    codex_provider: Any | None = None,
    api_key: str | None = None,
    hosted_mode: bool = False,
    platform_database_url: str | None = None,
    platform_db_path: Path | None = None,
    auth_secret: str | None = None,
    bootstrap_admin_email: str | None = None,
    bootstrap_admin_password: str | None = None,
    oidc_issuer: str | None = None,
    oidc_client_id: str | None = None,
    oidc_client_secret_env_var: str | None = None,
    oidc_redirect_url: str | None = None,
    cors_allow_origins: list[str] | None = None,
    max_request_bytes: int = 1_048_576,
    max_upload_bytes: int = 10_485_760,
    auth_rate_limit: int = 20,
    codex_rate_limit: int = 30,
) -> FastAPI:
    app = FastAPI(
        title="molecule-ranker API",
        version=__version__,
        description=(
            "Local API by default. Hosted mode adds users, teams, RBAC, jobs, "
            "audit logs, guarded Codex worker orchestration, and V0.9 integrations."
        ),
        openapi_tags=OPENAPI_TAGS,
    )
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_response)
    cors_origins = normalized_cors_origins(cors_allow_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        )
    install_security_middleware(
        app,
        SecurityConfig(
            cors_allow_origins=cors_origins,
            max_request_bytes=max_request_bytes,
            max_upload_bytes=max_upload_bytes,
            auth_rate_limit=auth_rate_limit,
            codex_rate_limit=codex_rate_limit,
        ),
    )
    app.add_middleware(ObservabilityMiddleware)
    resolved_root = root_dir.resolve()
    config = codex_config or CodexBackboneConfig(
        enable_codex_backbone=enable_codex_backbone,
        codex_working_dir=resolved_root,
    )
    app.state.root_dir = resolved_root
    app.state.workspace_store = ProjectWorkspaceStore(resolved_root)
    app.state.enable_codex_backbone = bool(config.enable_codex_backbone)
    app.state.codex_config = config
    app.state.codex_provider = codex_provider
    app.state.api_key = api_key
    app.state.hosted_mode = hosted_mode
    app.state.auth_secret = auth_secret or "local-development-hosted-secret-change-me-32"
    app.state.oidc_issuer = oidc_issuer
    app.state.oidc_client_id = oidc_client_id
    app.state.oidc_client_secret_env_var = oidc_client_secret_env_var
    app.state.oidc_redirect_url = oidc_redirect_url
    if hosted_mode:
        database = PlatformDatabase(
            resolved_root,
            database_url=platform_database_url,
            db_path=platform_db_path,
        )
        database.auth_secret = app.state.auth_secret
        app.state.platform_database = database
        if bootstrap_admin_email and bootstrap_admin_password and not database.list_users():
            database.create_user(
                email=bootstrap_admin_email,
                password=bootstrap_admin_password,
                roles=["platform_admin", "user"],
            )
    else:
        app.state.platform_database = None
    app.mount(
        "/static/dashboard",
        StaticFiles(directory=Path(__file__).parents[1] / "web" / "static"),
        name="dashboard-static",
    )

    @app.get("/health", tags=["health"], response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            local_only=not bool(app.state.hosted_mode),
            hosted_mode=bool(app.state.hosted_mode),
            host="127.0.0.1",
            codex_enabled=bool(app.state.enable_codex_backbone),
            version=__version__,
        )

    @app.get("/ready", tags=["health"], response_model=ReadyResponse)
    def ready() -> ReadyResponse:
        database = getattr(app.state, "platform_database", None)
        if database is None:
            return ReadyResponse(ok=True, checks={"workspace_store": True})
        health_payload = database.health()
        return ReadyResponse(
            ok=bool(health_payload["ok"]),
            database=str(health_payload["database"]),
            checks=health_payload,
        )

    @app.get("/version", tags=["health"], response_model=VersionResponse)
    def version() -> VersionResponse:
        return VersionResponse(version=__version__)

    @app.get("/metrics", tags=["health"], response_class=PlainTextResponse)
    def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(metrics.render_prometheus())

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    dependencies = [Depends(require_api_key)]
    app.include_router(auth.router)
    app.include_router(web_router)
    app.include_router(platform.router)
    app.include_router(projects.router, dependencies=dependencies)
    app.include_router(artifacts.router, dependencies=dependencies)
    app.include_router(codex.router, dependencies=dependencies)
    app.include_router(review.router, dependencies=dependencies)
    app.include_router(experiments.router, dependencies=dependencies)
    app.include_router(integrations.router, dependencies=dependencies)
    app.include_router(webhooks.router)
    return app


def run_local_server(
    *,
    root_dir: Path = Path("."),
    host: str = "127.0.0.1",
    port: int = 8765,
    enable_codex_backbone: bool = False,
    api_key: str | None = None,
    hosted_mode: bool = False,
    auth_secret: str | None = None,
    platform_database_url: str | None = None,
    platform_db_path: Path | None = None,
    allow_public_bind: bool = False,
) -> None:
    import uvicorn

    public_bind_allowed(host, allow_public_bind=allow_public_bind)
    app = create_app(
        root_dir=root_dir,
        enable_codex_backbone=enable_codex_backbone,
        api_key=api_key,
        hosted_mode=hosted_mode,
        auth_secret=auth_secret,
        platform_database_url=platform_database_url,
        platform_db_path=platform_db_path,
    )
    uvicorn.run(app, host=host, port=port)
