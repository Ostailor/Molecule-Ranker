from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ENGINEERING_CODEX_PROFILE = "engineering"
ENGINEERING_FAILURE_CATEGORIES = {
    "test_failure",
    "lint_failure",
    "typecheck_failure",
    "schema_contract_failure",
    "docs_check_failure",
    "unknown",
}
ENGINEERING_ACTION_TYPES = {
    "inspect_failure",
    "run_regression_command",
    "run_lint",
    "run_typecheck",
    "run_schema_check",
    "run_docs_check",
    "propose_patch",
    "apply_patch",
}
ENGINEERING_SIDE_EFFECT_LEVELS = {"none", "file_read", "code_write"}
ENGINEERING_EXECUTION_STATUSES = {
    "dry_run",
    "succeeded",
    "failed",
    "rejected",
    "partially_succeeded",
}


class EngineeringFailure(BaseModel):
    failure_id: str = Field(default_factory=lambda: f"eng-failure-{uuid4().hex[:12]}")
    category: str
    summary: str
    file_path: str | None = None
    test_name: str | None = None
    line: int | None = None
    error_type: str | None = None
    excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in ENGINEERING_FAILURE_CATEGORIES:
            raise ValueError(f"unsupported engineering failure category: {value}")
        return value


class EngineeringFailureReport(BaseModel):
    failure_report_id: str = Field(default_factory=lambda: f"eng-failure-report-{uuid4().hex[:12]}")
    source_type: str = "test_output"
    source_path: str | None = None
    summary: str
    failures: list[EngineeringFailure] = Field(default_factory=list)
    redaction_warnings: list[str] = Field(default_factory=list)
    codex_profile: str = ENGINEERING_CODEX_PROFILE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("codex_profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        if value != ENGINEERING_CODEX_PROFILE:
            raise ValueError("engineering repair must use the Codex engineering profile")
        return value


class EngineeringRepairAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"eng-repair-action-{uuid4().hex[:12]}")
    action_type: str
    summary: str
    command: list[str] | None = None
    target_files: list[str] = Field(default_factory=list)
    side_effect_level: str = "none"
    requires_apply: bool = False
    codex_profile: str = ENGINEERING_CODEX_PROFILE
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, value: str) -> str:
        if value not in ENGINEERING_ACTION_TYPES:
            raise ValueError(f"unsupported engineering repair action type: {value}")
        return value

    @field_validator("side_effect_level")
    @classmethod
    def validate_side_effect(cls, value: str) -> str:
        if value not in ENGINEERING_SIDE_EFFECT_LEVELS:
            raise ValueError(f"unsupported engineering side effect level: {value}")
        return value

    @field_validator("codex_profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        if value != ENGINEERING_CODEX_PROFILE:
            raise ValueError("engineering repair must use the Codex engineering profile")
        return value


class EngineeringRepairPlan(BaseModel):
    repair_plan_id: str = Field(default_factory=lambda: f"eng-repair-plan-{uuid4().hex[:12]}")
    failure_report_id: str
    summary: str
    actions: list[EngineeringRepairAction] = Field(default_factory=list)
    regression_commands: list[list[str]] = Field(default_factory=list)
    dry_run_by_default: bool = True
    requires_apply: bool = False
    codex_profile: str = ENGINEERING_CODEX_PROFILE
    forbidden_commands: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("codex_profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        if value != ENGINEERING_CODEX_PROFILE:
            raise ValueError("engineering repair must use the Codex engineering profile")
        return value

    @model_validator(mode="after")
    def derive_apply_requirement(self) -> EngineeringRepairPlan:
        if any(action.requires_apply for action in self.actions):
            self.requires_apply = True
        return self


class EngineeringCommandResult(BaseModel):
    command: list[str]
    status: str
    returncode: int | None = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    duration_seconds: float = 0.0
    rejection_reason: str | None = None


class EngineeringRepairExecutionReport(BaseModel):
    execution_id: str = Field(default_factory=lambda: f"eng-repair-exec-{uuid4().hex[:12]}")
    repair_plan_id: str
    status: str
    dry_run: bool = True
    applied: bool = False
    command_results: list[EngineeringCommandResult] = Field(default_factory=list)
    rejected_actions: list[str] = Field(default_factory=list)
    regression_commands: list[list[str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ENGINEERING_EXECUTION_STATUSES:
            raise ValueError(f"unsupported engineering execution status: {value}")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value
