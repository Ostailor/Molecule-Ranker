from __future__ import annotations

import json
import logging
import re
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from molecule_ranker.codex_backbone.guardrails import redact_secrets

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

COUNTER_NAMES = (
    "pipeline_runs_total",
    "pipeline_run_failures_total",
    "jobs_queued_total",
    "jobs_failed_total",
    "codex_tasks_total",
    "codex_guardrail_failures_total",
    "artifacts_written_total",
    "auth_failures_total",
)

SUMMARY_NAMES = (
    "api_request_duration_seconds",
    "pipeline_step_duration_seconds",
    "job_duration_seconds",
    "artifact_write_duration_seconds",
)

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "codex_credential",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "service_token",
    "token",
)

MAX_LOG_STRING_BYTES = 4096
LOG_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\bauthorization=\[REDACTED\]\s+[A-Za-z0-9._~+/=-]{8,}"),
        "authorization=[REDACTED]",
    ),
    (re.compile(r"\bmrs_[A-Za-z0-9._~+/=-]{8,}"), "mrs_[REDACTED]"),
    (re.compile(r"\bmrr_[A-Za-z0-9._~+/=-]{8,}"), "mrr_[REDACTED]"),
)


@dataclass
class SummaryValues:
    count: int = 0
    total: float = 0.0


@dataclass
class MetricsRegistry:
    """Small in-process Prometheus-style registry for the V0.9 internal MVP."""

    counters: dict[str, float] = field(default_factory=dict)
    summaries: dict[str, SummaryValues] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.counters = {name: 0.0 for name in COUNTER_NAMES}
            self.summaries = {name: SummaryValues() for name in SUMMARY_NAMES}

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0.0) + value

    def observe(self, name: str, value: float) -> None:
        if value < 0:
            value = 0.0
        with self._lock:
            summary = self.summaries.setdefault(name, SummaryValues())
            summary.count += 1
            summary.total += value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "summaries": {
                    name: {"count": summary.count, "sum": summary.total}
                    for name, summary in self.summaries.items()
                },
            }

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self.counters)
            summaries = {
                name: SummaryValues(count=summary.count, total=summary.total)
                for name, summary in self.summaries.items()
            }
        for name in sorted(counters):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {_format_number(counters[name])}")
        for name in sorted(summaries):
            summary = summaries[name]
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{name}_count {summary.count}")
            lines.append(f"{name}_sum {_format_number(summary.total)}")
        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()


class JSONLogFormatter(logging.Formatter):
    """Formatter for deployments that want JSON at the handler boundary."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_for_log(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            payload["request_id"] = request_id
        for field_name in ("job_id", "project_id", "run_id", "error_class"):
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = redact_for_log(value)
        if record.exc_info:
            payload["exception"] = redact_for_log(
                "".join(traceback.format_exception(*record.exc_info))
            )
        return json.dumps(payload, sort_keys=True)


def configure_json_logging(level: int = logging.INFO) -> None:
    formatter = JSONLogFormatter()
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        return
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


def set_request_id(request_id: str | None) -> None:
    _request_id_ctx.set(request_id)


def current_request_id() -> str | None:
    return _request_id_ctx.get()


def redact_for_log(value: Any) -> Any:
    return _sanitize(value)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    if "request_id" not in payload and current_request_id():
        payload["request_id"] = current_request_id()
    logger.log(level, json.dumps(_sanitize(payload), sort_keys=True))


def classify_error(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, ValueError):
        return "validation_error"
    return "internal_error"


def record_pipeline_run(*, succeeded: bool) -> None:
    metrics.increment("pipeline_runs_total")
    if not succeeded:
        metrics.increment("pipeline_run_failures_total")


@contextmanager
def pipeline_step_timer(
    step_name: str,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        metrics.observe("pipeline_step_duration_seconds", duration)
        log_event(
            logging.getLogger("molecule_ranker.pipeline"),
            "pipeline_step_completed",
            step_name=step_name,
            project_id=project_id,
            run_id=run_id,
            duration_seconds=duration,
        )


def record_api_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
    request_id: str | None,
) -> None:
    metrics.observe("api_request_duration_seconds", duration_seconds)
    log_event(
        logging.getLogger("molecule_ranker.api"),
        "api_request",
        request_id=request_id,
        method=method,
        path=path,
        status_code=status_code,
        duration_seconds=duration_seconds,
    )


def configure_opentelemetry(*, service_name: str = "molecule-ranker") -> bool:
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
    except Exception:
        return False
    trace.set_tracer_provider(
        TracerProvider(resource=Resource.create({"service.name": service_name}))
    )
    return True


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = getattr(request.state, "request_id", None)
        if not request_id:
            request_id = request.headers.get("X-Request-ID")
        set_request_id(str(request_id) if request_id else None)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            log_event(
                logging.getLogger("molecule_ranker.api"),
                "api_request_failed",
                level=logging.ERROR,
                request_id=current_request_id(),
                method=request.method,
                path=request.url.path,
                error_class=classify_error(exc),
                error_summary=str(exc),
            )
            raise
        finally:
            record_api_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=time.perf_counter() - start,
                request_id=current_request_id(),
            )
            set_request_id(None)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = _sanitize(item)
        return sanitized
    if isinstance(value, list | tuple | set):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        redacted = _redact_log_text(value)
        if len(redacted.encode("utf-8")) > MAX_LOG_STRING_BYTES:
            return redacted[:MAX_LOG_STRING_BYTES] + "...[TRUNCATED]"
        return redacted
    if isinstance(value, Path):
        return redact_secrets(str(value))
    return value


def _redact_log_text(value: str) -> str:
    for pattern, replacement in LOG_SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    redacted = redact_secrets(value)
    for pattern, replacement in LOG_SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


__all__ = [
    "JSONLogFormatter",
    "MetricsRegistry",
    "ObservabilityMiddleware",
    "classify_error",
    "configure_json_logging",
    "configure_opentelemetry",
    "current_request_id",
    "log_event",
    "metrics",
    "pipeline_step_timer",
    "record_api_request",
    "record_pipeline_run",
    "redact_for_log",
    "set_request_id",
]
