from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
from typing import Any, Literal

from sqlalchemy import select

from molecule_ranker import __version__
from molecule_ranker.platform.database import (
    artifact_records,
    integration_sync_jobs,
    platform_audit_events,
    platform_jobs,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.observability import metrics as runtime_metrics_registry
from molecule_ranker.platform.observability import redact_for_log

SLO_REPORT_TYPE = "v2_slo_report"
SLO_REPORT_VERSION = "2.0"
TERMINAL_JOB_STATUSES = {
    "succeeded",
    "failed",
    "partial",
    "cancelled",
    "timed_out",
    "dead_lettered",
    "guardrail_failed",
}
FAILED_JOB_STATUSES = {"failed", "partial", "timed_out", "dead_lettered", "guardrail_failed"}
SUCCESSFUL_SYNC_STATUSES = {"succeeded", "success", "completed", "complete", "passed"}
FAILED_SYNC_STATUSES = {"failed", "error", "errored", "partial", "rejected"}
MAX_BACKUP_AGE_HOURS = 24.0
MISSING_BACKUP_AGE_HOURS = 999999.0

Comparator = Literal["gte", "lte"]


@dataclass(frozen=True)
class SLODefinition:
    slo_id: str
    name: str
    description: str
    target: float
    unit: str
    comparator: Comparator = "gte"
    window_hours: int = 24

    def to_dict(self) -> dict[str, Any]:
        return {
            "slo_id": self.slo_id,
            "name": self.name,
            "description": self.description,
            "target": self.target,
            "unit": self.unit,
            "comparator": self.comparator,
            "window_hours": self.window_hours,
        }


@dataclass(frozen=True)
class SLOMeasurement:
    definition: SLODefinition
    status: Literal["pass", "fail"]
    observed_value: float
    total_events: int = 0
    bad_events: int = 0
    source: str = "platform_database"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            **self.definition.to_dict(),
            "status": self.status,
            "observed_value": _round(self.observed_value),
            "total_events": self.total_events,
            "bad_events": self.bad_events,
            "source": self.source,
            "details": self.details,
        }
        if self.definition.slo_id == "backup_freshness":
            payload["observed_value_hours"] = _round(self.observed_value)
        return redact_for_log(payload)


@dataclass(frozen=True)
class SLOReport:
    generated_at: datetime
    measurements: list[SLOMeasurement]
    runtime_metrics: dict[str, Any] = field(default_factory=dict)
    report_id: str = "v2-slo-report"
    version: str = __version__
    report_version: str = SLO_REPORT_VERSION

    @property
    def status(self) -> Literal["pass", "fail"]:
        return "fail" if any(item.status == "fail" for item in self.measurements) else "pass"

    @property
    def error_budget_summary(self) -> dict[str, Any]:
        items = [_error_budget(item) for item in self.measurements]
        return redact_for_log(
            {
                "overall_status": self.status,
                "failed_slos": [
                    item.definition.slo_id for item in self.measurements if item.status == "fail"
                ],
                "items": items,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return redact_for_log(
            {
                "report_type": SLO_REPORT_TYPE,
                "report_id": self.report_id,
                "report_version": self.report_version,
                "generated_at": self.generated_at.isoformat(),
                "version": self.version,
                "status": self.status,
                "definitions": [definition.to_dict() for definition in DEFAULT_V2_SLOS],
                "measurements": [item.to_dict() for item in self.measurements],
                "error_budget_summary": self.error_budget_summary,
                "runtime_metrics": self.runtime_metrics,
                "notes": [
                    "SLO reporting is software/platform operational evidence.",
                    "Metrics are redacted and must not include secrets or tokens.",
                ],
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


DEFAULT_V2_SLOS: tuple[SLODefinition, ...] = (
    SLODefinition(
        "api_availability",
        "API availability",
        "Share of observed API requests not recorded as failures.",
        0.995,
        "ratio",
    ),
    SLODefinition(
        "job_success_rate",
        "Job success rate",
        "Share of terminal platform jobs that completed successfully.",
        0.99,
        "ratio",
    ),
    SLODefinition(
        "job_queue_latency",
        "Job queue latency",
        "P95 time from queued to started for platform jobs.",
        300.0,
        "seconds",
        comparator="lte",
    ),
    SLODefinition(
        "artifact_write_success",
        "Artifact write success",
        "Share of artifact write attempts without recorded storage failures.",
        0.999,
        "ratio",
    ),
    SLODefinition(
        "codex_guardrail_pass_rate",
        "Codex guardrail pass rate",
        "Share of terminal Codex tasks that did not fail guardrails.",
        0.995,
        "ratio",
    ),
    SLODefinition(
        "integration_sync_success",
        "Integration sync success",
        "Share of terminal external integration sync jobs that completed successfully.",
        0.99,
        "ratio",
    ),
    SLODefinition(
        "backup_freshness",
        "Backup freshness",
        "Age of the newest backup artifact.",
        MAX_BACKUP_AGE_HOURS,
        "hours",
        comparator="lte",
    ),
    SLODefinition(
        "dashboard_latency",
        "Dashboard latency",
        "Observed dashboard response latency.",
        2.0,
        "seconds",
        comparator="lte",
    ),
    SLODefinition(
        "auth_failure_rate",
        "Auth failure rate",
        "Share of observed authentication events that failed.",
        0.05,
        "ratio",
        comparator="lte",
    ),
    SLODefinition(
        "support_bundle_generation_success",
        "Support bundle generation success",
        "Share of support bundle generation attempts without failure.",
        0.99,
        "ratio",
    ),
)


def generate_slo_report(
    *,
    database: PlatformDatabase,
    backup_path: str | Path | None = None,
    runtime_metrics: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> SLOReport:
    now = generated_at or datetime.now(UTC)
    jobs = _job_rows(database)
    audits = _audit_rows(database)
    sync_jobs = _sync_rows(database)
    artifact_count = _artifact_count(database)
    runtime_snapshot = redact_for_log(runtime_metrics or runtime_metrics_registry.snapshot())
    measurements = [
        _api_availability(audits),
        _job_success_rate(jobs),
        _job_queue_latency(jobs),
        _artifact_write_success(audits, artifact_count),
        _codex_guardrail_pass_rate(jobs),
        _integration_sync_success(sync_jobs, jobs),
        _backup_freshness(backup_path, now=now),
        _dashboard_latency(runtime_snapshot),
        _auth_failure_rate(audits),
        _support_bundle_success(audits),
    ]
    return SLOReport(generated_at=now, measurements=measurements, runtime_metrics=runtime_snapshot)


def write_slo_report(report: SLOReport, output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json())
    return target


def _api_availability(audits: list[dict[str, Any]]) -> SLOMeasurement:
    total = _count_events(audits, ("api_request",))
    failures = _count_events(audits, ("api_request_failed",))
    if total == 0 and failures:
        total = failures
    value = _ratio(total - failures, total, default=1.0)
    return _measurement("api_availability", value, total_events=total, bad_events=failures)


def _job_success_rate(jobs: list[dict[str, Any]]) -> SLOMeasurement:
    terminal = [job for job in jobs if str(job.get("status")) in TERMINAL_JOB_STATUSES]
    failures = [job for job in terminal if str(job.get("status")) in FAILED_JOB_STATUSES]
    value = _ratio(len(terminal) - len(failures), len(terminal), default=1.0)
    return _measurement(
        "job_success_rate",
        value,
        total_events=len(terminal),
        bad_events=len(failures),
        details={"status_counts": _status_counts(terminal)},
    )


def _job_queue_latency(jobs: list[dict[str, Any]]) -> SLOMeasurement:
    latencies = [
        max(0.0, (_aware(job["started_at"]) - _aware(job["created_at"])).total_seconds())
        for job in jobs
        if job.get("started_at") is not None and job.get("created_at") is not None
    ]
    observed = _p95(latencies) if latencies else 0.0
    bad = sum(1 for latency in latencies if latency > _definition("job_queue_latency").target)
    return _measurement(
        "job_queue_latency",
        observed,
        total_events=len(latencies),
        bad_events=bad,
        details={"sample_count": len(latencies)},
    )


def _artifact_write_success(audits: list[dict[str, Any]], artifact_count: int) -> SLOMeasurement:
    failures = _count_events(audits, ("artifact_write_failed", "artifact_storage_write_failed"))
    total = artifact_count + failures
    value = _ratio(artifact_count, total, default=1.0)
    return _measurement(
        "artifact_write_success",
        value,
        total_events=total,
        bad_events=failures,
        details={"artifact_count": artifact_count},
    )


def _codex_guardrail_pass_rate(jobs: list[dict[str, Any]]) -> SLOMeasurement:
    terminal = [
        job
        for job in jobs
        if str(job.get("job_type")) == "codex_task"
        and str(job.get("status")) in TERMINAL_JOB_STATUSES
    ]
    failures = [job for job in terminal if str(job.get("status")) == "guardrail_failed"]
    value = _ratio(len(terminal) - len(failures), len(terminal), default=1.0)
    return _measurement(
        "codex_guardrail_pass_rate",
        value,
        total_events=len(terminal),
        bad_events=len(failures),
    )


def _integration_sync_success(
    sync_jobs: list[dict[str, Any]],
    platform_job_rows: list[dict[str, Any]],
) -> SLOMeasurement:
    terminal_syncs = [
        row
        for row in sync_jobs
        if str(row.get("status")).lower() in SUCCESSFUL_SYNC_STATUSES | FAILED_SYNC_STATUSES
    ]
    platform_syncs = [
        job
        for job in platform_job_rows
        if str(job.get("job_type")) == "integration_sync"
        and str(job.get("status")) in TERMINAL_JOB_STATUSES
    ]
    total = len(terminal_syncs) + len(platform_syncs)
    failures = sum(
        1 for row in terminal_syncs if str(row.get("status")).lower() in FAILED_SYNC_STATUSES
    ) + sum(1 for job in platform_syncs if str(job.get("status")) in FAILED_JOB_STATUSES)
    value = _ratio(total - failures, total, default=1.0)
    return _measurement(
        "integration_sync_success",
        value,
        total_events=total,
        bad_events=failures,
    )


def _backup_freshness(backup_path: str | Path | None, *, now: datetime) -> SLOMeasurement:
    backup_root = Path(backup_path) if backup_path is not None else Path(".molecule-ranker/backups")
    candidates = (
        [path for path in backup_root.rglob("*") if path.is_file()]
        if backup_root.exists()
        else []
    )
    latest = max(candidates, key=lambda path: path.stat().st_mtime, default=None)
    if latest is None:
        age_hours = MISSING_BACKUP_AGE_HOURS
        details = {"backup_path": str(backup_root), "latest_backup_path": None, "missing": True}
    else:
        modified_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        age_hours = max(0.0, (now - modified_at).total_seconds() / 3600.0)
        details = {
            "backup_path": str(backup_root),
            "latest_backup_path": str(latest),
            "latest_backup_modified_at": modified_at.isoformat(),
        }
    return _measurement(
        "backup_freshness",
        age_hours,
        total_events=1 if latest else 0,
        bad_events=1 if age_hours > MAX_BACKUP_AGE_HOURS else 0,
        source="backup_storage",
        details=details,
    )


def _dashboard_latency(runtime_snapshot: dict[str, Any]) -> SLOMeasurement:
    observed = _runtime_average(runtime_snapshot, "dashboard_request_duration_seconds")
    if observed is None:
        observed = _runtime_average(runtime_snapshot, "api_request_duration_seconds") or 0.0
    return _measurement("dashboard_latency", observed, total_events=1, bad_events=0)


def _auth_failure_rate(audits: list[dict[str, Any]]) -> SLOMeasurement:
    auth_events = [
        row
        for row in audits
        if "auth" in str(row.get("event_type", "")).lower()
        or "login" in str(row.get("event_type", "")).lower()
        or "session" in str(row.get("event_type", "")).lower()
    ]
    failures = [
        row
        for row in auth_events
        if any(
            marker in str(row.get("event_type", "")).lower()
            for marker in ("failed", "failure", "denied")
        )
    ]
    value = _ratio(len(failures), len(auth_events), default=0.0)
    return _measurement(
        "auth_failure_rate",
        value,
        total_events=len(auth_events),
        bad_events=len(failures),
    )


def _support_bundle_success(audits: list[dict[str, Any]]) -> SLOMeasurement:
    support_events = [
        row for row in audits if "support_bundle" in str(row.get("event_type", "")).lower()
    ]
    failures = [
        row
        for row in support_events
        if any(
            marker in str(row.get("event_type", "")).lower()
            for marker in ("failed", "failure", "denied")
        )
    ]
    value = _ratio(len(support_events) - len(failures), len(support_events), default=1.0)
    return _measurement(
        "support_bundle_generation_success",
        value,
        total_events=len(support_events),
        bad_events=len(failures),
    )


def _measurement(
    slo_id: str,
    observed_value: float,
    *,
    total_events: int,
    bad_events: int,
    source: str = "platform_database",
    details: dict[str, Any] | None = None,
) -> SLOMeasurement:
    definition = _definition(slo_id)
    if definition.comparator == "gte":
        status: Literal["pass", "fail"] = "pass" if observed_value >= definition.target else "fail"
    else:
        status = "pass" if observed_value <= definition.target else "fail"
    return SLOMeasurement(
        definition=definition,
        status=status,
        observed_value=observed_value,
        total_events=total_events,
        bad_events=bad_events,
        source=source,
        details=details or {},
    )


def _error_budget(measurement: SLOMeasurement) -> dict[str, Any]:
    definition = measurement.definition
    if definition.comparator == "gte":
        allowed_bad_events = measurement.total_events * max(0.0, 1.0 - definition.target)
        consumed = measurement.bad_events
        remaining = allowed_bad_events - consumed
    else:
        allowed_bad_events = definition.target
        consumed = max(0.0, measurement.observed_value - definition.target)
        remaining = -consumed if consumed else definition.target - measurement.observed_value
    return {
        "slo_id": definition.slo_id,
        "status": measurement.status,
        "allowed_bad_events_or_margin": _round(allowed_bad_events),
        "consumed_bad_events_or_margin": _round(consumed),
        "remaining_budget_or_margin": _round(remaining),
    }


def _job_rows(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = connection.execute(select(platform_jobs)).mappings().fetchall()
    return [dict(row) for row in rows]


def _audit_rows(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = connection.execute(select(platform_audit_events)).mappings().fetchall()
    return [dict(row) for row in rows]


def _sync_rows(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = connection.execute(select(integration_sync_jobs)).mappings().fetchall()
    return [dict(row) for row in rows]


def _artifact_count(database: PlatformDatabase) -> int:
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records.c.artifact_id)).fetchall()
    return len(rows)


def _count_events(rows: list[dict[str, Any]], event_types: tuple[str, ...]) -> int:
    needles = {event_type.lower() for event_type in event_types}
    return sum(1 for row in rows if str(row.get("event_type", "")).lower() in needles)


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _ratio(numerator: int, denominator: int, *, default: float) -> float:
    if denominator <= 0:
        return default
    return numerator / denominator


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return quantiles(values, n=20, method="inclusive")[18]


def _runtime_average(runtime_snapshot: dict[str, Any], name: str) -> float | None:
    summaries = runtime_snapshot.get("summaries")
    if not isinstance(summaries, dict):
        return None
    summary = summaries.get(name)
    if not isinstance(summary, dict):
        return None
    count = int(summary.get("count") or 0)
    if count <= 0:
        return None
    return float(summary.get("sum") or 0.0) / count


def _definition(slo_id: str) -> SLODefinition:
    for definition in DEFAULT_V2_SLOS:
        if definition.slo_id == slo_id:
            return definition
    raise KeyError(slo_id)


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.fromisoformat(str(value))


def _round(value: float) -> float:
    return round(float(value), 6)


__all__ = [
    "DEFAULT_V2_SLOS",
    "SLODefinition",
    "SLOMeasurement",
    "SLOReport",
    "generate_slo_report",
    "write_slo_report",
]
