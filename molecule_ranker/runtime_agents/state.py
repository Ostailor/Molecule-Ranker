from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RuntimeMemoryType = Literal[
    "session",
    "project",
    "user_preference",
    "task_outcome",
    "failure_pattern",
    "workflow_template",
]


class RuntimeMemoryPolicyError(ValueError):
    """Raised when runtime memory would violate scientific or retention policy."""


class RuntimeStateModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class RuntimeMemoryRecord(RuntimeStateModel):
    memory_id: str
    memory_type: RuntimeMemoryType
    session_id: str | None = None
    project_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    summary: str
    content: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeMemoryState(RuntimeStateModel):
    records: list[RuntimeMemoryRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def load_memory_state(path: Path) -> RuntimeMemoryState:
    if not path.exists():
        return RuntimeMemoryState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeMemoryState.model_validate(payload)


def save_memory_state(path: Path, state: RuntimeMemoryState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


__all__ = [
    "RuntimeMemoryPolicyError",
    "RuntimeMemoryRecord",
    "RuntimeMemoryState",
    "RuntimeMemoryType",
    "load_memory_state",
    "save_memory_state",
]
