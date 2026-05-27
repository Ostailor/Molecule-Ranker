from __future__ import annotations

import json

import pytest
from pydantic import SecretStr, ValidationError
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.settings import LOCAL_DEVELOPMENT_SECRET, PlatformSettings


def test_platform_settings_dev_defaults_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = PlatformSettings.from_environment()

    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.auth_secret == LOCAL_DEVELOPMENT_SECRET
    assert settings.auth_mode == "local_password"
    assert settings.allowed_hosts == []
    assert settings.token_expiration_minutes == 15
    assert settings.refresh_token_expiration_days == 30
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.model_config.get("env_file") is None


def test_platform_settings_production_missing_secret_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(ValidationError, match="requires MOLECULE_RANKER_SECRET_KEY"):
        PlatformSettings(environment="production", allowed_hosts=["ranker.internal"])


def test_platform_settings_production_rejects_debug_and_requires_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(ValidationError, match="rejects debug"):
        PlatformSettings(
            environment="production",
            secret_key=SecretStr("production-secret-value-with-length"),
            debug=True,
            allowed_hosts=["ranker.internal"],
        )

    with pytest.raises(ValidationError, match="requires explicit allowed_hosts"):
        PlatformSettings(
            environment="production",
            secret_key=SecretStr("production-secret-value-with-length"),
        )

    with pytest.raises(ValidationError, match="rejects wildcard"):
        PlatformSettings(
            environment="production",
            secret_key=SecretStr("production-secret-value-with-length"),
            allowed_hosts=["*"],
        )

    settings = PlatformSettings(
        environment="production",
        secret_key=SecretStr("production-secret-value-with-length"),
        allowed_hosts=["ranker.internal"],
    )
    assert settings.allowed_hosts == ["ranker.internal"]


def test_platform_settings_secrets_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)
    settings = PlatformSettings(secret_key=SecretStr("super-secret-value"))

    payload = settings.redacted_model_dump()

    assert payload["secret_key"] == "[REDACTED]"
    assert "super-secret-value" not in json.dumps(payload)


def test_config_cli_show_and_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("MOLECULE_RANKER_SECRET_KEY", "cli-secret-value")
    monkeypatch.setenv("MOLECULE_RANKER_ALLOWED_HOSTS", "ranker.internal,localhost")
    runner = CliRunner()

    shown = runner.invoke(app, ["config", "show", "--redacted"])
    validated = runner.invoke(app, ["config", "validate"])

    assert shown.exit_code == 0, shown.output
    shown_payload = json.loads(shown.output)
    assert shown_payload["secret_key"] == "[REDACTED]"
    assert "cli-secret-value" not in shown.output
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["ok"] is True


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "MOLECULE_RANKER_ENVIRONMENT",
        "MOLECULE_RANKER_DEBUG",
        "MOLECULE_RANKER_SECRET_KEY",
        "MOLECULE_RANKER_AUTH_SECRET",
        "SECRET_KEY",
        "MOLECULE_RANKER_DATABASE_URL",
        "DATABASE_URL",
        "MOLECULE_RANKER_ALLOWED_HOSTS",
        "MOLECULE_RANKER_CORS_ORIGINS",
        "MOLECULE_RANKER_AUTH_MODE",
    ]:
        monkeypatch.delenv(name, raising=False)
