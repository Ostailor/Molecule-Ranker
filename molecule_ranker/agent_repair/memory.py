from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import RepairMemoryRecord

SECRET_FIELD_NAMES = {
    "access_key",
    "api_key",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
}
SCIENTIFIC_MEMORY_KEYS = {
    "assay_result",
    "assay_results",
    "assayresult",
    "biological_evidence",
    "citation",
    "citations",
    "codex_transcript",
    "codex_transcripts",
    "docking_score",
    "docking_scores",
    "evidence_item",
    "evidence_items",
    "evidenceitem",
    "full_codex_transcript",
    "graph_fact",
    "graph_facts",
    "raw_assay_data",
    "raw_evidence",
    "score",
    "scores",
    "scientific_score",
    "scientific_scores",
}
SIGNATURE_KEYS = (
    "tool_name",
    "job_type",
    "failure_category",
    "error_code",
    "artifact_type",
    "schema_version",
    "config_keys",
    "policy_context",
    "guardrail_category",
)


class RepairMemory:
    """Operational memory for recurring repair failures and strategies."""

    def __init__(self, records: list[RepairMemoryRecord] | None = None) -> None:
        self.records: dict[str, RepairMemoryRecord] = {
            record.failure_signature: record for record in records or []
        }

    def compute_failure_signature(self, **kwargs: Any) -> str:
        return compute_failure_signature(**kwargs)

    def get_repair_recommendations(
        self,
        signature: str,
        *,
        limit: int = 5,
    ) -> list[RepairMemoryRecord]:
        exact = self.records.get(signature)
        if exact is not None:
            return [exact]
        category = _signature_component(signature, "failure_category")
        matches = [
            record
            for record in self.records.values()
            if category
            and _signature_component(record.failure_signature, "failure_category") == category
        ]
        matches.sort(
            key=lambda record: (
                record.repair_success_rate,
                record.occurrence_count,
                record.last_seen_at,
            ),
            reverse=True,
        )
        return matches[:limit]

    def record_repair_outcome(
        self,
        *,
        signature: str,
        failure_category: str,
        repair_plan_id: str | None,
        succeeded: bool,
        recommended_repair_strategy: str,
        metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> RepairMemoryRecord:
        now = occurred_at or datetime.now(UTC)
        existing = self.records.get(signature)
        sanitized_metadata = _sanitize_memory_payload(metadata or {})
        if existing is None:
            record = RepairMemoryRecord(
                memory_id=f"repair-memory-{uuid4().hex[:12]}",
                failure_signature=signature,
                failure_category=failure_category,  # type: ignore[arg-type]
                successful_repair_plan_id=repair_plan_id if succeeded else None,
                repair_success_rate=1.0 if succeeded else 0.0,
                last_seen_at=now,
                occurrence_count=1,
                recommended_repair_strategy=recommended_repair_strategy,
                warnings=_memory_warnings(metadata or {}),
                metadata=sanitized_metadata,
            )
            self.records[signature] = record
            return record

        prior_occurrences = existing.occurrence_count
        prior_successes = existing.repair_success_rate * prior_occurrences
        successes = prior_successes + (1 if succeeded else 0)
        occurrence_count = prior_occurrences + 1
        success_rate = successes / occurrence_count
        record = existing.model_copy(
            update={
                "successful_repair_plan_id": repair_plan_id
                if succeeded
                else existing.successful_repair_plan_id,
                "repair_success_rate": round(success_rate, 4),
                "last_seen_at": now,
                "occurrence_count": occurrence_count,
                "recommended_repair_strategy": recommended_repair_strategy
                if succeeded
                else existing.recommended_repair_strategy,
                "warnings": sorted(set(existing.warnings + _memory_warnings(metadata or {}))),
                "metadata": {
                    **existing.metadata,
                    **sanitized_metadata,
                    "operational_memory_only": True,
                    "not_scientific_evidence": True,
                },
            }
        )
        self.records[signature] = record
        return record

    def decay_old_memory(
        self,
        *,
        now: datetime | None = None,
        half_life_days: int = 90,
        prune_below_success_rate: float | None = None,
    ) -> list[RepairMemoryRecord]:
        current = now or datetime.now(UTC)
        decayed: list[RepairMemoryRecord] = []
        for signature, record in list(self.records.items()):
            age_days = max((current - record.last_seen_at).days, 0)
            if age_days == 0:
                decayed.append(record)
                continue
            decay_factor = 0.5 ** (age_days / max(half_life_days, 1))
            updated_rate = round(record.repair_success_rate * decay_factor, 4)
            if (
                prune_below_success_rate is not None
                and updated_rate < prune_below_success_rate
            ):
                del self.records[signature]
                continue
            updated = record.model_copy(update={"repair_success_rate": updated_rate})
            self.records[signature] = updated
            decayed.append(updated)
        return decayed

    def export_repair_memory(self) -> dict[str, Any]:
        return {
            "schema": "repair_memory.v1",
            "exported_at": datetime.now(UTC).isoformat(),
            "records": [
                _sanitize_memory_payload(record.model_dump(mode="json"))
                for record in self.records.values()
            ],
            "metadata": {
                "operational_memory_only": True,
                "not_scientific_evidence": True,
            },
        }

    def import_repair_memory(self, payload: Mapping[str, Any]) -> list[RepairMemoryRecord]:
        records_payload = payload.get("records", [])
        imported: list[RepairMemoryRecord] = []
        if not isinstance(records_payload, list):
            return imported
        for item in records_payload:
            if not isinstance(item, Mapping):
                continue
            sanitized = _sanitize_memory_payload(item)
            record = RepairMemoryRecord.model_validate(sanitized)
            self.records[record.failure_signature] = record
            imported.append(record)
        return imported


_DEFAULT_MEMORY = RepairMemory()


def compute_failure_signature(
    *,
    tool_name: str | None = None,
    job_type: str | None = None,
    failure_category: str,
    error_code: str | None = None,
    artifact_type: str | None = None,
    schema_version: str | None = None,
    relevant_config_keys: list[str] | tuple[str, ...] | set[str] | None = None,
    policy_context: Mapping[str, Any] | str | None = None,
    guardrail_category: str | None = None,
    **kwargs: Any,
) -> str:
    config_keys = sorted(str(key) for key in relevant_config_keys or [])
    payload = {
        "tool_name": _normalize(tool_name or kwargs.get("tool")),
        "job_type": _normalize(job_type),
        "failure_category": _normalize(failure_category),
        "error_code": _normalize(error_code),
        "artifact_type": _normalize(artifact_type),
        "schema_version": _normalize(schema_version),
        "config_keys": ",".join(config_keys),
        "policy_context": _normalize_policy(policy_context),
        "guardrail_category": _normalize(guardrail_category),
    }
    readable = "|".join(f"{key}={payload[key]}" for key in SIGNATURE_KEYS)
    digest = hashlib.sha256(readable.encode("utf-8")).hexdigest()[:16]
    return f"repair-signature:{digest}:{readable}"


def get_repair_recommendations(
    signature: str,
    *,
    memory: RepairMemory | None = None,
    limit: int = 5,
) -> list[RepairMemoryRecord]:
    return (memory or _DEFAULT_MEMORY).get_repair_recommendations(signature, limit=limit)


def record_repair_outcome(
    *,
    signature: str,
    failure_category: str,
    repair_plan_id: str | None,
    succeeded: bool,
    recommended_repair_strategy: str,
    metadata: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
    memory: RepairMemory | None = None,
) -> RepairMemoryRecord:
    return (memory or _DEFAULT_MEMORY).record_repair_outcome(
        signature=signature,
        failure_category=failure_category,
        repair_plan_id=repair_plan_id,
        succeeded=succeeded,
        recommended_repair_strategy=recommended_repair_strategy,
        metadata=metadata,
        occurred_at=occurred_at,
    )


def decay_old_memory(
    *,
    memory: RepairMemory | None = None,
    now: datetime | None = None,
    half_life_days: int = 90,
    prune_below_success_rate: float | None = None,
) -> list[RepairMemoryRecord]:
    return (memory or _DEFAULT_MEMORY).decay_old_memory(
        now=now,
        half_life_days=half_life_days,
        prune_below_success_rate=prune_below_success_rate,
    )


def export_repair_memory(memory: RepairMemory | None = None) -> dict[str, Any]:
    return (memory or _DEFAULT_MEMORY).export_repair_memory()


def import_repair_memory(
    payload: Mapping[str, Any],
    *,
    memory: RepairMemory | None = None,
) -> list[RepairMemoryRecord]:
    return (memory or _DEFAULT_MEMORY).import_repair_memory(payload)


def _sanitize_memory_payload(value: Any, *, allow_redacted_transcript: bool = False) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = _normalize_key(key)
            if normalized_key in SECRET_FIELD_NAMES:
                sanitized[str(key)] = "[REDACTED]"
                continue
            if normalized_key in SCIENTIFIC_MEMORY_KEYS:
                if normalized_key in {"codex_transcript", "codex_transcripts"} and (
                    allow_redacted_transcript
                    or value.get("policy_allows_redacted_transcript") is True
                ):
                    sanitized[str(key)] = "[REDACTED_TRANSCRIPT]"
                continue
            sanitized[str(key)] = _sanitize_memory_payload(
                item,
                allow_redacted_transcript=allow_redacted_transcript,
            )
        sanitized.setdefault("operational_memory_only", True)
        sanitized.setdefault("not_scientific_evidence", True)
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_memory_payload(
                item,
                allow_redacted_transcript=allow_redacted_transcript,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def _memory_warnings(metadata: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if _contains_key(metadata, SECRET_FIELD_NAMES):
        warnings.append("Secrets were redacted from repair memory.")
    if _contains_key(metadata, SCIENTIFIC_MEMORY_KEYS):
        warnings.append("Scientific or transcript payload was not stored in repair memory.")
    return warnings


def _contains_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            _normalize_key(key) in keys or _contains_key(item, keys)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(item, keys) for item in value)
    return False


def _redact_secret_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", redacted)
    return redacted


def _signature_component(signature: str, key: str) -> str | None:
    for part in signature.split("|"):
        if part.startswith(f"{key}="):
            return part.split("=", 1)[1]
    return None


def _normalize(value: Any) -> str:
    if value is None:
        return "none"
    text = str(value).strip().lower()
    text = re.sub(r"\s+", "_", text)
    return text or "none"


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _normalize_policy(policy_context: Mapping[str, Any] | str | None) -> str:
    if policy_context is None:
        return "none"
    if isinstance(policy_context, str):
        return _normalize(policy_context)
    sanitized = _sanitize_memory_payload(policy_context)
    parts = [
        f"{_normalize_key(key)}:{_normalize(value)}"
        for key, value in sorted(sanitized.items(), key=lambda item: str(item[0]))
        if key not in {"operational_memory_only", "not_scientific_evidence"}
    ]
    return ",".join(parts) or "none"


def stale_timestamp(days_old: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_old)


__all__ = [
    "RepairMemory",
    "RepairMemoryRecord",
    "compute_failure_signature",
    "decay_old_memory",
    "export_repair_memory",
    "get_repair_recommendations",
    "import_repair_memory",
    "record_repair_outcome",
    "stale_timestamp",
]
