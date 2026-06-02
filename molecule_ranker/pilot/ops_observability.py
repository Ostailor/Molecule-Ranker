from __future__ import annotations

import json
import re
import resource
import shutil
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.database import SCHEMA_VERSION, PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.platform.observability import metrics as runtime_metrics

PILOT_OPS_VERSION = "pilot-ops-observability.v1.9"

ALERT_RULES: tuple[dict[str, Any], ...] = (
    {"alert_type": "worker_down", "severity": "critical", "target": "worker_health == healthy"},
    {"alert_type": "queue_backlog_high", "severity": "warning", "target": "queued_jobs <= 50"},
    {"alert_type": "job_failure_rate_high", "severity": "warning", "target": "failure_rate < 0.10"},
    {
        "alert_type": "codex_guardrail_failures_high",
        "severity": "warning",
        "target": "codex_guardrail_failures <= 2",
    },
    {
        "alert_type": "integration_failures_high",
        "severity": "warning",
        "target": "integration_failure_count <= 2",
    },
    {
        "alert_type": "storage_write_failures",
        "severity": "critical",
        "target": "artifact_storage_write_failures == 0",
    },
    {"alert_type": "auth_failures_high", "severity": "warning", "target": "auth_failures <= 10"},
    {
        "alert_type": "webhook_signature_failures_high",
        "severity": "warning",
        "target": "webhook_signature_failures <= 5",
    },
    {"alert_type": "disk_space_low", "severity": "critical", "target": "free_disk_ratio >= 0.10"},
    {
        "alert_type": "backup_stale",
        "severity": "warning",
        "target": "latest_backup_age_hours <= 24",
    },
    {
        "alert_type": "migration_pending",
        "severity": "warning",
        "target": "migrations_current == true",
    },
)

INTERNAL_TARGETS = {
    "availability_target": "99.0% internal pilot availability target",
    "api_latency_p95_seconds": 2.0,
    "job_start_latency_p95_seconds": 300.0,
    "job_failure_rate": 0.10,
    "codex_guardrail_failure_count": 2,
    "integration_failure_count": 2,
    "queue_backlog_jobs": 50,
    "backup_max_age_hours": 24,
    "disk_free_ratio": 0.10,
    "memory_warning_mb": 2048,
}

FAILURE_STATUSES = {"failed", "timed_out", "dead_lettered", "guardrail_failed"}
INTEGRATION_JOB_TYPES = {
    "integration_sync",
    "webhook_processing",
    "warehouse_export",
    "registry_mapping_review",
    "external_export",
    "connector_health_check",
}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|service[_-]?token|token)"
    r"\s*[:=]\s*[^\s,;]+"
)


def build_ops_metrics(
    *,
    root_dir: str | Path = ".",
    database_url: str | None = None,
    db_path: str | Path | None = None,
    backup_path: str | Path | None = None,
    job_limit: int = 500,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    database = _open_database(root, database_url=database_url, db_path=db_path)
    jobs, platform_health, audit_events, migrations_current = _database_inputs(database)
    backup_summary = _backup_summary(root, backup_path=backup_path)
    disk_summary = _disk_summary(root)
    memory_summary = _memory_summary()
    runtime_snapshot = runtime_metrics.snapshot()

    selected_jobs = jobs[: max(1, job_limit)]
    job_monitoring = _job_monitoring(selected_jobs)
    audit_monitoring = _audit_monitoring(audit_events)
    latency = _latency_percentiles(selected_jobs)
    health_trends = _health_trends(root, current_health=platform_health)

    report = {
        "report_id": f"ops-metrics-{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "ops_version": PILOT_OPS_VERSION,
        "alert_rules": list(ALERT_RULES),
        "internal_targets": INTERNAL_TARGETS,
        "metrics_dashboard": {
            "title": "molecule-ranker enterprise pilot operations",
            "panels": _dashboard_panels(),
        },
        "runtime_metrics": runtime_snapshot,
        "platform_health": platform_health,
        "health_trend_summaries": health_trends,
        "job_monitoring": job_monitoring,
        "codex_guardrail_monitoring": _codex_guardrail_monitoring(selected_jobs, runtime_snapshot),
        "integration_sync_monitoring": _integration_monitoring(selected_jobs),
        "assay_import_monitoring": _assay_import_monitoring(selected_jobs),
        "artifact_storage_monitoring": _artifact_storage_monitoring(
            selected_jobs,
            runtime_snapshot,
        ),
        "queue_backlog_monitoring": {
            "queued_count": job_monitoring["queued_count"],
            "target": INTERNAL_TARGETS["queue_backlog_jobs"],
        },
        "auth_monitoring": {
            "auth_failures": audit_monitoring["auth_failures"],
            "target": 10,
        },
        "webhook_monitoring": {
            "signature_failures": audit_monitoring["webhook_signature_failures"],
            "target": 5,
        },
        "latency_percentiles": latency,
        "memory_monitoring": memory_summary,
        "disk_monitoring": disk_summary,
        "backup_monitoring": backup_summary,
        "migration_monitoring": {"migrations_current": migrations_current},
        "contains_secret_values": False,
    }
    return _redact_json(report)


def build_ops_alerts(metrics_report: dict[str, Any]) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    job_monitoring = metrics_report.get("job_monitoring", {})
    codex = metrics_report.get("codex_guardrail_monitoring", {})
    integration = metrics_report.get("integration_sync_monitoring", {})
    artifact = metrics_report.get("artifact_storage_monitoring", {})
    auth = metrics_report.get("auth_monitoring", {})
    webhook = metrics_report.get("webhook_monitoring", {})
    disk = metrics_report.get("disk_monitoring", {})
    backup = metrics_report.get("backup_monitoring", {})
    migration = metrics_report.get("migration_monitoring", {})
    platform_health = metrics_report.get("platform_health", {})

    if not bool(platform_health.get("ok", False)) and platform_health.get(
        "reason"
    ) == "worker_down":
        alerts.append(_alert("worker_down", "critical", "Worker health check reports down."))
    if int(job_monitoring.get("queued_count", 0)) > INTERNAL_TARGETS["queue_backlog_jobs"]:
        alerts.append(
            _alert("queue_backlog_high", "warning", "Queued job backlog is above target.")
        )
    if float(job_monitoring.get("failure_rate", 0.0)) >= INTERNAL_TARGETS["job_failure_rate"]:
        alerts.append(
            _alert("job_failure_rate_high", "warning", "Recent job failure rate is high.")
        )
    if (
        int(codex.get("guardrail_failure_count", 0))
        > INTERNAL_TARGETS["codex_guardrail_failure_count"]
    ):
        alerts.append(
            _alert(
                "codex_guardrail_failures_high",
                "warning",
                "Codex guardrail failures are high.",
            )
        )
    if int(integration.get("failure_count", 0)) > INTERNAL_TARGETS["integration_failure_count"]:
        alerts.append(
            _alert("integration_failures_high", "warning", "Integration failures are high.")
        )
    if int(artifact.get("write_failure_count", 0)) > 0:
        alerts.append(
            _alert(
                "storage_write_failures",
                "critical",
                "Artifact storage write failures detected.",
            )
        )
    if int(auth.get("auth_failures", 0)) > int(auth.get("target", 10)):
        alerts.append(
            _alert("auth_failures_high", "warning", "Authentication failures are above target.")
        )
    if int(webhook.get("signature_failures", 0)) > int(webhook.get("target", 5)):
        alerts.append(
            _alert(
                "webhook_signature_failures_high",
                "warning",
                "Webhook signature failures are high.",
            )
        )
    if float(disk.get("free_ratio", 1.0)) < INTERNAL_TARGETS["disk_free_ratio"]:
        alerts.append(_alert("disk_space_low", "critical", "Disk free space is below target."))
    if bool(backup.get("stale", False)):
        alerts.append(_alert("backup_stale", "warning", "Latest backup is stale or missing."))
    if not bool(migration.get("migrations_current", True)):
        alerts.append(_alert("migration_pending", "warning", "Platform migrations are pending."))
    return _redact_json(
        {
            "report_id": f"ops-alerts-{uuid.uuid4().hex[:12]}",
            "created_at": datetime.now(UTC).isoformat(),
            "version": __version__,
            "ops_version": PILOT_OPS_VERSION,
            "alert_count": len(alerts),
            "alerts": alerts,
            "contains_secret_values": False,
        }
    )


def build_health_history(
    *,
    root_dir: str | Path = ".",
    database_url: str | None = None,
    db_path: str | Path | None = None,
    backup_path: str | Path | None = None,
) -> dict[str, Any]:
    current = build_ops_metrics(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        backup_path=backup_path,
    )
    return _redact_json(
        {
            "report_id": f"ops-health-history-{uuid.uuid4().hex[:12]}",
            "created_at": datetime.now(UTC).isoformat(),
            "version": __version__,
            "health_trend_summaries": current["health_trend_summaries"],
            "platform_health": current["platform_health"],
            "latency_percentiles": current["latency_percentiles"],
            "queue_backlog_monitoring": current["queue_backlog_monitoring"],
            "contains_secret_values": False,
        }
    )


def _open_database(
    root: Path,
    *,
    database_url: str | None,
    db_path: str | Path | None,
) -> PlatformDatabase | None:
    try:
        resolved_db_path = (
            Path(db_path) if db_path else root / ".molecule-ranker" / "platform.sqlite"
        )
        if database_url is None and not resolved_db_path.exists():
            return None
        return PlatformDatabase(
            root,
            database_url=database_url,
            db_path=resolved_db_path if database_url is None else None,
            initialize=False,
        )
    except Exception:
        return None


def _database_inputs(
    database: PlatformDatabase | None,
) -> tuple[list[Any], dict[str, Any], list[Any], bool]:
    if database is None:
        return [], {"ok": False, "reason": "platform_database_unavailable"}, [], False
    try:
        jobs = PlatformJobQueue(database).list_jobs(limit=500)
        health = database.health()
        audit_events = database.list_audit_events(limit=500)
        migrations_current = SCHEMA_VERSION in set(database.applied_migrations())
        return jobs, health, audit_events, migrations_current
    except Exception as exc:
        return [], {"ok": False, "error": redact_secrets(str(exc))}, [], False


def _job_monitoring(jobs: list[Any]) -> dict[str, Any]:
    statuses = Counter(str(job.status) for job in jobs)
    failures = [job for job in jobs if str(job.status) in FAILURE_STATUSES]
    total = len(jobs)
    return {
        "recent_job_count": total,
        "queued_count": statuses.get("queued", 0),
        "running_count": statuses.get("running", 0),
        "failed_count": len(failures),
        "failure_rate": len(failures) / total if total else 0.0,
        "status_counts": dict(statuses),
        "recent_failures": [_safe_job_failure(job) for job in failures[:10]],
    }


def _safe_job_failure(job: Any) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "error_summary": redact_secrets(job.error_summary or ""),
    }


def _audit_monitoring(audit_events: list[Any]) -> dict[str, int]:
    auth_failures = 0
    webhook_signature_failures = 0
    for event in audit_events:
        event_type = str(getattr(event, "event_type", "")).lower()
        summary = str(getattr(event, "summary", "")).lower()
        if "auth" in event_type and any(term in event_type for term in ("fail", "denied")):
            auth_failures += 1
        if "login_failed" in event_type or "permission_denied" in event_type:
            auth_failures += 1
        if "webhook" in event_type and "signature" in f"{event_type} {summary}":
            webhook_signature_failures += 1
    return {
        "auth_failures": auth_failures,
        "webhook_signature_failures": webhook_signature_failures,
    }


def _codex_guardrail_monitoring(jobs: list[Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    counter = snapshot.get("counters", {}).get("codex_guardrail_failures_total", 0)
    job_failures = [
        job
        for job in jobs
        if str(job.status) == "guardrail_failed" or str(job.job_type) == "codex_task"
    ]
    guardrail_jobs = [job for job in job_failures if str(job.status) in FAILURE_STATUSES]
    return {
        "guardrail_failure_count": int(counter) + len(guardrail_jobs),
        "failed_codex_job_count": len(guardrail_jobs),
        "target": INTERNAL_TARGETS["codex_guardrail_failure_count"],
    }


def _integration_monitoring(jobs: list[Any]) -> dict[str, Any]:
    integration_jobs = [job for job in jobs if str(job.job_type) in INTEGRATION_JOB_TYPES]
    failures = [job for job in integration_jobs if str(job.status) in FAILURE_STATUSES]
    return {
        "job_count": len(integration_jobs),
        "failure_count": len(failures),
        "failure_rate": len(failures) / len(integration_jobs) if integration_jobs else 0.0,
    }


def _assay_import_monitoring(jobs: list[Any]) -> dict[str, Any]:
    assay_jobs = [
        job
        for job in jobs
        if "assay" in str(job.job_type) or "import" in str(job.job_type)
    ]
    failures = [job for job in assay_jobs if str(job.status) in FAILURE_STATUSES]
    return {
        "job_count": len(assay_jobs),
        "failure_count": len(failures),
        "failure_rate": len(failures) / len(assay_jobs) if assay_jobs else 0.0,
    }


def _artifact_storage_monitoring(jobs: list[Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    errors = [
        job
        for job in jobs
        if str(job.status) in FAILURE_STATUSES
        and any(
            term in str(job.error_summary or "").lower()
            for term in ("artifact", "storage", "write")
        )
    ]
    writes = snapshot.get("counters", {}).get("artifacts_written_total", 0)
    return {
        "write_failure_count": len(errors),
        "artifacts_written_total": writes,
    }


def _latency_percentiles(jobs: list[Any]) -> dict[str, Any]:
    durations = []
    queue_waits = []
    for job in jobs:
        if job.started_at and job.completed_at:
            durations.append(max(0.0, (job.completed_at - job.started_at).total_seconds()))
        if job.started_at:
            queue_waits.append(max(0.0, (job.started_at - job.created_at).total_seconds()))
    return {
        "job_run_seconds": _percentiles(durations),
        "queue_wait_seconds": _percentiles(queue_waits),
    }


def _percentiles(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    sorted_values = sorted(values)
    return {
        "count": len(sorted_values),
        "p50": _percentile(sorted_values, 0.50),
        "p90": _percentile(sorted_values, 0.90),
        "p95": _percentile(sorted_values, 0.95),
        "p99": _percentile(sorted_values, 0.99),
    }


def _percentile(sorted_values: list[float], quantile: float) -> float:
    index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * quantile)))
    return float(sorted_values[index])


def _backup_summary(root: Path, *, backup_path: str | Path | None) -> dict[str, Any]:
    path = Path(backup_path).resolve() if backup_path else root / ".molecule-ranker" / "backups"
    backups = [item for item in path.glob("*") if item.is_file()] if path.exists() else []
    if not backups:
        return {"path": str(path), "latest_backup": None, "latest_age_hours": None, "stale": True}
    latest = max(backups, key=lambda item: item.stat().st_mtime)
    modified_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
    age_hours = (datetime.now(UTC) - modified_at).total_seconds() / 3600
    return {
        "path": str(path),
        "latest_backup": latest.name,
        "latest_age_hours": age_hours,
        "stale": age_hours > INTERNAL_TARGETS["backup_max_age_hours"],
    }


def _disk_summary(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    free_ratio = usage.free / usage.total if usage.total else 0.0
    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "free_ratio": free_ratio,
        "target_free_ratio": INTERNAL_TARGETS["disk_free_ratio"],
    }


def _memory_summary() -> dict[str, Any]:
    max_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if max_rss_kb > 10_000_000:
        max_rss_mb = max_rss_kb / (1024 * 1024)
    else:
        max_rss_mb = max_rss_kb / 1024
    threshold = INTERNAL_TARGETS["memory_warning_mb"]
    return {
        "max_rss_mb": max_rss_mb,
        "warning_threshold_mb": threshold,
        "warning": max_rss_mb > threshold,
    }


def _health_trends(root: Path, *, current_health: dict[str, Any]) -> dict[str, Any]:
    history_dir = root / ".molecule-ranker" / "health-history"
    samples: list[dict[str, Any]] = []
    if history_dir.exists():
        for path in sorted(history_dir.glob("*.json"))[-50:]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            samples.append(_redact_json(payload))
    samples.append({"timestamp": datetime.now(UTC).isoformat(), "health": current_health})
    ok_count = sum(1 for sample in samples if bool(sample.get("health", sample).get("ok", False)))
    return {
        "sample_count": len(samples),
        "ok_count": ok_count,
        "degraded_count": len(samples) - ok_count,
        "latest": samples[-1] if samples else {},
    }


def _dashboard_panels() -> list[dict[str, Any]]:
    return [
        {"id": "platform_health", "title": "Platform health", "type": "stat"},
        {"id": "queue_backlog", "title": "Queue backlog", "type": "timeseries"},
        {"id": "job_failure_rate", "title": "Job failure rate", "type": "gauge"},
        {"id": "codex_guardrails", "title": "Codex guardrail failures", "type": "stat"},
        {"id": "integration_failures", "title": "Integration sync failures", "type": "stat"},
        {"id": "artifact_storage", "title": "Artifact storage errors", "type": "stat"},
        {"id": "latency_percentiles", "title": "Latency percentiles", "type": "table"},
        {"id": "memory", "title": "Memory warnings", "type": "stat"},
    ]


def _alert(alert_type: str, severity: str, message: str) -> dict[str, Any]:
    rule = next((item for item in ALERT_RULES if item["alert_type"] == alert_type), {})
    return {
        "alert_id": f"alert-{uuid.uuid4().hex[:12]}",
        "alert_type": alert_type,
        "severity": severity,
        "message": message,
        "target": rule.get("target"),
    }


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            sensitive_parts = ("api_key", "password", "secret", "token", "credential")
            if any(part in lowered for part in sensitive_parts):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return SENSITIVE_ASSIGNMENT_RE.sub("[REDACTED]", redact_secrets(value))


__all__ = [
    "ALERT_RULES",
    "INTERNAL_TARGETS",
    "PILOT_OPS_VERSION",
    "build_health_history",
    "build_ops_alerts",
    "build_ops_metrics",
]
