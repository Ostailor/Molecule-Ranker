from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette import status

from molecule_ranker.codex_backbone.guardrails import is_secret_path
from molecule_ranker.platform.observability import set_request_id


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    detail: str
    error: ErrorBody


class HealthResponse(BaseModel):
    ok: bool
    local_only: bool
    hosted_mode: bool
    host: str
    codex_enabled: bool
    version: str


class ReadyResponse(BaseModel):
    ok: bool
    database: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    version: str
    api_contract_version: str | None = None
    artifact_contract_version: str | None = None
    data_contract_version: str | None = None
    warehouse_contract_version: str | None = None


@dataclass(frozen=True)
class SecurityConfig:
    cors_allow_origins: tuple[str, ...] = ()
    max_request_bytes: int = 1_048_576
    max_upload_bytes: int = 10_485_760
    rate_limit_window_seconds: int = 60
    auth_rate_limit: int = 20
    codex_rate_limit: int = 30
    audit_sensitive_actions: bool = True


@dataclass
class InMemoryRateLimiter:
    window_seconds: int
    buckets: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def allow(self, key: str, *, limit: int) -> bool:
        now = time.monotonic()
        bucket = self.buckets[key]
        while bucket and bucket[0] <= now - self.window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


CACHE_MARKERS = (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
SENSITIVE_PATH_PREFIXES = (
    "/auth",
    "/api/v1/auth",
    "/api/v2/auth",
    "/login",
    "/logout",
    "/projects/",
    "/api/v1/projects/",
    "/api/v2/projects/",
    "/codex",
    "/api/v1/codex",
    "/api/v2/codex",
    "/jobs/run-next",
    "/api/v1/jobs/run-next",
    "/api/v2/jobs/run-next",
    "/admin",
    "/api/v1/admin",
    "/api/v2/admin",
    "/data",
)


def install_security_middleware(app: Any, config: SecurityConfig) -> None:
    limiter = InMemoryRateLimiter(window_seconds=config.rate_limit_window_seconds)

    @app.middleware("http")
    async def security_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        set_request_id(request_id)
        too_large = _request_too_large(request, config=config)
        if too_large is not None:
            return add_security_headers(too_large, request_id=request_id)
        rate_limited = _rate_limited_response(request, limiter=limiter, config=config)
        if rate_limited is not None:
            return add_security_headers(rate_limited, request_id=request_id)
        response = await call_next(request)
        if config.audit_sensitive_actions:
            _audit_sensitive_action(request, response)
        return add_security_headers(response, request_id=request_id)


def add_security_headers(response: Response, *, request_id: str) -> Response:
    response.headers.setdefault("X-Request-ID", request_id)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        ),
    )
    return response


def http_exception_response(request: Request, exc: HTTPException) -> Response:
    if 300 <= exc.status_code < 400 and exc.headers and "Location" in exc.headers:
        return Response(status_code=exc.status_code, headers=exc.headers)
    message = str(exc.detail)
    return structured_error_response(
        request,
        status_code=exc.status_code,
        code=_error_code(exc.status_code),
        message=message,
        headers=exc.headers,
    )


def validation_exception_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    return structured_error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message="Request validation failed.",
        detail={"errors": exc.errors()},
    )


def unhandled_exception_response(request: Request, _exc: Exception) -> JSONResponse:
    return structured_error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="Internal server error.",
    )


def structured_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
    detail: Any | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    if not request_id:
        request_id = _request_id(request)
    payload: dict[str, Any] = {
        "detail": message,
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        },
    }
    if detail is not None:
        payload["detail"] = detail
    response = JSONResponse(payload, status_code=status_code, headers=headers)
    return add_security_headers(response, request_id=request_id)  # type: ignore[return-value]


def safe_artifact_path(path: Path, *, root_dir: Path) -> Path:
    resolved_root = root_dir.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="Artifact path is outside the project.",
        ) from exc
    lowered = str(resolved).lower()
    if any(marker in lowered for marker in CACHE_MARKERS):
        raise HTTPException(status_code=403, detail="Cache files are not exposed.")
    if resolved.name == ".env" or is_secret_path(resolved):
        raise HTTPException(status_code=403, detail="Secret files are not exposed.")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found.")
    return resolved


def reject_suspicious_identifier(value: str, *, label: str) -> None:
    if ".." in value or "/" in value or "\\" in value:
        raise HTTPException(status_code=403, detail=f"Invalid {label}.")


def public_bind_allowed(host: str, *, allow_public_bind: bool) -> None:
    if host in {"0.0.0.0", "::"} and not allow_public_bind:
        raise ValueError("Binding to all interfaces requires allow_public_bind=True.")


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "").strip()
    if candidate and len(candidate) <= 128 and all(ch.isprintable() for ch in candidate):
        return candidate
    return f"req-{uuid.uuid4().hex}"


def _request_too_large(request: Request, *, config: SecurityConfig) -> JSONResponse | None:
    content_length = request.headers.get("content-length")
    if not content_length:
        return None
    try:
        size = int(content_length)
    except ValueError:
        return structured_error_response(
            request,
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_content_length",
            message="Invalid Content-Length header.",
        )
    content_type = request.headers.get("content-type", "")
    limit = (
        config.max_upload_bytes
        if content_type.startswith("multipart/")
        else config.max_request_bytes
    )
    if size <= limit:
        return None
    return structured_error_response(
        request,
        status_code=413,
        code="request_too_large",
        message="Request body is too large.",
    )


def _rate_limited_response(
    request: Request,
    *,
    limiter: InMemoryRateLimiter,
    config: SecurityConfig,
) -> JSONResponse | None:
    path = request.url.path
    limit: int | None = None
    bucket = ""
    if path.startswith(("/auth", "/api/v1/auth", "/api/v2/auth")) or path in {
        "/login",
        "/logout",
    }:
        limit = config.auth_rate_limit
        bucket = "auth"
    elif "/codex" in path or path.startswith("/codex"):
        limit = config.codex_rate_limit
        bucket = "codex"
    if limit is None:
        return None
    client = request.client.host if request.client else "unknown"
    key = f"{bucket}:{client}:{path}"
    if limiter.allow(key, limit=limit):
        return None
    return structured_error_response(
        request,
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        code="rate_limited",
        message="Too many requests.",
    )


def _audit_sensitive_action(request: Request, response: Response) -> None:
    if not _is_sensitive_path(request.url.path):
        return
    database = getattr(request.app.state, "platform_database", None)
    if database is None:
        return
    actor = getattr(request.state, "user", None)
    actor_user_id = getattr(actor, "user_id", None)
    project_id = request.path_params.get("project_id")
    try:
        database.write_audit(
            "sensitive_http_request",
            actor_user_id=actor_user_id,
            project_id=str(project_id) if project_id else None,
            summary=f"{request.method} {request.url.path} returned {response.status_code}.",
            object_type="http_request",
            object_id=str(getattr(request.state, "request_id", "request")),
            metadata={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
    except Exception:
        return


def _is_sensitive_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SENSITIVE_PATH_PREFIXES)


def _error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "request_too_large",
        422: "validation_error",
        429: "rate_limited",
    }.get(status_code, "http_error")


def normalized_cors_origins(origins: Iterable[str] | None) -> tuple[str, ...]:
    if origins is None:
        return ()
    return tuple(origin for origin in origins if origin)
