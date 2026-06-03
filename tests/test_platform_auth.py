from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.auth import (
    AuthError,
    OIDCConfig,
    PasswordHasher,
    PasswordPolicyConfig,
    SessionTokenManager,
)
from molecule_ranker.platform.database import PlatformDatabase, service_account_tokens
from molecule_ranker.platform.sso import (
    StaticOIDCMetadataProvider,
    validate_id_token,
    validate_oidc_discovery_document,
)
from molecule_ranker.server import create_app


def test_password_hash_verification_and_policy() -> None:
    policy = PasswordPolicyConfig()
    hasher = PasswordHasher(policy=policy)

    with pytest.raises(AuthError):
        hasher.hash_password("weak-password")

    salt, password_hash = hasher.hash_password("Strong-password-1")

    assert password_hash != "Strong-password-1"
    assert hasher.verify("Strong-password-1", salt=salt, expected_hash=password_hash)
    assert not hasher.verify("Wrong-password-1", salt=salt, expected_hash=password_hash)


def test_login_refresh_current_user_and_logout(tmp_path: Path) -> None:
    client = _client(tmp_path)

    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert login.status_code == 200, login.text
    token_payload = login.json()
    headers = {"Authorization": f"Bearer {token_payload['access_token']}"}

    me = client.get("/auth/me", headers=headers)
    refreshed = client.post(
        "/auth/refresh",
        json={"refresh_token": token_payload["refresh_token"]},
    )
    logout = client.post("/auth/logout", json={}, headers=headers)
    after_logout = client.get("/auth/me", headers=headers)

    assert me.status_code == 200
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]
    assert logout.status_code == 200
    assert after_logout.status_code == 401


def test_login_failure_and_inactive_user_blocked(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="user@example.com", password="User-password-1")
    database.disable_user(user.user_id, actor_user_id=user.user_id)
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            platform_db_path=tmp_path / "platform.sqlite",
        )
    )

    wrong_password = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "Wrong-password-1"},
    )
    inactive = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "User-password-1"},
    )

    assert wrong_password.status_code == 401
    assert inactive.status_code == 401


def test_signed_token_expiration() -> None:
    manager = SessionTokenManager(_secret(), ttl_seconds=-1)
    token = manager.issue(user_id="user-1", roles=["user"], ttl_seconds=-1)

    with pytest.raises(AuthError):
        manager.verify(token)


def test_hosted_app_rejects_short_auth_secret_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Hosted auth secret must be at least 32 characters"):
        create_app(root_dir=tmp_path, hosted_mode=True, auth_secret="too-short")


def test_service_account_token_hash_storage_and_revoke(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client)
    admin_user_id = client.get("/auth/me", headers=headers).json()["user"]["user_id"]

    created = client.post(
        "/auth/token/create",
        json={
            "name": "automation",
            "user_id": admin_user_id,
            "scopes": ["project:read", "job:run"],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    service_token = created.json()["access_token"]
    token_id = created.json()["token_id"]

    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(service_account_tokens).where(
                    service_account_tokens.c.token_id == token_id
                )
            )
            .mappings()
            .one()
        )
    assert row["token_hash"] != service_token
    assert row["token_salt"] != service_token

    service_me = client.get("/auth/me", headers={"Authorization": f"Bearer {service_token}"})
    revoked = client.post(
        "/auth/token/revoke",
        json={"token_id": token_id},
        headers=headers,
    )
    after_revoke = client.get("/auth/me", headers={"Authorization": f"Bearer {service_token}"})

    assert service_me.status_code == 200, service_me.text
    assert service_me.json()["user"]["auth_provider"] == "service_account"
    assert revoked.status_code == 200
    assert after_revoke.status_code == 401


def test_service_account_token_scopes_are_enforced(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = _login(client)
    admin_user_id = client.get("/auth/me", headers=headers).json()["user"]["user_id"]

    created = client.post(
        "/auth/token/create",
        json={
            "name": "automation",
            "user_id": admin_user_id,
            "scopes": ["project:read"],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text

    admin_audit = client.get(
        "/admin/audit",
        headers={"Authorization": f"Bearer {created.json()['access_token']}"},
    )

    assert admin_audit.status_code == 403


def test_oidc_routes_disabled_when_not_configured(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/auth/oidc/login")

    assert response.status_code == 404


def test_oidc_token_validation_and_group_role_mapping_are_mockable() -> None:
    secret = "mock-oidc-signing-secret"
    config = OIDCConfig(
        issuer="https://idp.example.com",
        client_id="molecule-ranker",
        discovery_url="https://idp.example.com/.well-known/openid-configuration",
        allowed_email_domains=["example.com"],
        group_role_mapping={"discovery-admins": ["platform_admin"], "scientists": ["user"]},
    )
    provider = StaticOIDCMetadataProvider(
        discovery={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
            "token_endpoint": "https://idp.example.com/oauth2/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        },
        jwks={"keys": [{"kid": "kid-1", "kty": "oct", "alg": "HS256", "k": _b64(secret)}]},
    )
    token = _jwt(
        {"alg": "HS256", "kid": "kid-1", "typ": "JWT"},
        {
            "iss": "https://idp.example.com",
            "sub": "subject-1",
            "aud": "molecule-ranker",
            "email": "ada@example.com",
            "email_verified": True,
            "groups": ["scientists", "discovery-admins"],
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        secret,
    )

    discovery = validate_oidc_discovery_document(config, provider.discovery_document())
    identity = validate_id_token(token, config=config, provider=provider, discovery=discovery)

    assert identity.email == "ada@example.com"
    assert identity.groups == ["scientists", "discovery-admins"]
    assert identity.roles == ["platform_admin", "user"]


def test_oidc_jwks_rotation_refreshes_unknown_key() -> None:
    old_secret = "old-signing-secret"
    new_secret = "new-signing-secret"
    config = OIDCConfig(
        issuer="https://idp.example.com",
        client_id="molecule-ranker",
        discovery_url="https://idp.example.com/.well-known/openid-configuration",
        allowed_email_domains=["example.com"],
    )
    provider = StaticOIDCMetadataProvider(
        discovery={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
            "token_endpoint": "https://idp.example.com/oauth2/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        },
        jwks={"keys": [{"kid": "old", "kty": "oct", "alg": "HS256", "k": _b64(old_secret)}]},
        rotated_jwks={
            "keys": [{"kid": "new", "kty": "oct", "alg": "HS256", "k": _b64(new_secret)}]
        },
    )
    token = _jwt(
        {"alg": "HS256", "kid": "new", "typ": "JWT"},
        {
            "iss": "https://idp.example.com",
            "sub": "subject-2",
            "aud": "molecule-ranker",
            "email": "grace@example.com",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        new_secret,
    )

    identity = validate_id_token(token, config=config, provider=provider)

    assert identity.subject == "subject-2"
    assert provider.jwks_fetch_count == 2


def test_oidc_rejects_disallowed_email_domain() -> None:
    secret = "mock-oidc-signing-secret"
    config = OIDCConfig(
        issuer="https://idp.example.com",
        client_id="molecule-ranker",
        discovery_url="https://idp.example.com/.well-known/openid-configuration",
        allowed_email_domains=["example.com"],
    )
    provider = StaticOIDCMetadataProvider(
        discovery={
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
            "token_endpoint": "https://idp.example.com/oauth2/token",
            "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
        },
        jwks={"keys": [{"kid": "kid-1", "kty": "oct", "alg": "HS256", "k": _b64(secret)}]},
    )
    token = _jwt(
        {"alg": "HS256", "kid": "kid-1", "typ": "JWT"},
        {
            "iss": "https://idp.example.com",
            "sub": "subject-3",
            "aud": "molecule-ranker",
            "email": "eve@other.test",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        secret,
    )

    with pytest.raises(AuthError, match="email domain"):
        validate_id_token(token, config=config, provider=provider)


def test_user_and_service_token_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"
    create_user = runner.invoke(
        app,
        [
            "user",
            "create",
            "--email",
            "admin@example.com",
            "--password",
            "Admin-password-1",
            "--admin",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert create_user.exit_code == 0, create_user.stdout
    user_id = json.loads(create_user.stdout)["user"]["user_id"]
    list_users = runner.invoke(app, ["user", "list", "--db-path", str(db_path), "--json"])
    create_token = runner.invoke(
        app,
        [
            "auth",
            "token",
            "create",
            "--name",
            "automation",
            "--user-id",
            user_id,
            "--created-by-user-id",
            user_id,
            "--scope",
            "project:read",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert list_users.exit_code == 0, list_users.stdout
    assert "Admin-password-1" not in list_users.stdout
    assert create_token.exit_code == 0, create_token.stdout
    token_payload = json.loads(create_token.stdout)
    assert token_payload["access_token"].startswith("mrs_")
    revoke_token = runner.invoke(
        app,
        [
            "auth",
            "token",
            "revoke",
            "--token-id",
            token_payload["token_id"],
            "--actor-user-id",
            user_id,
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert revoke_token.exit_code == 0, revoke_token.stdout


def test_v2_auth_cli_service_account_and_sessions(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"
    created_user = runner.invoke(
        app,
        [
            "user",
            "create",
            "--email",
            "admin@example.com",
            "--password",
            "Admin-password-1",
            "--admin",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert created_user.exit_code == 0, created_user.stdout
    user_id = json.loads(created_user.stdout)["user"]["user_id"]

    created_token = runner.invoke(
        app,
        [
            "auth",
            "service-account",
            "create",
            "--name",
            "automation",
            "--user-id",
            user_id,
            "--created-by-user-id",
            user_id,
            "--scope",
            "project:read",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert created_token.exit_code == 0, created_token.stdout
    token_payload = json.loads(created_token.stdout)
    assert token_payload["access_token"].startswith("mrs_")

    database = PlatformDatabase(tmp_path, db_path=db_path)
    refresh_token = "mrr_test_refresh_token"
    session_id = database.create_auth_session(
        user_id=user_id,
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"token": "plaintext-refresh-token-value"},
    )
    sessions = runner.invoke(
        app,
        ["auth", "sessions", "list", "--db-path", str(db_path), "--json"],
    )
    revoked_session = runner.invoke(
        app,
        [
            "auth",
            "sessions",
            "revoke",
            "--session-id",
            session_id,
            "--actor-user-id",
            user_id,
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    revoked_token = runner.invoke(
        app,
        [
            "auth",
            "service-account",
            "revoke",
            "--token-id",
            token_payload["token_id"],
            "--actor-user-id",
            user_id,
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert sessions.exit_code == 0, sessions.stdout
    assert session_id in sessions.stdout
    assert "plaintext-refresh-token-value" not in sessions.stdout
    assert revoked_session.exit_code == 0, revoked_session.stdout
    assert revoked_token.exit_code == 0, revoked_token.stdout


def test_oidc_cli_test_redacts_secrets(tmp_path: Path) -> None:
    discovery_path = tmp_path / "discovery.json"
    jwks_path = tmp_path / "jwks.json"
    discovery_path.write_text(
        json.dumps(
            {
                "issuer": "https://idp.example.com",
                "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
                "token_endpoint": "https://idp.example.com/oauth2/token",
                "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
            }
        )
    )
    jwks_path.write_text(
        json.dumps(
            {"keys": [{"kid": "kid-1", "kty": "oct", "alg": "HS256", "k": _b64("secret")}]}
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "auth",
            "oidc",
            "test",
            "--issuer",
            "https://idp.example.com",
            "--client-id",
            "molecule-ranker",
            "--discovery-path",
            str(discovery_path),
            "--jwks-path",
            str(jwks_path),
            "--client-secret",
            "oidc-client-secret-value",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "oidc-client-secret-value" not in result.stdout
    assert "secret" not in result.stdout
    assert json.loads(result.stdout)["ok"] is True


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret=_secret(),
            platform_db_path=tmp_path / "platform.sqlite",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _jwt(header: dict[str, object], payload: dict[str, object], secret: str) -> str:
    header_segment = _b64(json.dumps(header, separators=(",", ":")))
    payload_segment = _b64(json.dumps(payload, separators=(",", ":")))
    signing_input = f"{header_segment}.{payload_segment}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    signature_segment = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{header_segment}.{payload_segment}.{signature_segment}"
