from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PilotCheckStatus = Literal["pass", "warn", "fail"]
PilotEnvironment = Literal["development", "test", "staging", "production"]


class PilotReadinessReport(BaseModel):
    report_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: str
    environment: str
    checks: list[dict[str, Any]]
    passed_count: int
    warning_count: int
    failed_count: int
    blockers: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


__all__ = ["PilotCheckStatus", "PilotEnvironment", "PilotReadinessReport"]
