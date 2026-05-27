from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EngineeringTaskType = Literal[
    "implementation_planning",
    "bug_fix_planning",
    "test_failure_analysis",
    "patch_proposal",
    "docs_update_proposal",
    "migration_planning",
    "benchmark_failure_analysis",
]


class CodexEngineeringTask(BaseModel):
    task_id: str
    task_type: EngineeringTaskType
    goal: str
    working_directory: Path = Path(".")
    input_paths: list[Path] = Field(default_factory=list)
    log_text: str | None = None
    apply: bool = False
    allow_git_push: bool = False
    allow_deletions: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "goal")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value
