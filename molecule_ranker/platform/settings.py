from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
AuthMode = Literal["local_password", "oidc", "oauth", "service_account"]

LOCAL_DEVELOPMENT_SECRET = "local-development-hosted-secret-change-me-32"
SENSITIVE_SETTING_NAMES = {"secret_key"}


class PlatformSettings(BaseSettings):
    """Production settings for hosted molecule-ranker deployments.

    Settings are sourced from process environment variables only. The model intentionally does
    not read `.env` files so local secret files are never pulled into Codex prompts or logs by
    settings rendering.
    """

    model_config = SettingsConfigDict(
        env_prefix="MOLECULE_RANKER_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        enable_decoding=False,
    )

    environment: Environment = "development"
    debug: bool = False
    secret_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MOLECULE_RANKER_SECRET_KEY",
            "MOLECULE_RANKER_AUTH_SECRET",
            "SECRET_KEY",
        ),
    )
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MOLECULE_RANKER_DATABASE_URL", "DATABASE_URL"),
    )
    database_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("MOLECULE_RANKER_PLATFORM_DB_PATH"),
    )
    artifact_storage_root: Path = Path(".molecule-ranker/artifacts")
    allowed_hosts: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=list)
    auth_mode: AuthMode = "local_password"
    token_expiration_minutes: int = Field(default=15, gt=0)
    refresh_token_expiration_days: int = Field(default=30, gt=0)
    password_min_length: int = Field(default=12, ge=8)
    enable_oidc: bool = False
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret_env: str | None = None
    oidc_redirect_url: str | None = None
    enable_codex_worker: bool = False
    codex_cli_command: str = "codex"
    codex_worker_concurrency: int = Field(default=1, ge=1)
    codex_worker_workspace_root: Path | None = None
    codex_job_timeout_seconds: int = Field(default=300, gt=0)
    codex_artifact_context_max_bytes: int = Field(default=1_000_000, ge=0)
    codex_worker_allow_engineering_tasks: bool = False
    codex_worker_allow_runtime_tasks: bool = False
    enable_observability: bool = True
    log_level: str = "INFO"
    artifact_retention_days: int | None = Field(default=None, ge=1)
    codex_transcript_retention_days: int | None = Field(default=None, ge=1)
    audit_log_retention_days: int | None = Field(default=None, ge=1)
    cache_retention_days: int | None = Field(default=None, ge=1)
    assay_result_retention_days: int | None = Field(default=None, ge=1)
    max_upload_size_mb: int = Field(default=10, gt=0)
    max_artifact_download_size_mb: int = Field(default=100, gt=0)
    hosted_mode: bool = False

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def parse_list_setting(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                import json

                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @model_validator(mode="after")
    def validate_production_requirements(self) -> PlatformSettings:
        if self.environment != "production":
            return self
        if self.secret_key is None or not self.secret_key.get_secret_value().strip():
            raise ValueError("production mode requires MOLECULE_RANKER_SECRET_KEY")
        if self.debug:
            raise ValueError("production mode rejects debug=True")
        if not self.allowed_hosts:
            raise ValueError("production mode requires explicit allowed_hosts")
        if any(host.strip() == "*" for host in self.allowed_hosts):
            raise ValueError("production mode rejects wildcard allowed_hosts")
        return self

    @classmethod
    def from_environment(cls) -> PlatformSettings:
        return cls()

    @property
    def auth_secret(self) -> str:
        if self.secret_key is None:
            return LOCAL_DEVELOPMENT_SECRET
        return self.secret_key.get_secret_value()

    @property
    def codex_worker_enabled(self) -> bool:
        return self.enable_codex_worker

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_artifact_download_bytes(self) -> int:
        return self.max_artifact_download_size_mb * 1024 * 1024

    def retention_settings(self) -> dict[str, int | None]:
        return {
            "artifact_retention_days": self.artifact_retention_days,
            "codex_transcript_retention_days": self.codex_transcript_retention_days,
            "audit_log_retention_days": self.audit_log_retention_days,
            "cache_retention_days": self.cache_retention_days,
            "assay_result_retention_days": self.assay_result_retention_days,
        }

    def redacted_model_dump(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["secret_key"] = "[REDACTED]" if self.secret_key is not None else None
        if self.oidc_client_secret_env:
            payload["oidc_client_secret_configured"] = True
        payload.pop("oidc_client_secret", None)
        return payload


def validate_settings(settings: PlatformSettings | None = None) -> dict[str, Any]:
    active_settings = settings or PlatformSettings.from_environment()
    return {
        "ok": True,
        "environment": active_settings.environment,
        "debug": active_settings.debug,
        "auth_mode": active_settings.auth_mode,
        "database_configured": bool(active_settings.database_url or active_settings.database_path),
        "allowed_hosts": list(active_settings.allowed_hosts),
        "enable_codex_worker": active_settings.enable_codex_worker,
        "enable_observability": active_settings.enable_observability,
    }


__all__ = [
    "AuthMode",
    "Environment",
    "LOCAL_DEVELOPMENT_SECRET",
    "PlatformSettings",
    "validate_settings",
]
