from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from molecule_ranker.platform.auth import (
    AuthError,
    AuthTokenConfig,
    OIDCConfig,
    PasswordPolicyConfig,
    SessionTokenManager,
    generate_opaque_token,
)
from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.observability import metrics
from molecule_ranker.platform.rbac import require_platform_admin
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.platform.sso import (
    HTTPOIDCMetadataProvider,
    validate_id_token,
    validate_oidc_discovery_document,
)
from molecule_ranker.server.dependencies import current_user, platform_database
from molecule_ranker.server.routes.platform import public_user

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class ServiceTokenCreateRequest(BaseModel):
    name: str
    user_id: str
    scopes: list[str] = Field(default_factory=list)
    expires_in_seconds: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceTokenRevokeRequest(BaseModel):
    token_id: str


class OIDCCallbackRequest(BaseModel):
    id_token: str


@router.post("/auth/login")
def login(
    request: LoginRequest,
    http_request: Request,
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    try:
        user = database.authenticate_user(email=request.email, password=request.password)
        return _issue_token_pair(
            database=database,
            user=user,
            auth_secret=http_request.app.state.auth_secret,
            metadata={"flow": "api_bearer"},
        )
    except AuthError as exc:
        metrics.increment("auth_failures_total")
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/auth/refresh")
def refresh_token(
    request: RefreshRequest,
    http_request: Request,
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    try:
        user, session_id = database.refresh_auth_session(refresh_token=request.refresh_token)
        manager = SessionTokenManager(http_request.app.state.auth_secret)
        config = AuthTokenConfig()
        access_token = manager.issue(
            user_id=user.user_id,
            roles=user.roles,
            token_type="access",
            session_id=session_id,
            ttl_seconds=config.access_token_ttl_seconds,
        )
    except AuthError as exc:
        metrics.increment("auth_failures_total")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": config.access_token_ttl_seconds,
    }


@router.post("/auth/logout")
def logout(
    request: LogoutRequest,
    http_request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    authorization = http_request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    if token:
        try:
            payload = SessionTokenManager(http_request.app.state.auth_secret).verify(token)
            session_id = payload.get("sid")
            if session_id:
                database.revoke_auth_session(session_id=str(session_id), actor_user_id=user.user_id)
        except AuthError:
            pass
    if request.refresh_token:
        try:
            _refreshed_user, session_id = database.refresh_auth_session(
                refresh_token=request.refresh_token
            )
            database.revoke_auth_session(session_id=session_id, actor_user_id=user.user_id)
        except AuthError:
            pass
    return {"logged_out": True}


@router.get("/auth/me")
def auth_me(user: Annotated[UserAccount, Depends(current_user)]) -> dict[str, Any]:
    return {"user": public_user(user)}


@router.get("/me")
def me(user: Annotated[UserAccount, Depends(current_user)]) -> dict[str, Any]:
    return {"user": public_user(user)}


@router.get("/auth/oidc/login")
def oidc_login(request: Request) -> dict[str, Any]:
    config = _oidc_config(request)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="OIDC is not configured.")
    provider = HTTPOIDCMetadataProvider(config.discovery_url or _default_discovery_url(config))
    try:
        discovery = validate_oidc_discovery_document(config, provider.discovery_document())
    except AuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not config.redirect_url:
        return {
            "issuer": discovery.issuer,
            "authorization_endpoint": discovery.authorization_endpoint,
            "jwks_uri": discovery.jwks_uri,
        }
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_url,
            "response_type": "code",
            "scope": "openid email profile groups",
        }
    )
    return {
        "issuer": discovery.issuer,
        "authorization_url": f"{discovery.authorization_endpoint}?{query}",
    }


@router.get("/auth/oidc/callback")
def oidc_callback_get(request: Request) -> dict[str, Any]:
    config = _oidc_config(request)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="OIDC is not configured.")
    id_token = request.query_params.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=400,
            detail="OIDC callback requires an ID token for validation.",
        )
    return _complete_oidc_callback(id_token=id_token, request=request)


@router.post("/auth/oidc/callback")
def oidc_callback(
    callback: OIDCCallbackRequest,
    request: Request,
) -> dict[str, Any]:
    config = _oidc_config(request)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="OIDC is not configured.")
    return _complete_oidc_callback(id_token=callback.id_token, request=request)


@router.post("/auth/token/create")
def create_service_token(
    request: ServiceTokenCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_admin_token_scope(user, "admin:manage_users")
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
        "scopes": request.scopes,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.post("/auth/token/revoke")
def revoke_service_token(
    request: ServiceTokenRevokeRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_admin_token_scope(user, "admin:manage_users")
    require_platform_admin(user)
    revoked = database.revoke_service_account_token(
        token_id=request.token_id,
        actor_user_id=user.user_id,
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="Service account token not found.")
    return {"revoked": True, "token_id": request.token_id}


def _issue_token_pair(
    *,
    database: PlatformDatabase,
    user: UserAccount,
    auth_secret: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    config = AuthTokenConfig()
    refresh_token = generate_opaque_token(prefix="mrr")
    session_id = database.create_auth_session(
        user_id=user.user_id,
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(seconds=config.refresh_token_ttl_seconds),
        metadata=metadata,
    )
    access_token = SessionTokenManager(auth_secret).issue(
        user_id=user.user_id,
        roles=user.roles,
        token_type="access",
        session_id=session_id,
        ttl_seconds=config.access_token_ttl_seconds,
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": config.access_token_ttl_seconds,
        "user": public_user(user),
    }


def _complete_oidc_callback(*, id_token: str, request: Request) -> dict[str, Any]:
    config = _oidc_config(request)
    provider = getattr(request.app.state, "oidc_metadata_provider", None)
    if provider is None:
        provider = HTTPOIDCMetadataProvider(config.discovery_url or _default_discovery_url(config))
    try:
        identity = validate_id_token(id_token, config=config, provider=provider)
    except AuthError as exc:
        metrics.increment("auth_failures_total")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not identity.email:
        raise HTTPException(status_code=401, detail="OIDC email claim is required.")
    database: PlatformDatabase = request.app.state.platform_database
    user = database.upsert_oidc_user(
        email=identity.email,
        subject=identity.subject,
        issuer=identity.issuer,
        roles=identity.roles,
        display_name=str(identity.claims.get("name")) if identity.claims.get("name") else None,
        metadata={"groups": identity.groups},
    )
    return _issue_token_pair(
        database=database,
        user=user,
        auth_secret=request.app.state.auth_secret,
        metadata={"flow": "oidc", "issuer": identity.issuer},
    )


def _oidc_config(request: Request) -> OIDCConfig:
    return OIDCConfig(
        issuer=getattr(request.app.state, "oidc_issuer", None),
        client_id=getattr(request.app.state, "oidc_client_id", None),
        client_secret_env_var=getattr(request.app.state, "oidc_client_secret_env_var", None),
        redirect_url=getattr(request.app.state, "oidc_redirect_url", None),
        discovery_url=getattr(request.app.state, "oidc_discovery_url", None),
        allowed_email_domains=list(getattr(request.app.state, "oidc_allowed_email_domains", [])),
        group_role_mapping=dict(getattr(request.app.state, "oidc_group_role_mapping", {})),
        allow_insecure_http_for_dev=bool(
            getattr(request.app.state, "oidc_allow_insecure_http_for_dev", False)
        ),
    )


def _default_discovery_url(config: OIDCConfig) -> str:
    if not config.issuer:
        raise AuthError("OIDC issuer is required.")
    return f"{config.issuer.rstrip('/')}/.well-known/openid-configuration"


def _require_admin_token_scope(user: UserAccount, permission: str) -> None:
    if user.auth_provider != "service_account":
        return
    scopes = {str(scope) for scope in user.metadata.get("scopes", [])}
    if "*" not in scopes and permission not in scopes:
        raise HTTPException(status_code=403, detail="Permission denied.")


__all__ = ["PasswordPolicyConfig", "router"]
