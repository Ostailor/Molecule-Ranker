from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AuthProvider = Literal["local_password", "oidc", "oauth", "service_account"]
PlatformRole = Literal["platform_admin", "user"]
OrganizationRole = Literal["owner", "admin", "member"]
MembershipRole = Literal["owner", "admin", "scientist", "reviewer", "viewer", "service_account"]
PrincipalType = Literal["user", "team", "org"]
ProjectRole = Literal["project_owner", "editor", "reviewer", "viewer", "runner"]
PlatformJobType = Literal[
    "ranking",
    "generation",
    "developability",
    "literature",
    "experiment_import",
    "integration_sync",
    "connector_health_check",
    "webhook_processing",
    "warehouse_export",
    "registry_mapping_review",
    "external_export",
    "active_learning",
    "model_training",
    "model_validation",
    "model_prediction",
    "model_dataset_build",
    "model_train",
    "model_evaluate",
    "model_predict",
    "model_calibrate",
    "review_export",
    "dashboard_build",
    "codex_task",
    "project_dashboard",
    "artifact_export",
    "design_plan",
    "design_generate",
    "design_score",
    "design_loop",
    "design_benchmark",
]
PlatformJobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "partial",
    "cancelled",
    "guardrail_failed",
]
JobPriority = Literal["low", "normal", "high"]
CommentObjectType = Literal["project", "run", "candidate"]
AssignmentObjectType = Literal["project", "run", "candidate", "review_item"]
AssignmentStatus = Literal["open", "in_progress", "completed", "cancelled"]


class PlatformSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value


class UserAccount(PlatformSchema):
    user_id: str
    email: str
    display_name: str | None = None
    is_active: bool = True
    is_admin: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_login_at: datetime | None = None
    auth_provider: str = "local_password"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "status" in normalized and "is_active" not in normalized:
            normalized["is_active"] = normalized["status"] == "active"
        roles = normalized.pop("roles", None)
        if roles is not None and "is_admin" not in normalized:
            normalized["is_admin"] = "platform_admin" in roles
        normalized.setdefault("updated_at", normalized.get("created_at") or datetime.now(UTC))
        normalized.setdefault("auth_provider", "local_password")
        normalized.setdefault("metadata", {})
        return normalized

    @field_validator("user_id")
    @classmethod
    def require_user_id(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("email must contain @")
        return normalized

    @property
    def status(self) -> str:
        return "active" if self.is_active else "disabled"

    @property
    def roles(self) -> list[str]:
        return ["platform_admin", "user"] if self.is_admin else ["user"]


class Organization(PlatformSchema):
    org_id: str
    name: str
    slug: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def fill_slug_and_updated_at(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized.setdefault(
            "slug",
            _slug(str(normalized.get("name") or normalized.get("org_id"))),
        )
        normalized.setdefault("updated_at", normalized.get("created_at") or datetime.now(UTC))
        return normalized


class Team(PlatformSchema):
    team_id: str
    org_id: str
    name: str
    slug: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def fill_slug_and_updated_at(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized.setdefault(
            "slug",
            _slug(str(normalized.get("name") or normalized.get("team_id"))),
        )
        normalized.setdefault("updated_at", normalized.get("created_at") or datetime.now(UTC))
        return normalized


class Membership(PlatformSchema):
    membership_id: str
    user_id: str
    org_id: str
    team_id: str | None = None
    role: MembershipRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectPermission(PlatformSchema):
    permission_id: str
    project_id: str
    principal_type: PrincipalType
    principal_id: str
    role: ProjectRole
    granted_by: str
    granted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        user_id = normalized.pop("user_id", None)
        team_id = normalized.pop("team_id", None)
        org_id = normalized.pop("org_id", None)
        if "principal_type" not in normalized or "principal_id" not in normalized:
            if user_id is not None:
                normalized["principal_type"] = "user"
                normalized["principal_id"] = user_id
            elif team_id is not None:
                normalized["principal_type"] = "team"
                normalized["principal_id"] = team_id
            elif org_id is not None:
                normalized["principal_type"] = "org"
                normalized["principal_id"] = org_id
        if normalized.get("role") == "owner":
            normalized["role"] = "project_owner"
        normalized.setdefault(
            "permission_id",
            "-".join(
                [
                    "perm",
                    str(normalized.get("project_id", "")),
                    str(normalized.get("principal_type", "")),
                    str(normalized.get("principal_id", "")),
                ]
            ),
        )
        normalized.setdefault("granted_by", "system")
        normalized.setdefault("granted_at", normalized.pop("created_at", datetime.now(UTC)))
        normalized.setdefault("metadata", {})
        return normalized

    @property
    def user_id(self) -> str | None:
        return self.principal_id if self.principal_type == "user" else None

    @property
    def team_id(self) -> str | None:
        return self.principal_id if self.principal_type == "team" else None

    @property
    def org_id(self) -> str | None:
        return self.principal_id if self.principal_type == "org" else None

    @property
    def created_at(self) -> datetime:
        return self.granted_at


class PlatformAuditEvent(PlatformSchema):
    event_id: str
    actor_user_id: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    event_type: str
    object_type: str
    object_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ip_address: str | None = None
    user_agent: str | None = None
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized.setdefault("object_type", normalized.get("event_type", "platform"))
        normalized.setdefault(
            "object_id",
            normalized.get("project_id") or normalized.get("org_id") or normalized.get("event_id"),
        )
        normalized.setdefault("timestamp", normalized.pop("created_at", datetime.now(UTC)))
        normalized.setdefault("before", None)
        normalized.setdefault("after", None)
        return normalized

    @property
    def created_at(self) -> datetime:
        return self.timestamp


class PlatformJob(PlatformSchema):
    job_id: str
    org_id: str
    project_id: str | None = None
    requested_by_user_id: str
    job_type: PlatformJobType
    status: PlatformJobStatus = "queued"
    priority: JobPriority = "normal"
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_artifact_ids: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexWorkerJob(PlatformSchema):
    codex_job_id: str
    platform_job_id: str
    org_id: str
    project_id: str | None = None
    requested_by_user_id: str
    task_type: str
    codex_task_id: str
    status: str
    allowed_artifact_ids: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    forbidden_commands: list[str] = Field(default_factory=list)
    transcript_artifact_id: str | None = None
    guardrail_status: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectComment(PlatformSchema):
    comment_id: str
    org_id: str = "default"
    project_id: str
    object_type: CommentObjectType = "project"
    object_id: str
    author_user_id: str
    body: str
    run_id: str | None = None
    candidate_id: str | None = None
    mentions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Assignment(PlatformSchema):
    assignment_id: str
    org_id: str = "default"
    project_id: str
    object_type: AssignmentObjectType = "review_item"
    object_id: str
    assigned_to_user_id: str
    assigned_by_user_id: str
    status: AssignmentStatus = "open"
    run_id: str | None = None
    candidate_id: str | None = None
    due_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Notification(PlatformSchema):
    notification_id: str
    org_id: str = "default"
    recipient_user_id: str
    actor_user_id: str | None = None
    project_id: str | None = None
    event_type: str
    title: str
    body: str
    target_type: str
    target_id: str
    is_read: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActivityFeedItem(PlatformSchema):
    activity_id: str
    org_id: str = "default"
    project_id: str | None = None
    actor_user_id: str | None = None
    activity_type: str
    object_type: str
    object_id: str
    summary: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobRecord(PlatformSchema):
    """Compatibility record for the initial V1.0 SQLite job queue."""

    job_id: str
    job_type: str
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "partial",
        "cancelled",
        "guardrail_failed",
    ] = "pending"
    project_id: str | None = None
    requested_by_user_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str = ""
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None


AuditEvent = PlatformAuditEvent


class RetentionPolicy(PlatformSchema):
    scope_type: Literal["platform", "organization", "project", "user"] = "platform"
    scope_id: str = "platform"
    retention_days: int | None = Field(default=None, ge=1)
    export_enabled: bool = True
    delete_enabled: bool = True
    legal_hold: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be empty")
    return value


def _slug(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    parts = [part for part in normalized.split("-") if part]
    return "-".join(parts) or "platform"
