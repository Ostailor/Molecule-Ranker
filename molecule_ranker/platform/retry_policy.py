from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

EXTERNAL_WRITE_JOB_TYPES = {
    "external_export",
    "warehouse_export",
    "integration_sync",
    "webhook_processing",
}
IMPORT_JOB_TYPES = {"experiment_import"}
SYNC_JOB_TYPES = {"integration_sync", "warehouse_export", "external_export"}
CODEX_JOB_TYPES = {"codex_task"}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    retry_transient: bool = True

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.base_delay_seconds * (2 ** max(attempt - 1, 0))
        return min(delay, self.max_delay_seconds)


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    reason: str
    policy: RetryPolicy
    delay_seconds: float = 0.0
    dead_letter: bool = False


def retry_policy_for_job(
    job_type: str,
    config_snapshot: dict[str, Any],
    metadata: dict[str, Any],
) -> RetryPolicy:
    max_attempts = _positive_int(config_snapshot.get("max_attempts"), default=3)
    base_delay = _nonnegative_float(
        config_snapshot.get("retry_backoff_seconds"),
        default=1.0,
    )
    max_delay = _nonnegative_float(config_snapshot.get("max_retry_backoff_seconds"), default=60.0)
    if job_type in EXTERNAL_WRITE_JOB_TYPES and not is_explicitly_idempotent(
        job_type,
        config_snapshot,
        metadata,
    ):
        max_attempts = 1
    return RetryPolicy(
        max_attempts=max_attempts,
        base_delay_seconds=base_delay,
        max_delay_seconds=max_delay,
    )


def retry_decision(
    *,
    job_type: str,
    config_snapshot: dict[str, Any],
    metadata: dict[str, Any],
    attempts: int,
    exc: Exception,
) -> RetryDecision:
    policy = retry_policy_for_job(job_type, config_snapshot, metadata)
    if not _is_transient_exception(exc):
        return RetryDecision(False, "non_transient_failure", policy)
    if job_type in EXTERNAL_WRITE_JOB_TYPES and not is_explicitly_idempotent(
        job_type,
        config_snapshot,
        metadata,
    ):
        return RetryDecision(False, "external_write_not_idempotent", policy)
    if job_type in CODEX_JOB_TYPES and not codex_context_unchanged(config_snapshot, metadata):
        return RetryDecision(False, "codex_context_changed", policy)
    if job_type in IMPORT_JOB_TYPES and not import_has_duplicate_guard(config_snapshot, metadata):
        return RetryDecision(False, "import_duplicate_guard_missing", policy)
    if job_type in SYNC_JOB_TYPES and not sync_has_duplicate_write_guard(config_snapshot, metadata):
        return RetryDecision(False, "sync_duplicate_write_guard_missing", policy)
    if attempts >= policy.max_attempts:
        return RetryDecision(
            False,
            "max_attempts_exceeded",
            policy,
            dead_letter=True,
        )
    next_attempt = attempts
    return RetryDecision(
        True,
        "transient_failure",
        policy,
        delay_seconds=policy.delay_for_attempt(next_attempt),
    )


def is_explicitly_idempotent(
    job_type: str,
    config_snapshot: dict[str, Any],
    metadata: dict[str, Any],
) -> bool:
    if job_type in IMPORT_JOB_TYPES:
        return import_has_duplicate_guard(config_snapshot, metadata)
    if job_type in SYNC_JOB_TYPES:
        return sync_has_duplicate_write_guard(config_snapshot, metadata)
    return bool(config_snapshot.get("idempotent") or metadata.get("idempotent")) and bool(
        idempotency_key(config_snapshot, metadata)
    )


def idempotency_key(config_snapshot: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    value = config_snapshot.get("idempotency_key") or metadata.get("idempotency_key")
    if value:
        return str(value)
    if metadata.get("auto_idempotency_key"):
        return str(metadata["auto_idempotency_key"])
    return None


def build_auto_idempotency_key(job_type: str, config_snapshot: dict[str, Any]) -> str:
    normalized = json.dumps(config_snapshot, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{job_type}:{normalized}".encode()).hexdigest()[:24]
    return f"{job_type}:{digest}"


def codex_context_hash(config_snapshot: dict[str, Any]) -> str:
    context = {
        "prompt": config_snapshot.get("prompt"),
        "prompt_hash": config_snapshot.get("prompt_hash"),
        "artifact_ids": config_snapshot.get("artifact_ids"),
        "artifact_hashes": config_snapshot.get("artifact_hashes"),
        "input_artifact_paths": config_snapshot.get("input_artifact_paths"),
    }
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def codex_context_unchanged(config_snapshot: dict[str, Any], metadata: dict[str, Any]) -> bool:
    previous = metadata.get("codex_context_hash")
    current = codex_context_hash(config_snapshot)
    return previous is None or str(previous) == current


def import_has_duplicate_guard(config_snapshot: dict[str, Any], metadata: dict[str, Any]) -> bool:
    return bool(
        config_snapshot.get("dedupe_key")
        or config_snapshot.get("idempotency_key")
        or metadata.get("dedupe_key")
        or metadata.get("idempotency_key")
    )


def sync_has_duplicate_write_guard(
    config_snapshot: dict[str, Any],
    metadata: dict[str, Any],
) -> bool:
    if not (config_snapshot.get("external_write") or config_snapshot.get("allow_writes")):
        return True
    return bool(
        config_snapshot.get("external_write_idempotency_key")
        or config_snapshot.get("idempotency_key")
        or metadata.get("external_write_idempotency_key")
        or (metadata.get("idempotent") and metadata.get("idempotency_key"))
    )


def _is_transient_exception(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    lowered = str(exc).lower()
    return any(token in lowered for token in ("timeout", "temporary", "transient", "retryable"))


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


__all__ = [
    "RetryDecision",
    "RetryPolicy",
    "build_auto_idempotency_key",
    "codex_context_hash",
    "idempotency_key",
    "retry_decision",
    "retry_policy_for_job",
]
