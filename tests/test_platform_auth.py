from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.auth import (
    AuthError,
    PasswordHasher,
    PasswordPolicyConfig,
    SessionTokenManager,
)
from molecule_ranker.platform.database import PlatformDatabase, service_account_tokens
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


def test_oidc_routes_disabled_when_not_configured(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/auth/oidc/login")

    assert response.status_code == 404


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
