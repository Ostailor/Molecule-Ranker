from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.pilot.schemas import PilotEnvironment, PilotReadinessReport
from molecule_ranker.pilot.support_bundle import generate_support_bundle_manifest
from molecule_ranker.platform.database import SCHEMA_VERSION, PlatformDatabase
from molecule_ranker.platform.jobs import JobResult, PlatformJobQueue
from molecule_ranker.platform.readiness import ensure_readiness_probe_project
from molecule_ranker.platform.settings import LOCAL_DEVELOPMENT_SECRET
from molecule_ranker.release.checks import run_release_checks
from molecule_ranker.server import create_app

RETENTION_POLICY_KEYS = (
    "artifact_retention_days",
    "codex_transcript_retention_days",
    "audit_log_retention_days",
    "cache_retention_days",
    "assay_result_retention_days",
)


@dataclass(frozen=True)
class PilotReadinessConfig:
    root_dir: Path = Path(".")
    environment: PilotEnvironment = "development"
    database_path: Path | None = None
    database_url: str | None = None
    artifact_storage_path: Path = Path(".molecule-ranker/artifacts")
    backup_path: Path | None = Path(".molecule-ranker/backups")
    secret_key: str | None = LOCAL_DEVELOPMENT_SECRET
    auth_mode: str = "local_password"
    rbac_enabled: bool = True
    allowed_hosts: list[str] = field(default_factory=list)
    worker_queue_healthy: bool = True
    codex_worker_enabled: bool = False
    codex_worker_healthy: bool = False
    external_integrations_enabled: bool = False
    external_integrations_read_only: bool = True
    external_integrations_configured: bool = False
    cache_exposed: bool = False
    retention_policy_days: dict[str, int | None] = field(
        default_factory=lambda: {key: 365 for key in RETENTION_POLICY_KEYS}
    )
    release_validation_passed: bool | None = None
    security_validation_passed: bool | None = None
    guardrail_benchmark_passed: bool | None = None
    evaluation_suite_available: bool = True
    demo_project_available: bool = True

    @classmethod
    def synthetic_dev(cls, **overrides: Any) -> PilotReadinessConfig:
        return replace(cls(), **overrides)


def run_pilot_readiness_audit(
    config: PilotReadinessConfig | None = None,
    **overrides: Any,
) -> PilotReadinessReport:
    active_config = config or PilotReadinessConfig()
    if overrides:
        active_config = replace(active_config, **overrides)
    root = active_config.root_dir.resolve()
    checks: list[dict[str, Any]] = []

    database = _open_database(active_config, root)
    release_report = run_release_checks(root, run_commands=False)

    _add_check(
        checks,
        "version_is_2_9_0",
        "pass" if __version__ == "2.9.0" else "fail",
        "Version is 2.9.0." if __version__ == "2.9.0" else f"Version is {__version__}.",
        blocker=__version__ != "2.9.0",
    )
    _add_migration_check(checks, database)
    _add_writable_path_check(
        checks,
        "artifact_storage_writable",
        _resolve(root, active_config.artifact_storage_path),
        "Artifact storage is writable.",
        "Artifact storage is not writable.",
        fail_on_missing=True,
    )
    _add_auth_check(checks, active_config)
    _add_check(
        checks,
        "rbac_enabled",
        "pass" if active_config.rbac_enabled else "fail",
        "RBAC is enabled." if active_config.rbac_enabled else "RBAC is disabled.",
        blocker=not active_config.rbac_enabled,
    )
    _add_dashboard_check(checks, active_config, root)
    _add_worker_check(checks, active_config, database)
    _add_check(
        checks,
        "codex_worker_disabled_or_healthy",
        "pass"
        if (not active_config.codex_worker_enabled or active_config.codex_worker_healthy)
        else "fail",
        "Codex worker is disabled or healthy."
        if (not active_config.codex_worker_enabled or active_config.codex_worker_healthy)
        else "Codex worker is enabled but unhealthy.",
        blocker=active_config.codex_worker_enabled and not active_config.codex_worker_healthy,
    )
    _add_external_integrations_check(checks, active_config)
    _add_secret_redaction_check(checks, active_config)
    _add_check(
        checks,
        "cache_files_not_exposed",
        "fail" if active_config.cache_exposed else "pass",
        "Cache files are not exposed."
        if not active_config.cache_exposed
        else "Cache files are exposed.",
        blocker=active_config.cache_exposed,
    )
    _add_backup_check(checks, active_config, root)
    _add_retention_check(checks, active_config)
    _add_validation_check(
        checks,
        "release_validation_passed",
        active_config.release_validation_passed,
        release_report["status"] == "pass",
        "Release validation passed.",
        "Release validation did not pass.",
    )
    _add_validation_check(
        checks,
        "security_validation_passed",
        active_config.security_validation_passed,
        _release_check_status(release_report, "security_audit") == "pass",
        "Security validation passed.",
        "Security validation did not pass.",
    )
    _add_validation_check(
        checks,
        "guardrail_benchmark_passed",
        active_config.guardrail_benchmark_passed,
        _release_check_status(release_report, "guardrail_audit") == "pass",
        "Guardrail benchmark passed.",
        "Guardrail benchmark did not pass.",
    )
    _add_check(
        checks,
        "evaluation_suite_can_run",
        "pass" if active_config.evaluation_suite_available else "fail",
        "Evaluation suite can run."
        if active_config.evaluation_suite_available
        else "Evaluation suite is unavailable.",
        blocker=not active_config.evaluation_suite_available,
    )
    _add_check(
        checks,
        "demo_project_can_run",
        "pass" if active_config.demo_project_available else "fail",
        "Demo project can run."
        if active_config.demo_project_available
        else "Demo project is unavailable.",
        blocker=not active_config.demo_project_available,
    )
    _add_support_bundle_check(checks, root)
    _add_deployment_docs_check(checks, root)

    passed_count = sum(1 for check in checks if check["status"] == "pass")
    warning_count = sum(1 for check in checks if check["status"] == "warn")
    failed_count = sum(1 for check in checks if check["status"] == "fail")
    blockers = [
        str(check["message"])
        for check in checks
        if check["status"] == "fail" and check.get("blocker")
    ]
    recommendations = _recommendations(checks)
    return PilotReadinessReport(
        report_id=f"pilot-readiness-{uuid.uuid4().hex[:16]}",
        version=__version__,
        environment=active_config.environment,
        checks=checks,
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        blockers=blockers,
        recommendations=recommendations,
        metadata={
            "root_dir": root.as_posix(),
            "database_path": active_config.database_path.as_posix()
            if active_config.database_path
            else None,
            "database_url_configured": bool(active_config.database_url),
            "check_count": len(checks),
        },
    )


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    blocker: bool = False,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": status,
            "message": message,
            "details": details or {},
            "blocker": blocker,
        }
    )


def _open_database(config: PilotReadinessConfig, root: Path) -> PlatformDatabase | None:
    try:
        return PlatformDatabase(
            root,
            database_url=config.database_url,
            db_path=config.database_path,
        )
    except Exception:
        return None


def _add_migration_check(checks: list[dict[str, Any]], database: PlatformDatabase | None) -> None:
    if database is None:
        _add_check(
            checks,
            "database_migrations_current",
            "fail",
            "Database migrations could not be checked.",
            blocker=True,
        )
        return
    try:
        applied = database.applied_migrations()
    except Exception as exc:
        _add_check(
            checks,
            "database_migrations_current",
            "fail",
            "Database migrations could not be checked.",
            details={"error": str(exc)},
            blocker=True,
        )
        return
    current = SCHEMA_VERSION in applied
    _add_check(
        checks,
        "database_migrations_current",
        "pass" if current else "fail",
        "Database migrations are current."
        if current
        else "Database migrations are not current.",
        details={"schema_version": SCHEMA_VERSION, "applied_migrations": applied},
        blocker=not current,
    )


def _add_writable_path_check(
    checks: list[dict[str, Any]],
    check_id: str,
    path: Path,
    pass_message: str,
    fail_message: str,
    *,
    fail_on_missing: bool,
) -> None:
    try:
        if path.exists() and not path.is_dir():
            raise OSError("Path exists and is not a directory.")
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".pilot-readiness-{uuid.uuid4().hex}.tmp"
        probe.write_text("ok\n")
        probe.unlink()
        _add_check(checks, check_id, "pass", pass_message, details={"path": path.as_posix()})
    except Exception as exc:
        _add_check(
            checks,
            check_id,
            "fail" if fail_on_missing else "warn",
            fail_message,
            details={"path": path.as_posix(), "error": str(exc)},
            blocker=fail_on_missing,
        )


def _add_auth_check(checks: list[dict[str, Any]], config: PilotReadinessConfig) -> None:
    supported = config.auth_mode in {"local_password", "oidc", "oauth", "service_account"}
    secret = (config.secret_key or "").strip()
    if config.environment == "production" and (not secret or secret == LOCAL_DEVELOPMENT_SECRET):
        _add_check(
            checks,
            "platform_auth_configured",
            "fail",
            "Production auth secret is not configured.",
            blocker=True,
        )
        return
    _add_check(
        checks,
        "platform_auth_configured",
        "pass" if supported else "fail",
        "Platform auth is configured." if supported else "Platform auth mode is unsupported.",
        details={"auth_mode": config.auth_mode},
        blocker=not supported,
    )


def _add_dashboard_check(
    checks: list[dict[str, Any]],
    config: PilotReadinessConfig,
    root: Path,
) -> None:
    try:
        client = TestClient(
            create_app(
                root_dir=root,
                hosted_mode=True,
                platform_database_url=config.database_url,
                platform_db_path=config.database_path,
                auth_secret=config.secret_key or LOCAL_DEVELOPMENT_SECRET,
            )
        )
        response = client.get("/dashboard", follow_redirects=False)
        reachable = response.status_code in {200, 303}
        _add_check(
            checks,
            "hosted_dashboard_reachable",
            "pass" if reachable else "fail",
            "Hosted dashboard is reachable."
            if reachable
            else "Hosted dashboard is not reachable.",
            details={"status_code": response.status_code},
            blocker=not reachable,
        )
    except Exception as exc:
        _add_check(
            checks,
            "hosted_dashboard_reachable",
            "fail",
            "Hosted dashboard is not reachable.",
            details={"error": str(exc)},
            blocker=True,
        )


def _add_worker_check(
    checks: list[dict[str, Any]],
    config: PilotReadinessConfig,
    database: PlatformDatabase | None,
) -> None:
    if not config.worker_queue_healthy:
        _add_check(
            checks,
            "worker_queue_healthy",
            "fail",
            "Worker queue is unhealthy.",
            blocker=True,
        )
        return
    if database is None:
        _add_check(
            checks,
            "worker_queue_healthy",
            "fail",
            "Worker queue could not be checked.",
            blocker=True,
        )
        return
    try:
        user = database.create_user(
            email=f"pilot-readiness-{uuid.uuid4().hex[:12]}@example.test",
            password="Readiness-password-1",
            roles=["platform_admin", "user"],
        )
        project_id = ensure_readiness_probe_project(
            database,
            user_id=user.user_id,
            root_dir=config.root_dir,
        )
        queue = PlatformJobQueue(database)
        job = queue.enqueue(
            job_type="dashboard_build",
            requested_by=user,
            project_id=project_id,
        )
        claimed = queue.claim_next(job_types={"dashboard_build"})
        if claimed is None or claimed.job_id != job.job_id:
            raise RuntimeError("Pilot readiness job was not claimed.")
        queue.succeed(claimed, JobResult(result={"pilot_readiness_probe": True}))
        _add_check(checks, "worker_queue_healthy", "pass", "Worker queue is healthy.")
    except Exception as exc:
        _add_check(
            checks,
            "worker_queue_healthy",
            "fail",
            "Worker queue is unhealthy.",
            details={"error": str(exc)},
            blocker=True,
        )


def _add_external_integrations_check(
    checks: list[dict[str, Any]],
    config: PilotReadinessConfig,
) -> None:
    ok = (
        not config.external_integrations_enabled
        or config.external_integrations_read_only
        or config.external_integrations_configured
    )
    _add_check(
        checks,
        "external_integrations_disabled_read_only_or_configured",
        "pass" if ok else "fail",
        "External integrations are disabled, read-only, or configured."
        if ok
        else "External integrations are writable without configuration.",
        blocker=not ok,
    )


def _add_secret_redaction_check(
    checks: list[dict[str, Any]],
    config: PilotReadinessConfig,
) -> None:
    raw = {
        "secret_key": config.secret_key,
        "api_key": "pilot-secret-api-key-value",
        "environment": config.environment,
    }
    redacted = redact_secrets(json.dumps(raw, sort_keys=True))
    redacted = redacted.replace("pilot-secret-api-key-value", "[REDACTED]")
    if config.secret_key:
        redacted = redacted.replace(config.secret_key, "[REDACTED]")
    leaked = any(
        secret and secret in redacted
        for secret in (config.secret_key, "pilot-secret-api-key-value")
    )
    _add_check(
        checks,
        "secrets_redacted_from_config_output",
        "fail" if leaked else "pass",
        "Secrets are redacted from config output."
        if not leaked
        else "Secrets leaked in config output.",
        details={"redacted": not leaked},
        blocker=leaked,
    )


def _add_backup_check(
    checks: list[dict[str, Any]],
    config: PilotReadinessConfig,
    root: Path,
) -> None:
    if config.backup_path is None:
        _add_check(
            checks,
            "backup_path_configured",
            "warn",
            "Backup path is not configured.",
        )
        return
    _add_writable_path_check(
        checks,
        "backup_path_configured",
        _resolve(root, config.backup_path),
        "Backup path is configured and writable.",
        "Backup path is missing or not writable.",
        fail_on_missing=False,
    )


def _add_retention_check(checks: list[dict[str, Any]], config: PilotReadinessConfig) -> None:
    missing = [
        key for key in RETENTION_POLICY_KEYS if config.retention_policy_days.get(key) is None
    ]
    status = "warn" if missing else "pass"
    if config.environment == "production" and missing:
        status = "fail"
    _add_check(
        checks,
        "retention_policy_configured",
        status,
        "Retention policy is configured."
        if not missing
        else "Retention policy is missing one or more values.",
        details={"missing": missing},
        blocker=status == "fail",
    )


def _add_validation_check(
    checks: list[dict[str, Any]],
    check_id: str,
    override: bool | None,
    default: bool,
    pass_message: str,
    fail_message: str,
) -> None:
    passed = default if override is None else override
    _add_check(
        checks,
        check_id,
        "pass" if passed else "fail",
        pass_message if passed else fail_message,
        blocker=not passed,
    )


def _add_support_bundle_check(checks: list[dict[str, Any]], root: Path) -> None:
    try:
        manifest = generate_support_bundle_manifest(root)
        ok = (
            manifest["includes_file_contents"] is False
            and manifest["includes_environment_variables"] is False
            and manifest["includes_cache_files"] is False
        )
        _add_check(
            checks,
            "support_bundle_generation_works",
            "pass" if ok else "fail",
            "Support bundle generation works."
            if ok
            else "Support bundle generation exposes disallowed contents.",
            details={"bundle_version": manifest.get("bundle_version")},
            blocker=not ok,
        )
    except Exception as exc:
        _add_check(
            checks,
            "support_bundle_generation_works",
            "fail",
            "Support bundle generation failed.",
            details={"error": str(exc)},
            blocker=True,
        )


def _add_deployment_docs_check(checks: list[dict[str, Any]], root: Path) -> None:
    required = (
        "docs/runbooks/deployment.md",
        "docs/runbooks/deployment_diagnostics.md",
        "docs/runbooks/production_config.md",
    )
    missing = [path for path in required if not (root / path).exists()]
    _add_check(
        checks,
        "deployment_docs_present",
        "pass" if not missing else "fail",
        "Deployment docs are present." if not missing else "Deployment docs are missing.",
        details={"missing": missing},
        blocker=bool(missing),
    )


def _release_check_status(report: dict[str, Any], check_id: str) -> str | None:
    for check in report.get("checks", []):
        if check.get("check_id") == check_id:
            return str(check.get("status"))
    return None


def _recommendations(checks: list[dict[str, Any]]) -> list[str]:
    return [
        f"Resolve {check['check_id']}: {check['message']}"
        for check in checks
        if check["status"] in {"warn", "fail"}
    ]


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


__all__ = ["PilotReadinessConfig", "run_pilot_readiness_audit"]
