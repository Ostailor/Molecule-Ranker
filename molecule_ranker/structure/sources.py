from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator


class StructureSourceHealthStatus(BaseModel):
    source: str
    ok: bool
    status: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_seconds: float | None = Field(default=None, ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("checked_at")
    @classmethod
    def require_timezone_aware_checked_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value


class StructureSourceAdapter(Protocol):
    source_name: str

    def health_check(self) -> StructureSourceHealthStatus:
        ...


def check_structure_source_health(
    adapters: Iterable[StructureSourceAdapter],
) -> list[StructureSourceHealthStatus]:
    return [adapter.health_check() for adapter in adapters]


def timed_health_check(
    *,
    source: str,
    check: Any,
) -> StructureSourceHealthStatus:
    started = time.perf_counter()
    try:
        metadata = check()
    except Exception as exc:
        return StructureSourceHealthStatus(
            source=source,
            ok=False,
            status="unavailable",
            latency_seconds=time.perf_counter() - started,
            warnings=[str(exc)],
        )
    return StructureSourceHealthStatus(
        source=source,
        ok=True,
        status="available",
        latency_seconds=time.perf_counter() - started,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def write_raw_metadata_artifact(
    *,
    raw_artifact_dir: Path | None,
    source: str,
    external_id: str,
    payload: Any,
) -> str | None:
    if raw_artifact_dir is None:
        return None
    raw_artifact_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    safe_source = _safe_name(source)
    safe_external_id = _safe_name(external_id)
    path = raw_artifact_dir / f"{safe_source}-{safe_external_id}-{digest}.json"
    path.write_text(
        json.dumps(
            {
                "source": source,
                "external_id": external_id,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    return str(path)


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )


__all__ = [
    "StructureSourceAdapter",
    "StructureSourceHealthStatus",
    "check_structure_source_health",
    "timed_health_check",
    "write_raw_metadata_artifact",
]
