from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.auth import AuthError, OIDCConfig


class OIDCDiscoveryDocument(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None
    revocation_endpoint: str | None = None


class OIDCIdentity(BaseModel):
    subject: str
    email: str | None = None
    email_verified: bool | None = None
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    issuer: str
    claims: dict[str, Any] = Field(default_factory=dict)


class OIDCMetadataProvider(Protocol):
    def discovery_document(self) -> Mapping[str, Any]:
        ...

    def jwks(self, *, refresh: bool = False) -> Mapping[str, Any]:
        ...


class HTTPOIDCMetadataProvider:
    def __init__(self, discovery_url: str) -> None:
        self.discovery_url = discovery_url
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    def discovery_document(self) -> Mapping[str, Any]:
        if self._discovery is None:
            self._discovery = _fetch_json(self.discovery_url)
        return dict(self._discovery)

    def jwks(self, *, refresh: bool = False) -> Mapping[str, Any]:
        if refresh:
            self._jwks = None
        if self._jwks is None:
            document = OIDCDiscoveryDocument.model_validate(self.discovery_document())
            self._jwks = _fetch_json(document.jwks_uri)
        return dict(self._jwks)


class StaticOIDCMetadataProvider:
    def __init__(
        self,
        *,
        discovery: Mapping[str, Any],
        jwks: Mapping[str, Any],
        rotated_jwks: Mapping[str, Any] | None = None,
    ) -> None:
        self.discovery = dict(discovery)
        self._jwks = dict(jwks)
        self.rotated_jwks = dict(rotated_jwks) if rotated_jwks is not None else None
        self.jwks_fetch_count = 0

    def discovery_document(self) -> Mapping[str, Any]:
        return dict(self.discovery)

    def jwks(self, *, refresh: bool = False) -> Mapping[str, Any]:
        self.jwks_fetch_count += 1
        if refresh and self.rotated_jwks is not None:
            self._jwks = dict(self.rotated_jwks)
        return dict(self._jwks)


class SAMLInterface(BaseModel):
    supported: bool = False
    note: str = "SAML is a V2.0 interface placeholder; authentication is not implemented."


class SCIMInterface(BaseModel):
    supported: bool = False
    note: str = "SCIM is a V2.0 interface placeholder; provisioning is not implemented."


def validate_oidc_discovery_document(
    config: OIDCConfig,
    discovery: Mapping[str, Any],
) -> OIDCDiscoveryDocument:
    document = OIDCDiscoveryDocument.model_validate(dict(discovery))
    if config.issuer and document.issuer.rstrip("/") != config.issuer.rstrip("/"):
        raise AuthError("OIDC discovery issuer does not match configured issuer.")
    for field_name in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        _require_https(
            str(getattr(document, field_name)),
            config=config,
            field_name=f"OIDC discovery {field_name}",
        )
    return document


def validate_id_token(
    id_token: str,
    *,
    config: OIDCConfig,
    provider: OIDCMetadataProvider,
    discovery: OIDCDiscoveryDocument | None = None,
    now: datetime | None = None,
    leeway_seconds: int = 60,
) -> OIDCIdentity:
    active_discovery = discovery or validate_oidc_discovery_document(
        config,
        provider.discovery_document(),
    )
    header, claims, signing_input, signature = _decode_jwt(id_token)
    key = _select_jwk(provider.jwks(), header)
    if key is None:
        key = _select_jwk(provider.jwks(refresh=True), header)
    if key is None:
        raise AuthError("OIDC signing key is not available.")
    _verify_signature(header, key, signing_input, signature)
    _validate_claims(
        claims,
        config=config,
        discovery=active_discovery,
        now=now,
        leeway=leeway_seconds,
    )
    groups = _claim_list(claims.get("groups") or claims.get("roles") or [])
    roles = _map_groups_to_roles(groups, config.group_role_mapping)
    return OIDCIdentity(
        subject=str(claims["sub"]),
        email=str(claims["email"]) if claims.get("email") else None,
        email_verified=bool(claims["email_verified"]) if "email_verified" in claims else None,
        groups=groups,
        roles=roles or ["user"],
        issuer=str(claims["iss"]),
        claims=_public_claims(claims),
    )


def redacted_oidc_diagnostic(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.lower()
        if any(marker in lowered for marker in ("secret", "token", "jwks", "key", "assertion")):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            redacted[key] = redacted_oidc_diagnostic(value)
        elif isinstance(value, list):
            redacted[key] = [
                redacted_oidc_diagnostic(item)
                if isinstance(item, Mapping)
                else redact_secrets(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            redacted[key] = redact_secrets(value)
        else:
            redacted[key] = value
    return redacted


def _decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("OIDC ID token must be a signed JWT.")
    header_segment, payload_segment, signature_segment = parts
    try:
        header = json.loads(_b64decode(header_segment))
        payload = json.loads(_b64decode(payload_segment))
        signature = _b64decode(signature_segment)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("OIDC ID token is malformed.") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AuthError("OIDC ID token is malformed.")
    return header, payload, f"{header_segment}.{payload_segment}".encode("ascii"), signature


def _select_jwk(jwks: Mapping[str, Any], header: Mapping[str, Any]) -> Mapping[str, Any] | None:
    kid = header.get("kid")
    keys = jwks.get("keys", [])
    if not isinstance(keys, list):
        raise AuthError("OIDC JWKS document is malformed.")
    for key in keys:
        if isinstance(key, Mapping) and (kid is None or key.get("kid") == kid):
            return key
    return None


def _verify_signature(
    header: Mapping[str, Any],
    key: Mapping[str, Any],
    signing_input: bytes,
    signature: bytes,
) -> None:
    algorithm = str(header.get("alg", ""))
    if algorithm == "HS256" and key.get("kty") == "oct":
        secret = _b64decode(str(key.get("k", "")))
        expected = hmac.new(secret, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthError("OIDC ID token signature is invalid.")
        return
    if algorithm == "RS256" and key.get("kty") == "RSA":
        _verify_rs256_signature(key, signing_input, signature)
        return
    raise AuthError(f"OIDC signing algorithm is not supported: {algorithm}.")


def _verify_rs256_signature(key: Mapping[str, Any], signing_input: bytes, signature: bytes) -> None:
    try:
        n = int.from_bytes(_b64decode(str(key["n"])), "big")
        e = int.from_bytes(_b64decode(str(key["e"])), "big")
        key_size = (n.bit_length() + 7) // 8
        encoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(key_size, "big")
    except Exception as exc:
        raise AuthError("OIDC ID token signature is invalid.") from exc
    digest_info_prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    expected_digest_info = digest_info_prefix + hashlib.sha256(signing_input).digest()
    if not encoded.startswith(b"\x00\x01"):
        raise AuthError("OIDC ID token signature is invalid.")
    separator_index = encoded.find(b"\x00", 2)
    if separator_index < 10:
        raise AuthError("OIDC ID token signature is invalid.")
    padding = encoded[2:separator_index]
    if any(byte != 0xFF for byte in padding):
        raise AuthError("OIDC ID token signature is invalid.")
    observed_digest_info = encoded[separator_index + 1 :]
    if not hmac.compare_digest(observed_digest_info, expected_digest_info):
        raise AuthError("OIDC ID token signature is invalid.")


def _validate_claims(
    claims: Mapping[str, Any],
    *,
    config: OIDCConfig,
    discovery: OIDCDiscoveryDocument,
    now: datetime | None,
    leeway: int,
) -> None:
    active_now = now or datetime.now(UTC)
    timestamp = int(active_now.timestamp())
    issuer = str(claims.get("iss", ""))
    if issuer.rstrip("/") != discovery.issuer.rstrip("/"):
        raise AuthError("OIDC issuer is invalid.")
    if not claims.get("sub"):
        raise AuthError("OIDC subject is missing.")
    audience = claims.get("aud")
    audiences = [str(item) for item in audience] if isinstance(audience, list) else [str(audience)]
    if not config.client_id or config.client_id not in audiences:
        raise AuthError("OIDC audience is invalid.")
    exp = int(claims.get("exp", 0))
    if exp + leeway < timestamp:
        raise AuthError("OIDC ID token expired.")
    nbf = claims.get("nbf")
    if nbf is not None and int(nbf) - leeway > timestamp:
        raise AuthError("OIDC ID token is not active yet.")
    email = str(claims.get("email", ""))
    _validate_email_domain(email, config.allowed_email_domains)


def _validate_email_domain(email: str, allowed_domains: Sequence[str]) -> None:
    if not allowed_domains or not email:
        return
    domain = email.rsplit("@", 1)[-1].lower()
    allowed = {item.lower().lstrip("@") for item in allowed_domains}
    if domain not in allowed:
        raise AuthError("OIDC email domain is not allowed.")


def _map_groups_to_roles(groups: Sequence[str], mapping: Mapping[str, Sequence[str]]) -> list[str]:
    roles: set[str] = set()
    for group in groups:
        roles.update(str(role) for role in mapping.get(group, []))
    return sorted(roles)


def _claim_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return []


def _public_claims(claims: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in claims.items()
        if key not in {"at_hash", "c_hash", "nonce", "auth_time"}
    }


def _require_https(value: str, *, config: OIDCConfig, field_name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme == "https":
        return
    if config.allow_insecure_http_for_dev and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    if config.require_https:
        raise AuthError(f"{field_name} must use HTTPS in production.")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise AuthError("OIDC provider returned a non-object JSON document.")
    return payload


__all__ = [
    "HTTPOIDCMetadataProvider",
    "OIDCDiscoveryDocument",
    "OIDCIdentity",
    "OIDCMetadataProvider",
    "SCIMInterface",
    "SAMLInterface",
    "StaticOIDCMetadataProvider",
    "redacted_oidc_diagnostic",
    "validate_id_token",
    "validate_oidc_discovery_document",
]
