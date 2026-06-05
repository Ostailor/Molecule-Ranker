from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.schemas import (
    CoPilotAction,
    CoPilotActionResult,
    CoPilotMemoryRecord,
    CoPilotTrigger,
)

_SECRET_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "credential",
)
_RAW_ASSAY_KEYS = (
    "raw_assay_data",
    "raw_assay",
    "raw_results",
    "assay_values",
    "measurements",
    "result_value",
    "concentration",
)
_UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+active\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+safe\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+effective\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+synthesizable\b", re.IGNORECASE),
    re.compile(r"\btherapeutic\b", re.IGNORECASE),
    re.compile(r"\bbinding\b", re.IGNORECASE),
)


class CoPilotMemory:
    def __init__(
        self,
        *,
        seen_event_ids: set[str] | None = None,
        action_history: list[CoPilotAction] | None = None,
        records: list[CoPilotMemoryRecord] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.seen_event_ids = seen_event_ids or set()
        self.action_history = action_history or []
        self.records = records or []
        self._now = now or (lambda: datetime.now(UTC))

    def mark_seen(self, event_ids: list[str]) -> None:
        self.seen_event_ids.update(event_ids)

    def remember_actions(self, actions: list[CoPilotAction]) -> None:
        self.action_history.extend(actions)

    def compute_trigger_signature(self, trigger: CoPilotTrigger | dict[str, Any]) -> str:
        if isinstance(trigger, CoPilotTrigger):
            if trigger.trigger_signature:
                return trigger.trigger_signature
            detector_type = trigger.metadata.get("detector_event_type", trigger.trigger_type)
            source_type = trigger.metadata.get("source_object_type", "unknown_source")
            return (
                f"{trigger.campaign_id}:{trigger.trigger_type}:"
                f"{detector_type}:{source_type}:{trigger.priority}"
            )
        metadata = trigger.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        explicit = trigger.get("trigger_signature")
        if explicit:
            return str(explicit)
        detector_type = metadata.get("detector_event_type", trigger.get("trigger_type"))
        source_type = metadata.get("source_object_type", "unknown_source")
        return (
            f"{trigger.get('campaign_id')}:{trigger.get('trigger_type')}:"
            f"{detector_type}:{source_type}:{trigger.get('priority')}"
        )

    def retrieve_similar_trigger_memories(
        self,
        trigger_or_signature: CoPilotTrigger | dict[str, Any] | str,
        *,
        campaign_id: str | None = None,
        limit: int = 5,
    ) -> list[CoPilotMemoryRecord]:
        signature = (
            trigger_or_signature
            if isinstance(trigger_or_signature, str)
            else self.compute_trigger_signature(trigger_or_signature)
        )
        matches = [
            record
            for record in self.records
            if record.trigger_signature == signature
            and (campaign_id is None or record.campaign_id == campaign_id)
        ]
        return sorted(
            matches,
            key=lambda record: (record.success_rate, record.occurrence_count),
            reverse=True,
        )[:limit]

    def record_action_outcome(
        self,
        *,
        trigger: CoPilotTrigger,
        action: CoPilotAction,
        result: CoPilotActionResult,
        human_feedback: str | None = None,
        time_to_resolution_seconds: int | float | None = None,
        campaign_context: dict[str, Any] | None = None,
        repeated_blocker: bool = False,
    ) -> CoPilotMemoryRecord:
        secret_values = self._collect_secret_values(
            trigger.metadata,
            action.tool_args,
            action.metadata,
            result.metadata,
            campaign_context or {},
        )
        signature = self.compute_trigger_signature(trigger)
        existing = self._find_record(signature, action.action_type, trigger.campaign_id)
        success = result.status == "succeeded"
        metadata = self._memory_metadata(
            action=action,
            result=result,
            human_feedback=human_feedback,
            time_to_resolution_seconds=time_to_resolution_seconds,
            campaign_context=campaign_context or {},
            repeated_blocker=repeated_blocker,
            success=success,
            existing=existing,
            secret_values=secret_values,
        )
        record = CoPilotMemoryRecord(
            memory_id=existing.memory_id if existing is not None else self._memory_id(
                signature,
                action.action_type,
            ),
            campaign_id=trigger.campaign_id,
            trigger_signature=signature,
            recommended_action_type=action.action_type,
            success_rate=self.update_success_rate(existing=existing, success=success),
            occurrence_count=metadata["occurrence_count"],
            last_seen_at=self._now(),
            notes=self._sanitize_string(
                human_feedback or "Operational action outcome recorded.",
                secret_values=secret_values,
            ),
            metadata=metadata,
        )
        if existing is None:
            self.records.append(record)
        else:
            self.records[self.records.index(existing)] = record
        return record

    def update_success_rate(
        self,
        *,
        existing: CoPilotMemoryRecord | None,
        success: bool,
    ) -> float:
        if existing is None:
            return 1.0 if success else 0.0
        success_count = self._int_metadata(existing.metadata, "success_count")
        occurrence_count = existing.occurrence_count
        updated_success_count = success_count + (1 if success else 0)
        updated_occurrence_count = occurrence_count + 1
        return round(updated_success_count / updated_occurrence_count, 4)

    def export_memory(self) -> dict[str, Any]:
        return {
            "records": [
                self._sanitize_value(record.model_dump(mode="json"), secret_values=set())
                for record in self.records
            ],
            "policy": {
                "evidence_role": "operational_memory_only",
                "stores_scientific_evidence": False,
            },
        }

    def delete_memory(self, memory_id: str) -> bool:
        original_count = len(self.records)
        self.records = [record for record in self.records if record.memory_id != memory_id]
        return len(self.records) != original_count

    def _memory_metadata(
        self,
        *,
        action: CoPilotAction,
        result: CoPilotActionResult,
        human_feedback: str | None,
        time_to_resolution_seconds: int | float | None,
        campaign_context: dict[str, Any],
        repeated_blocker: bool,
        success: bool,
        existing: CoPilotMemoryRecord | None,
        secret_values: set[str],
    ) -> dict[str, Any]:
        previous_success_count = (
            self._int_metadata(existing.metadata, "success_count")
            if existing is not None
            else 0
        )
        previous_failure_count = (
            self._int_metadata(existing.metadata, "failure_count")
            if existing is not None
            else 0
        )
        previous_occurrence_count = existing.occurrence_count if existing is not None else 0
        return {
            "selected_action_type": action.action_type,
            "outcome_status": result.status,
            "successful": success,
            "success_count": previous_success_count + (1 if success else 0),
            "failure_count": previous_failure_count + (0 if success else 1),
            "occurrence_count": previous_occurrence_count + 1,
            "human_feedback": self._sanitize_string(
                human_feedback or "",
                secret_values=secret_values,
            ),
            "time_to_resolution_seconds": time_to_resolution_seconds,
            "repeated_blocker": repeated_blocker,
            "campaign_context": self._sanitize_value(
                campaign_context,
                secret_values=secret_values,
            ),
            "result_summary": self._sanitize_string(
                result.summary,
                secret_values=secret_values,
            ),
            "recommendation_kind": "action_type_only",
            "is_scientific_evidence": False,
            "evidence_role": "operational_memory_only",
        }

    def _find_record(
        self,
        signature: str,
        action_type: str,
        campaign_id: str | None,
    ) -> CoPilotMemoryRecord | None:
        for record in self.records:
            if (
                record.trigger_signature == signature
                and record.recommended_action_type == action_type
                and record.campaign_id == campaign_id
            ):
                return record
        return None

    def _memory_id(self, signature: str, action_type: str) -> str:
        safe_signature = re.sub(r"[^a-zA-Z0-9_.-]+", "-", signature).strip("-")
        return f"memory-{safe_signature}-{action_type}"

    def _sanitize_value(self, value: Any, *, secret_values: set[str]) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if self._is_secret_key(key_text):
                    sanitized[key_text] = "[REDACTED]"
                elif self._is_raw_assay_key(key_text):
                    sanitized[key_text] = "[OMITTED_RAW_ASSAY_DATA]"
                else:
                    sanitized[key_text] = self._sanitize_value(
                        item,
                        secret_values=secret_values,
                    )
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_value(item, secret_values=secret_values) for item in value]
        if isinstance(value, str):
            return self._sanitize_string(value, secret_values=secret_values)
        return value

    def _sanitize_string(self, value: str, *, secret_values: set[str]) -> str:
        sanitized = value
        for secret in sorted(secret_values, key=len, reverse=True):
            if secret:
                sanitized = sanitized.replace(secret, "[REDACTED]")
        for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
            sanitized = pattern.sub("[UNSUPPORTED_CLAIM_REDACTED]", sanitized)
        return sanitized

    def _collect_secret_values(self, *values: Any) -> set[str]:
        secrets: set[str] = set()
        for value in values:
            self._collect_secret_values_into(value, secrets)
        return secrets

    def _collect_secret_values_into(self, value: Any, secrets: set[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if self._is_secret_key(str(key)):
                    if isinstance(item, str):
                        secrets.add(item)
                else:
                    self._collect_secret_values_into(item, secrets)
        elif isinstance(value, list):
            for item in value:
                self._collect_secret_values_into(item, secrets)

    def _is_secret_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)

    def _is_raw_assay_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(fragment in normalized for fragment in _RAW_ASSAY_KEYS)

    def _int_metadata(self, metadata: dict[str, Any], key: str) -> int:
        value = metadata.get(key, 0)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0
