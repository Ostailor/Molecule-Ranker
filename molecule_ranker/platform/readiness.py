from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient

from molecule_ranker.platform.database import SCHEMA_VERSION, PlatformDatabase
from molecule_ranker.platform.jobs import JobResult, PlatformJobQueue
from molecule_ranker.platform.settings import LOCAL_DEVELOPMENT_SECRET
from molecule_ranker.server import create_app

ReadinessStatus = Literal["pass", "warn", "fail"]
EnvironmentName = Literal["development", "test", "staging", "production"]

AUTH_MODES = {"local_password", "oidc", "oauth", "service_account"}
RETENTION_KEYS = (
    "artifact_retention_days",
    "codex_transcript_retention_days",
    "audit_log_retention_days",
    "cache_retention_days",
    "assay_result_retention_days",
)
DEFAULT_RETENTION_DAYS = 365


@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    category: str
    status: ReadinessStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "category": self.category,
            "status": self.status,
            "message": self.message,
            "details": _json_ready(self.details),
        }


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadinessStatus
    checks: list[ReadinessCheck]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, ReadinessCheck]:
        return {check.check_id: check for check in self.checks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metadata": _json_ready(self.metadata),
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Platform Readiness",
            "",
            f"Status: **{self.status.upper()}**",
            "",
            "| Check | Status | Message |",
            "| --- | --- | --- |",
        ]
        for check in self.checks:
            lines.append(f"| `{check.check_id}` | {check.status} | {check.message} |")
        lines.append("")
        return "\n".join(lines)


@dataclass(frozen=True)
class ReadinessConfig:
    root_dir: Path = Path(".")
    environment: EnvironmentName = "development"
    database_url: str | None = None
    database_path: Path | None = None
    artifact_storage_root: Path = Path(".molecule-ranker/artifacts")
    backup_path: Path = Path(".molecule-ranker/backups")
    secret_key: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    debug: bool = False
    auth_mode: str = "local_password"
    worker_enabled: bool = True
    enable_codex_worker: bool = False
    codex_cli_command: str = "codex"
    external_integrations_enabled: bool = False
    external_credentials_valid: bool = False
    enable_observability: bool = True
    retention_policy_days: dict[str, int | None] = field(
        default_factory=lambda: {key: DEFAULT_RETENTION_DAYS for key in RETENTION_KEYS}
    )

    @classmethod
    def from_environment(cls, *, root_dir: Path = Path(".")) -> ReadinessConfig:
        env = os.environ
        retention = {
            key: _parse_optional_int(
                env.get(f"MOLECULE_RANKER_{key.upper()}"),
                default=DEFAULT_RETENTION_DAYS,
            )
            for key in RETENTION_KEYS
        }
        return cls(
            root_dir=root_dir,
            environment=_environment(env.get("MOLECULE_RANKER_ENVIRONMENT")),
            database_url=env.get("MOLECULE_RANKER_DATABASE_URL") or env.get("DATABASE_URL"),
            database_path=_optional_path(env.get("MOLECULE_RANKER_PLATFORM_DB_PATH")),
            artifact_storage_root=_optional_path(
                env.get("MOLECULE_RANKER_ARTIFACT_STORAGE_ROOT")
            )
            or cls.artifact_storage_root,
            backup_path=_optional_path(env.get("MOLECULE_RANKER_BACKUP_PATH")) or cls.backup_path,
            secret_key=(
                env.get("MOLECULE_RANKER_SECRET_KEY")
                or env.get("MOLECULE_RANKER_AUTH_SECRET")
                or env.get("SECRET_KEY")
            ),
            allowed_hosts=_parse_list(env.get("MOLECULE_RANKER_ALLOWED_HOSTS")),
            debug=_parse_bool(env.get("MOLECULE_RANKER_DEBUG"), default=False),
            auth_mode=env.get("MOLECULE_RANKER_AUTH_MODE") or "local_password",
            worker_enabled=_parse_bool(env.get("MOLECULE_RANKER_WORKER_ENABLED"), default=True),
            enable_codex_worker=_parse_bool(
                env.get("MOLECULE_RANKER_ENABLE_CODEX_WORKER"),
                default=False,
            ),
            codex_cli_command=env.get("MOLECULE_RANKER_CODEX_CLI_COMMAND") or "codex",
            external_integrations_enabled=_parse_bool(
                env.get("MOLECULE_RANKER_EXTERNAL_INTEGRATIONS_ENABLED"),
                default=False,
            ),
            external_credentials_valid=_parse_bool(
                env.get("MOLECULE_RANKER_EXTERNAL_CREDENTIALS_VALID"),
                default=False,
            ),
            enable_observability=_parse_bool(
                env.get("MOLECULE_RANKER_ENABLE_OBSERVABILITY"),
                default=True,
            ),
            retention_policy_days=retention,
        )


def run_readiness_checks(
    config: ReadinessConfig | None = None,
    **overrides: Any,
) -> ReadinessReport:
    active_config = config or ReadinessConfig.from_environment()
    if overrides:
        active_config = replace(active_config, **overrides)
    root_dir = active_config.root_dir.resolve()
    checks: list[ReadinessCheck] = []
    database: PlatformDatabase | None = None

    def add(
        check_id: str,
        category: str,
        status: ReadinessStatus,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        checks.append(
            ReadinessCheck(
                check_id=check_id,
                category=category,
                status=status,
                message=message,
                details=details or {},
            )
        )

    try:
        database = PlatformDatabase(
            root_dir,
            database_url=active_config.database_url,
            db_path=active_config.database_path,
        )
        health = database.health()
        add("database_connection", "database", "pass", "Database connection succeeded.", health)
    except Exception as exc:
        add(
            "database_connection",
            "database",
            "fail",
            "Database connection failed.",
            {"error": str(exc)},
        )

    if database is None:
        add(
            "migrations_current",
            "database",
            "fail",
            "Migration check could not run without a database connection.",
        )
    else:
        try:
            applied = database.applied_migrations()
            if SCHEMA_VERSION in applied:
                add(
                    "migrations_current",
                    "database",
                    "pass",
                    "Database migrations are current.",
                    {"schema_version": SCHEMA_VERSION, "applied_migrations": applied},
                )
            else:
                add(
                    "migrations_current",
                    "database",
                    "fail",
                    "Current schema migration is not applied.",
                    {"schema_version": SCHEMA_VERSION, "applied_migrations": applied},
                )
        except Exception as exc:
            add(
                "migrations_current",
                "database",
                "fail",
                "Migration check failed.",
                {"error": str(exc)},
            )

    add(
        "artifact_storage_writable",
        "storage",
        *_check_writable_directory(
            _resolve_path(root_dir, active_config.artifact_storage_root),
            "Artifact storage is writable.",
            "Artifact storage is not writable.",
        ),
    )

    _add_production_checks(add, active_config)

    if active_config.auth_mode in AUTH_MODES:
        add(
            "auth_mode_configured",
            "auth",
            "pass",
            f"Authentication mode is configured as {active_config.auth_mode}.",
        )
    else:
        add(
            "auth_mode_configured",
            "auth",
            "fail",
            "Authentication mode is missing or unsupported.",
            {"auth_mode": active_config.auth_mode, "supported": sorted(AUTH_MODES)},
        )

    if active_config.worker_enabled:
        if database is None:
            add(
                "worker_queue_reachable",
                "jobs",
                "fail",
                "Worker queue could not be checked without a database connection.",
            )
        else:
            try:
                PlatformJobQueue(database).list_jobs(limit=1)
                add("worker_queue_reachable", "jobs", "pass", "Worker queue is reachable.")
            except Exception as exc:
                add(
                    "worker_queue_reachable",
                    "jobs",
                    "fail",
                    "Worker queue is not reachable.",
                    {"error": str(exc)},
                )
    else:
        add(
            "worker_queue_reachable",
            "jobs",
            "warn",
            "Background worker is disabled; queued jobs will not run until a worker is enabled.",
        )

    _add_codex_worker_check(add, active_config)
    _add_external_integrations_check(add, active_config)
    _add_http_checks(add, active_config, root_dir)
    _add_background_worker_check(add, active_config, database)
    _add_audit_logging_check(add, database)
    _add_retention_check(add, active_config)

    add(
        "backup_path_configured",
        "backup",
        *_check_writable_directory(
            _resolve_path(root_dir, active_config.backup_path),
            "Backup path is configured and writable.",
            "Backup path is missing or not writable.",
        ),
    )

    status = _overall_status(checks)
    return ReadinessReport(
        status=status,
        checks=checks,
        metadata={
            "environment": active_config.environment,
            "root_dir": root_dir,
            "database_url_configured": bool(active_config.database_url),
            "database_path": active_config.database_path,
        },
    )


def run_smoke_test(config: ReadinessConfig | None = None, **overrides: Any) -> ReadinessReport:
    return run_readiness_checks(config, **overrides)


def run_platform_doctor(config: ReadinessConfig | None = None, **overrides: Any) -> ReadinessReport:
    return run_readiness_checks(config, **overrides)


def write_readiness_reports(report: ReadinessReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "readiness.json"
    markdown_path = output_dir / "readiness.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(report.to_markdown())
    return {"json": json_path, "markdown": markdown_path}


def _add_production_checks(
    add: Any,
    config: ReadinessConfig,
) -> None:
    if config.environment != "production":
        add(
            "secret_key_configured_in_production",
            "security",
            "pass",
            "Development and test deployments may use the local development secret.",
        )
        add(
            "allowed_hosts_configured_in_production",
            "security",
            "pass",
            "Explicit allowed hosts are only required in production.",
        )
        add(
            "debug_disabled_in_production",
            "security",
            "pass",
            "Debug mode production restriction does not apply outside production.",
        )
        return
    secret = (config.secret_key or "").strip()
    if secret and secret != LOCAL_DEVELOPMENT_SECRET:
        add(
            "secret_key_configured_in_production",
            "security",
            "pass",
            "Production secret key is configured.",
        )
    else:
        add(
            "secret_key_configured_in_production",
            "security",
            "fail",
            "Production requires a non-development secret key.",
        )
    if config.allowed_hosts and all(host.strip() != "*" for host in config.allowed_hosts):
        add(
            "allowed_hosts_configured_in_production",
            "security",
            "pass",
            "Production allowed hosts are explicitly configured.",
            {"allowed_hosts": config.allowed_hosts},
        )
    else:
        add(
            "allowed_hosts_configured_in_production",
            "security",
            "fail",
            "Production requires explicit non-wildcard allowed hosts.",
            {"allowed_hosts": config.allowed_hosts},
        )
    if config.debug:
        add(
            "debug_disabled_in_production",
            "security",
            "fail",
            "Debug must be disabled in production.",
        )
    else:
        add("debug_disabled_in_production", "security", "pass", "Debug is disabled in production.")


def _add_codex_worker_check(add: Any, config: ReadinessConfig) -> None:
    if not config.enable_codex_worker:
        add(
            "codex_worker_configured_or_disabled",
            "codex",
            "pass",
            "Codex worker is disabled.",
        )
        return
    command = config.codex_cli_command.strip()
    if command and (shutil.which(command) or Path(command).exists()):
        add(
            "codex_worker_configured_or_disabled",
            "codex",
            "pass",
            "Codex worker is enabled and its CLI command is available.",
            {"codex_cli_command": command},
        )
    else:
        add(
            "codex_worker_configured_or_disabled",
            "codex",
            "fail",
            "Codex worker is enabled but its CLI command is unavailable.",
            {"codex_cli_command": command},
        )


def _add_external_integrations_check(add: Any, config: ReadinessConfig) -> None:
    if not config.external_integrations_enabled:
        add(
            "external_integrations_disabled_or_credentials_valid",
            "integrations",
            "pass",
            "External integrations are disabled for this readiness run.",
        )
        return
    if config.external_credentials_valid:
        add(
            "external_integrations_disabled_or_credentials_valid",
            "integrations",
            "pass",
            "External integrations are enabled and credentials were marked valid.",
        )
    else:
        add(
            "external_integrations_disabled_or_credentials_valid",
            "integrations",
            "fail",
            "External integrations are enabled but credentials were not validated.",
        )


def _add_http_checks(add: Any, config: ReadinessConfig, root_dir: Path) -> None:
    try:
        client = TestClient(
            create_app(
                root_dir=root_dir,
                hosted_mode=True,
                platform_database_url=config.database_url,
                platform_db_path=config.database_path,
                auth_secret=config.secret_key or LOCAL_DEVELOPMENT_SECRET,
                enable_codex_backbone=config.enable_codex_worker,
            )
        )
        endpoint_status = {
            path: client.get(path).status_code
            for path in ("/health", "/ready", "/api/v1/health", "/api/v1/ready")
        }
        if all(status == 200 for status in endpoint_status.values()):
            add(
                "health_endpoints_pass",
                "http",
                "pass",
                "Health and readiness endpoints passed.",
                {"endpoints": endpoint_status},
            )
        else:
            add(
                "health_endpoints_pass",
                "http",
                "fail",
                "One or more health endpoints failed.",
                {"endpoints": endpoint_status},
            )
        metrics_response = client.get("/metrics")
        if metrics_response.status_code == 200:
            add(
                "metrics_endpoint_available",
                "observability",
                "pass",
                "Metrics endpoint is available.",
            )
        else:
            add(
                "metrics_endpoint_available",
                "observability",
                "fail",
                "Metrics endpoint is unavailable or empty.",
                {"status_code": metrics_response.status_code},
            )
    except Exception as exc:
        add(
            "health_endpoints_pass",
            "http",
            "fail",
            "Health endpoint check failed.",
            {"error": str(exc)},
        )
        add(
            "metrics_endpoint_available",
            "observability",
            "fail",
            "Metrics endpoint check failed.",
            {"error": str(exc)},
        )


def _add_background_worker_check(
    add: Any,
    config: ReadinessConfig,
    database: PlatformDatabase | None,
) -> None:
    if not config.worker_enabled:
        add(
            "background_worker_can_pick_up_test_job",
            "jobs",
            "warn",
            "Background worker is disabled; test job pickup was skipped.",
        )
        return
    if database is None:
        add(
            "background_worker_can_pick_up_test_job",
            "jobs",
            "fail",
            "Test job pickup could not run without a database connection.",
        )
        return
    try:
        user = database.create_user(
            email=f"readiness-{uuid.uuid4().hex[:12]}@example.test",
            password="Readiness-password-1",
            roles=["platform_admin", "user"],
        )
        queue = PlatformJobQueue(database)
        enqueued = queue.enqueue(
            job_type="dashboard_build",
            requested_by=user,
            priority="high",
            metadata={"readiness_probe": True},
        )
        claimed = queue.claim_next(job_types={"dashboard_build"})
        if claimed is None or claimed.job_id != enqueued.job_id:
            add(
                "background_worker_can_pick_up_test_job",
                "jobs",
                "fail",
                "Background worker queue did not claim the readiness test job.",
                {
                    "enqueued_job_id": enqueued.job_id,
                    "claimed_job_id": claimed.job_id if claimed else None,
                },
            )
            return
        queue.succeed(claimed, JobResult(result={"readiness_probe": True}))
        add(
            "background_worker_can_pick_up_test_job",
            "jobs",
            "pass",
            "Background worker queue claimed and completed a readiness test job.",
            {"job_id": claimed.job_id},
        )
    except Exception as exc:
        add(
            "background_worker_can_pick_up_test_job",
            "jobs",
            "fail",
            "Background worker test job failed.",
            {"error": str(exc)},
        )


def _add_audit_logging_check(add: Any, database: PlatformDatabase | None) -> None:
    if database is None:
        add(
            "audit_logging_enabled",
            "audit",
            "fail",
            "Audit logging could not be checked without a database connection.",
        )
        return
    try:
        event = database.write_audit(
            "readiness_audit_probe",
            summary="Readiness audit logging probe.",
            object_type="readiness",
            object_id="platform",
            metadata={"readiness_probe": True},
        )
        events = database.list_audit_events(limit=10)
        if any(item.event_id == event.event_id for item in events):
            add("audit_logging_enabled", "audit", "pass", "Audit logging is enabled.")
        else:
            add(
                "audit_logging_enabled",
                "audit",
                "fail",
                "Audit probe event was not readable after write.",
            )
    except Exception as exc:
        add(
            "audit_logging_enabled",
            "audit",
            "fail",
            "Audit logging check failed.",
            {"error": str(exc)},
        )


def _add_retention_check(add: Any, config: ReadinessConfig) -> None:
    missing = [key for key in RETENTION_KEYS if config.retention_policy_days.get(key) is None]
    if missing:
        status: ReadinessStatus = "fail" if config.environment == "production" else "warn"
        add(
            "retention_policy_configured",
            "retention",
            status,
            "One or more retention policy values are not configured.",
            {"missing": missing, "retention_policy_days": config.retention_policy_days},
        )
        return
    add(
        "retention_policy_configured",
        "retention",
        "pass",
        "Retention policy values are configured.",
        {"retention_policy_days": config.retention_policy_days},
    )


def _check_writable_directory(
    path: Path,
    success_message: str,
    failure_message: str,
) -> tuple[ReadinessStatus, str, dict[str, Any]]:
    try:
        if path.exists() and not path.is_dir():
            return (
                "fail",
                failure_message,
                {"path": path, "error": "Path exists and is not a directory."},
            )
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".readiness-{uuid.uuid4().hex}.tmp"
        probe.write_text("ok\n")
        probe.unlink()
        return "pass", success_message, {"path": path}
    except Exception as exc:
        return "fail", failure_message, {"path": path, "error": str(exc)}


def _overall_status(checks: list[ReadinessCheck]) -> ReadinessStatus:
    if any(check.status == "fail" for check in checks):
        return "fail"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "pass"


def _resolve_path(root_dir: Path, path: Path) -> Path:
    return path if path.is_absolute() else root_dir / path


def _optional_path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _environment(value: str | None) -> EnvironmentName:
    normalized = (value or "development").strip().lower()
    if normalized in {"development", "test", "staging", "production"}:
        return normalized  # type: ignore[return-value]
    return "development"


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: str | None, *, default: int | None) -> int | None:
    if value is None or not value.strip():
        return default
    if value.strip().lower() in {"none", "null", "off"}:
        return None
    return int(value)


def _parse_list(value: str | None) -> list[str]:
    if value is None:
        return []
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


__all__ = [
    "ReadinessCheck",
    "ReadinessConfig",
    "ReadinessReport",
    "ReadinessStatus",
    "run_platform_doctor",
    "run_readiness_checks",
    "run_smoke_test",
    "write_readiness_reports",
]
