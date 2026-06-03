from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field


class AuthError(ValueError):
    """Raised when hosted authentication fails."""


class PasswordPolicyConfig(BaseModel):
    min_length: int = Field(default=12, ge=8)
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digit: bool = True
    require_symbol: bool = False

    def validate_password(self, password: str) -> None:
        if len(password) < self.min_length:
            raise AuthError(f"Password must be at least {self.min_length} characters.")
        if self.require_uppercase and not any(char.isupper() for char in password):
            raise AuthError("Password must contain an uppercase letter.")
        if self.require_lowercase and not any(char.islower() for char in password):
            raise AuthError("Password must contain a lowercase letter.")
        if self.require_digit and not any(char.isdigit() for char in password):
            raise AuthError("Password must contain a digit.")
        if self.require_symbol and not any(not char.isalnum() for char in password):
            raise AuthError("Password must contain a symbol.")


class OIDCConfig(BaseModel):
    issuer: str | None = None
    client_id: str | None = None
    client_secret_env_var: str | None = None
    redirect_url: str | None = None
    discovery_url: str | None = None
    allowed_email_domains: list[str] = Field(default_factory=list)
    group_role_mapping: dict[str, list[str]] = Field(default_factory=dict)
    session_ttl_seconds: int = Field(default=8 * 60 * 60, gt=0)
    require_https: bool = True
    allow_insecure_http_for_dev: bool = False
    jwks_cache_ttl_seconds: int = Field(default=5 * 60, gt=0)

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id and (self.discovery_url or self.redirect_url))


class AuthTokenConfig(BaseModel):
    access_token_ttl_seconds: int = Field(default=15 * 60, gt=0)
    refresh_token_ttl_seconds: int = Field(default=14 * 24 * 60 * 60, gt=0)
    service_token_default_ttl_seconds: int | None = Field(default=None, gt=0)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class ServiceAccountToken(BaseModel):
    token_id: str
    token: str
    token_type: str = "bearer"
    scopes: list[str] = Field(default_factory=list)


class PasswordHasher:
    def __init__(
        self,
        *,
        iterations: int = 240_000,
        policy: PasswordPolicyConfig | None = None,
    ) -> None:
        self.iterations = iterations
        self.policy = policy or PasswordPolicyConfig(
            min_length=8,
            require_uppercase=False,
            require_lowercase=False,
            require_digit=False,
            require_symbol=False,
        )

    def hash_password(self, password: str, *, salt: str | None = None) -> tuple[str, str]:
        if salt is None:
            self.policy.validate_password(password)
        active_salt = salt or secrets.token_urlsafe(24)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            active_salt.encode("utf-8"),
            self.iterations,
        )
        return active_salt, base64.urlsafe_b64encode(digest).decode("ascii")

    def verify(self, password: str, *, salt: str, expected_hash: str) -> bool:
        _, observed = self.hash_password(password, salt=salt)
        return hmac.compare_digest(observed, expected_hash)


class SessionTokenManager:
    def __init__(self, secret: str, *, ttl_seconds: int = 8 * 60 * 60) -> None:
        if len(secret) < 32:
            raise AuthError("Hosted auth secret must be at least 32 characters.")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(
        self,
        *,
        user_id: str,
        roles: Sequence[str],
        token_type: str = "access",
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        scopes: Sequence[str] | None = None,
    ) -> str:
        now = datetime.now(UTC)
        ttl = ttl_seconds or self.ttl_seconds
        payload = {
            "user_id": user_id,
            "roles": sorted(set(roles)),
            "type": token_type,
            "jti": secrets.token_urlsafe(18),
            "sid": session_id,
            "scopes": sorted(set(scopes or [])),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        }
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_token = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        signature = self._sign(payload_token)
        return f"{payload_token}.{signature}"

    def verify(self, token: str) -> dict[str, Any]:
        try:
            payload_token, signature = token.split(".", 1)
        except ValueError as exc:
            raise AuthError("Invalid bearer token.") from exc
        if not hmac.compare_digest(self._sign(payload_token), signature):
            raise AuthError("Invalid bearer token.")
        padded = payload_token + "=" * (-len(payload_token) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (json.JSONDecodeError, ValueError) as exc:
            raise AuthError("Invalid bearer token.") from exc
        exp = int(payload.get("exp", 0))
        if exp < int(datetime.now(UTC).timestamp()):
            raise AuthError("Bearer token expired.")
        return payload

    def _sign(self, payload_token: str) -> str:
        digest = hmac.new(self.secret, payload_token.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_opaque_token(*, prefix: str = "mr") -> str:
    return f"{prefix}_{secrets.token_urlsafe(36)}"


def hash_token(token: str, *, salt: str | None = None) -> tuple[str, str]:
    active_salt = salt or secrets.token_urlsafe(24)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        token.encode("utf-8"),
        active_salt.encode("utf-8"),
        240_000,
    )
    return active_salt, base64.urlsafe_b64encode(digest).decode("ascii")


def verify_token_hash(token: str, *, salt: str, expected_hash: str) -> bool:
    _, observed = hash_token(token, salt=salt)
    return hmac.compare_digest(observed, expected_hash)
