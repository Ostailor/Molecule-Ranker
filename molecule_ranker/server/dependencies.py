from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request

from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.platform.auth import AuthError, SessionTokenManager
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if bool(getattr(request.app.state, "hosted_mode", False)):
        request.state.user = _hosted_user_from_authorization(request, authorization)
        return
    expected = getattr(request.app.state, "api_key", None)
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key.")


def codex_provider(request: Request) -> Any:
    if not bool(request.app.state.enable_codex_backbone):
        raise HTTPException(
            status_code=403,
            detail="Codex backbone is disabled. Set enable_codex_backbone=True to opt in.",
        )
    provider = request.app.state.codex_provider
    if provider is not None:
        return provider
    return CodexBackboneProvider(request.app.state.codex_config)


def workspace_store(request: Request) -> ProjectWorkspaceStore:
    return request.app.state.workspace_store


def platform_database(request: Request) -> PlatformDatabase:
    database = getattr(request.app.state, "platform_database", None)
    if database is None:
        raise HTTPException(status_code=404, detail="Hosted platform mode is disabled.")
    return database


def current_user(request: Request) -> UserAccount:
    if not bool(getattr(request.app.state, "hosted_mode", False)):
        return UserAccount(
            user_id="local-user",
            email="local@molecule-ranker.internal",
            display_name="Local user",
            is_active=True,
            is_admin=True,
            auth_provider="service_account",
            metadata={},
        )
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    authorization = request.headers.get("Authorization")
    user = _hosted_user_from_authorization(request, authorization)
    request.state.user = user
    return user


def require_permission(permission: str) -> Callable[..., UserAccount]:
    def dependency(
        request: Request,
        user: Annotated[UserAccount, Depends(current_user)],
    ) -> UserAccount:
        if not bool(getattr(request.app.state, "hosted_mode", False)):
            return user
        database = platform_database(request)
        org_id = request.path_params.get("org_id")
        project_id = request.path_params.get("project_id")
        if user.auth_provider == "service_account" and not _service_token_allows(
            user,
            permission,
        ):
            _audit_permission_denial(
                request=request,
                database=database,
                user=user,
                permission=permission,
                org_id=str(org_id) if org_id is not None else None,
                project_id=str(project_id) if project_id is not None else None,
            )
            raise HTTPException(status_code=403, detail="Permission denied.")
        if has_permission(
            user,
            permission,
            org_id=str(org_id) if org_id is not None else None,
            project_id=str(project_id) if project_id is not None else None,
            database=database,
        ):
            return user
        _audit_permission_denial(
            request=request,
            database=database,
            user=user,
            permission=permission,
            org_id=str(org_id) if org_id is not None else None,
            project_id=str(project_id) if project_id is not None else None,
        )
        raise HTTPException(status_code=403, detail="Permission denied.")

    return dependency


def _service_token_allows(user: UserAccount, permission: str) -> bool:
    scopes = {str(scope) for scope in user.metadata.get("scopes", [])}
    return "*" in scopes or permission in scopes


def _audit_permission_denial(
    *,
    request: Request,
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
    org_id: str | None,
    project_id: str | None,
) -> None:
    if not bool(getattr(request.app.state, "audit_permission_denials", False)):
        return
    database.write_audit(
        "permission_denied",
        actor_user_id=user.user_id,
        org_id=org_id,
        project_id=project_id,
        summary=f"Denied {permission}.",
        object_type="permission",
        object_id=permission,
        metadata={"permission": permission},
    )


def _hosted_user_from_authorization(
    request: Request,
    authorization: str | None,
) -> UserAccount:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required.")
    token = authorization.removeprefix("Bearer ").strip()
    manager = SessionTokenManager(request.app.state.auth_secret)
    try:
        payload = manager.verify(token)
    except AuthError as exc:
        service_user = platform_database(request).authenticate_service_account_token(token)
        if service_user is not None:
            return service_user
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Access token required.")
    database = platform_database(request)
    session_id = payload.get("sid")
    if session_id and not database.auth_session_active(str(session_id)):
        raise HTTPException(status_code=401, detail="Session is not active.")
    user = database.get_user(str(payload["user_id"]))
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="User is not active.")
    return user
