from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AdapterHealthStatus(BaseModel):
    """Health probe result for a public biomedical data adapter."""

    source_name: str
    ok: bool
    endpoint: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
