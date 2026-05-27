from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    inspect,
    or_,
    select,
    update,
)
from sqlalchemy.engine import make_url

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    ExternalIdMapping,
    ExternalRecordEnvelope,
    IntegrationCredentialCreate,
    IntegrationCredentialRef,
    SyncJobRecord,
)
from molecule_ranker.platform.auth import (
    AuthError,
    PasswordHasher,
    hash_token,
    verify_token_hash,
)
from molecule_ranker.platform.schemas import (
    ActivityFeedItem,
    Assignment,
    AuditEvent,
    JobRecord,
    Notification,
    Organization,
    PrincipalType,
    ProjectComment,
    ProjectPermission,
    RetentionPolicy,
    Team,
    UserAccount,
)
from molecule_ranker.utils import slugify

SCHEMA_VERSION = "2026_05_27_0001_platform_core"

metadata = MetaData()

schema_migrations = Table(
    "platform_schema_migrations",
    metadata,
    Column("version", String(128), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(128), primary_key=True),
    Column("email", String(320), nullable=False, unique=True, index=True),
    Column("display_name", String(255), nullable=True),
    Column("password_hash", String(512), nullable=True),
    Column("password_salt", String(255), nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("auth_provider", String(64), nullable=False, default="local_password"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

organizations = Table(
    "organizations",
    metadata,
    Column("org_id", String(128), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("slug", String(255), nullable=False, unique=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

teams = Table(
    "teams",
    metadata,
    Column("team_id", String(128), primary_key=True),
    Column("org_id", String(128), ForeignKey("organizations.org_id"), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("slug", String(255), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    UniqueConstraint("org_id", "slug", name="uq_teams_org_slug"),
)

memberships = Table(
    "memberships",
    metadata,
    Column("membership_id", String(128), primary_key=True),
    Column("user_id", String(128), ForeignKey("users.user_id"), nullable=False, index=True),
    Column(
        "org_id",
        String(128),
        ForeignKey("organizations.org_id"),
        nullable=False,
        index=True,
    ),
    Column("team_id", String(128), ForeignKey("teams.team_id"), nullable=True, index=True),
    Column("role", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

project_permissions = Table(
    "project_permissions",
    metadata,
    Column("permission_id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False, index=True),
    Column("principal_type", String(32), nullable=False),
    Column("principal_id", String(128), nullable=False, index=True),
    Column("role", String(64), nullable=False),
    Column("granted_by", String(128), ForeignKey("users.user_id"), nullable=False),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    UniqueConstraint(
        "project_id",
        "principal_type",
        "principal_id",
        name="uq_project_permission_principal",
    ),
)

platform_audit_events = Table(
    "platform_audit_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("actor_user_id", String(128), nullable=True, index=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("event_type", String(255), nullable=False, index=True),
    Column("object_type", String(255), nullable=False),
    Column("object_id", String(255), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False, index=True),
    Column("ip_address", String(128), nullable=True),
    Column("user_agent", Text, nullable=True),
    Column("summary", Text, nullable=False),
    Column("before_json", JSON, nullable=True),
    Column("after_json", JSON, nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

platform_jobs = Table(
    "platform_jobs",
    metadata,
    Column("job_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("requested_by_user_id", String(128), nullable=False, index=True),
    Column("job_type", String(128), nullable=False, index=True),
    Column("status", String(32), nullable=False, index=True),
    Column("priority", String(32), nullable=False, default="normal"),
    Column("config_snapshot_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("result_artifact_ids_json", JSON, nullable=False, default=list),
    Column("error_summary", Text, nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("result_json", JSON, nullable=True),
)

codex_worker_jobs = Table(
    "codex_worker_jobs",
    metadata,
    Column("codex_job_id", String(128), primary_key=True),
    Column("platform_job_id", String(128), nullable=False, index=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("requested_by_user_id", String(128), nullable=False),
    Column("task_type", String(128), nullable=False),
    Column("codex_task_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("allowed_artifact_ids_json", JSON, nullable=False, default=list),
    Column("allowed_commands_json", JSON, nullable=False, default=list),
    Column("forbidden_commands_json", JSON, nullable=False, default=list),
    Column("transcript_artifact_id", String(128), nullable=True),
    Column("guardrail_status", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

artifact_records = Table(
    "artifact_records",
    metadata,
    Column("artifact_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("run_id", String(128), nullable=True, index=True),
    Column("artifact_type", String(128), nullable=False),
    Column("path", Text, nullable=False),
    Column("sha256", String(128), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("provenance_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

project_workspaces = Table(
    "project_workspaces",
    metadata,
    Column("project_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("name", String(255), nullable=False),
    Column("root_dir", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

project_runs = Table(
    "project_runs",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False, index=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("run_dir", Text, nullable=True),
    Column("disease_name", Text, nullable=True),
    Column("candidate_count", Integer, nullable=False, default=0),
    Column("generated_candidate_count", Integer, nullable=False, default=0),
    Column("target_count", Integer, nullable=False, default=0),
    Column("summary_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

review_workspaces = Table(
    "review_workspaces",
    metadata,
    Column("review_workspace_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("run_id", String(128), nullable=True, index=True),
    Column("status", String(64), nullable=False, default="active"),
    Column("payload_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

assay_results = Table(
    "assay_results",
    metadata,
    Column("assay_result_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("candidate_id", String(128), nullable=True, index=True),
    Column("source_file_artifact_id", String(128), nullable=True),
    Column("payload_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

active_learning_batches = Table(
    "active_learning_batches",
    metadata,
    Column("batch_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("requested_by_user_id", String(128), nullable=True),
    Column("payload_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("session_id", String(128), primary_key=True),
    Column("user_id", String(128), ForeignKey("users.user_id"), nullable=False, index=True),
    Column("refresh_token_hash", String(512), nullable=False),
    Column("refresh_token_salt", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

service_account_tokens = Table(
    "service_account_tokens",
    metadata,
    Column("token_id", String(128), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("token_hash", String(512), nullable=False),
    Column("token_salt", String(255), nullable=False),
    Column("user_id", String(128), ForeignKey("users.user_id"), nullable=False, index=True),
    Column("created_by_user_id", String(128), nullable=False),
    Column("scopes_json", JSON, nullable=False, default=list),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

project_comments = Table(
    "project_comments",
    metadata,
    Column("comment_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=False, index=True),
    Column("object_type", String(64), nullable=False, index=True),
    Column("object_id", String(255), nullable=False, index=True),
    Column("author_user_id", String(128), ForeignKey("users.user_id"), nullable=False, index=True),
    Column("body", Text, nullable=False),
    Column("run_id", String(128), nullable=True, index=True),
    Column("candidate_id", String(255), nullable=True, index=True),
    Column("mentions_json", JSON, nullable=False, default=list),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

assignments = Table(
    "assignments",
    metadata,
    Column("assignment_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=False, index=True),
    Column("object_type", String(64), nullable=False, index=True),
    Column("object_id", String(255), nullable=False, index=True),
    Column("assigned_to_user_id", String(128), ForeignKey("users.user_id"), nullable=False),
    Column("assigned_by_user_id", String(128), ForeignKey("users.user_id"), nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("run_id", String(128), nullable=True, index=True),
    Column("candidate_id", String(255), nullable=True, index=True),
    Column("due_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

notifications = Table(
    "notifications",
    metadata,
    Column("notification_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("recipient_user_id", String(128), ForeignKey("users.user_id"), nullable=False),
    Column("actor_user_id", String(128), nullable=True, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("event_type", String(128), nullable=False, index=True),
    Column("title", String(255), nullable=False),
    Column("body", Text, nullable=False),
    Column("target_type", String(128), nullable=False),
    Column("target_id", String(128), nullable=False),
    Column("is_read", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

activity_feed = Table(
    "activity_feed",
    metadata,
    Column("activity_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("actor_user_id", String(128), nullable=True, index=True),
    Column("activity_type", String(128), nullable=False, index=True),
    Column("object_type", String(128), nullable=False),
    Column("object_id", String(128), nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

integration_connectors = Table(
    "integration_connectors",
    metadata,
    Column("connector_id", String(128), primary_key=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("name", String(255), nullable=False),
    Column("provider", String(64), nullable=False, index=True),
    Column("kind", String(64), nullable=False, index=True),
    Column("mode", String(64), nullable=False, index=True),
    Column("direction", String(64), nullable=False),
    Column("base_url", Text, nullable=True),
    Column("credential_ref_json", JSON, nullable=True),
    Column("config_json", JSON, nullable=False, default=dict),
    Column("allow_writes", Boolean, nullable=False, default=False),
    Column("explicit_write_permission", Boolean, nullable=False, default=False),
    Column("sandbox", Boolean, nullable=False, default=True),
    Column("created_by_user_id", String(128), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

integration_credentials = Table(
    "integration_credentials",
    metadata,
    Column("credential_id", String(128), primary_key=True),
    Column("connector_id", String(128), nullable=True, index=True),
    Column("name", String(255), nullable=False),
    Column("backend", String(64), nullable=False),
    Column("key_ref", Text, nullable=True),
    Column("secret_hash", String(512), nullable=True),
    Column("secret_salt", String(255), nullable=True),
    Column("created_by_user_id", String(128), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

integration_sync_jobs = Table(
    "integration_sync_jobs",
    metadata,
    Column("sync_job_id", String(128), primary_key=True),
    Column("connector_id", String(128), nullable=False, index=True),
    Column("org_id", String(128), nullable=False, index=True),
    Column("project_id", String(128), nullable=True, index=True),
    Column("direction", String(64), nullable=False),
    Column("mode", String(64), nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("rows_seen", Integer, nullable=False, default=0),
    Column("rows_valid", Integer, nullable=False, default=0),
    Column("rows_rejected", Integer, nullable=False, default=0),
    Column("contract_report_json", JSON, nullable=True),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

integration_sync_audit_logs = Table(
    "integration_sync_audit_logs",
    metadata,
    Column("sync_audit_id", String(128), primary_key=True),
    Column("sync_job_id", String(128), nullable=False, index=True),
    Column("connector_id", String(128), nullable=False, index=True),
    Column("event_type", String(128), nullable=False, index=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("summary", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)

external_id_mappings = Table(
    "external_id_mappings",
    metadata,
    Column("mapping_id", String(128), primary_key=True),
    Column("connector_id", String(128), nullable=False, index=True),
    Column("internal_id", String(255), nullable=False, index=True),
    Column("external_id", String(255), nullable=False, index=True),
    Column("source_system", String(255), nullable=False, index=True),
    Column("source_record_id", String(255), nullable=False, index=True),
    Column("mapping_method", String(64), nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("confidence", Integer, nullable=False, default=1000),
    Column("validation_evidence_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

integration_provenance_records = Table(
    "integration_provenance_records",
    metadata,
    Column("provenance_record_id", String(128), primary_key=True),
    Column("sync_job_id", String(128), nullable=False, index=True),
    Column("connector_id", String(128), nullable=False, index=True),
    Column("record_type", String(128), nullable=False, index=True),
    Column("source_system", String(255), nullable=False, index=True),
    Column("source_record_id", String(255), nullable=False, index=True),
    Column("source_updated_at", DateTime(timezone=True), nullable=True),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    Column("payload_json", JSON, nullable=False, default=dict),
    Column("raw_metadata_json", JSON, nullable=False, default=dict),
)

REQUIRED_TABLES = {
    table.name
    for table in [
        users,
        organizations,
        teams,
        memberships,
        project_permissions,
        platform_audit_events,
        platform_jobs,
        codex_worker_jobs,
        artifact_records,
        project_workspaces,
        project_runs,
        review_workspaces,
        assay_results,
        active_learning_batches,
        auth_sessions,
        service_account_tokens,
        project_comments,
        assignments,
        notifications,
        activity_feed,
        integration_connectors,
        integration_credentials,
        integration_sync_jobs,
        integration_sync_audit_logs,
        external_id_mappings,
        integration_provenance_records,
    ]
}


class PlatformDatabaseError(ValueError):
    """Raised when the hosted platform database cannot satisfy an operation."""


class PlatformDatabase:
    """Central V0.9 platform metadata database.

    SQLite is the local/dev default. PostgreSQL URLs are supported for hosted
    deployments through SQLAlchemy and psycopg.
    """

    def __init__(
        self,
        root_dir: Path,
        *,
        database_url: str | None = None,
        db_path: Path | None = None,
        initialize: bool = True,
    ) -> None:
        self.root_dir = root_dir.resolve()
        self.database_url = self._resolve_database_url(database_url=database_url, db_path=db_path)
        self.engine = create_engine(self.database_url, future=True)
        self.auth_secret = ""
        self.audit_permission_denials = False
        if initialize:
            self.initialize()

    @property
    def database_kind(self) -> str:
        return self.engine.dialect.name

    def initialize(self) -> None:
        if self.database_kind == "sqlite":
            Path(make_url(self.database_url).database or "").parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        metadata.create_all(self.engine)
        self._record_migration(SCHEMA_VERSION)

    def migrate(self) -> list[str]:
        self.initialize()
        return self.applied_migrations()

    def check(self) -> dict[str, Any]:
        inspector = inspect(self.engine)
        existing = set(inspector.get_table_names())
        missing = sorted(REQUIRED_TABLES - existing)
        if not missing:
            with self.engine.connect() as connection:
                connection.execute(select(func.count()).select_from(users)).scalar_one()
        return {
            "ok": not missing,
            "database": self.database_kind,
            "database_url": self.safe_database_url,
            "missing_tables": missing,
            "applied_migrations": self.applied_migrations(),
        }

    @property
    def safe_database_url(self) -> str:
        return str(make_url(self.database_url).render_as_string(hide_password=True))

    def applied_migrations(self) -> list[str]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(schema_migrations.c.version)).fetchall()
        return sorted(str(row.version) for row in rows)

    def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
        roles: list[str] | None = None,
    ) -> UserAccount:
        salt, password_hash = PasswordHasher().hash_password(password)
        now = _now()
        is_admin = "platform_admin" in set(roles or [])
        user = UserAccount(
            user_id=f"user-{uuid.uuid4().hex[:16]}",
            email=email,
            display_name=display_name or email.split("@", 1)[0],
            is_active=True,
            is_admin=is_admin,
            created_at=now,
            updated_at=now,
            auth_provider="local_password",
            metadata={},
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(users).values(
                    user_id=user.user_id,
                    email=user.email,
                    display_name=user.display_name,
                    password_hash=password_hash,
                    password_salt=salt,
                    is_active=True,
                    is_admin=is_admin,
                    auth_provider=user.auth_provider,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                    last_login_at=None,
                    metadata_json={},
                )
            )
        self.write_audit(
            "user_created",
            actor_user_id=None,
            summary=f"Created user {user.email}.",
            object_type="user",
            object_id=user.user_id,
            metadata={"user_id": user.user_id, "is_admin": user.is_admin},
        )
        return user

    def create_auth_session(
        self,
        *,
        user_id: str,
        refresh_token: str,
        expires_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        session_id = f"sess-{uuid.uuid4().hex[:16]}"
        salt, token_hash = hash_token(refresh_token)
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                insert(auth_sessions).values(
                    session_id=session_id,
                    user_id=user_id,
                    refresh_token_hash=token_hash,
                    refresh_token_salt=salt,
                    created_at=now,
                    expires_at=expires_at,
                    revoked_at=None,
                    last_used_at=None,
                    metadata_json=_redact_json(metadata or {}),
                )
            )
        return session_id

    def refresh_auth_session(self, *, refresh_token: str) -> tuple[UserAccount, str]:
        now = _now()
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(auth_sessions).where(
                        (auth_sessions.c.revoked_at.is_(None))
                        & (auth_sessions.c.expires_at > now)
                    )
                )
                .mappings()
                .fetchall()
            )
            for row in rows:
                if verify_token_hash(
                    refresh_token,
                    salt=str(row["refresh_token_salt"]),
                    expected_hash=str(row["refresh_token_hash"]),
                ):
                    connection.execute(
                        update(auth_sessions)
                        .where(auth_sessions.c.session_id == row["session_id"])
                        .values(last_used_at=now)
                    )
                    user = self.get_user(str(row["user_id"]))
                    if user is None or not user.is_active:
                        raise AuthError("User is not active.")
                    return user, str(row["session_id"])
        raise AuthError("Invalid refresh token.")

    def revoke_auth_session(self, *, session_id: str, actor_user_id: str | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(auth_sessions)
                .where(auth_sessions.c.session_id == session_id)
                .values(revoked_at=_now())
            )
        self.write_audit(
            "auth_session_revoked",
            actor_user_id=actor_user_id,
            summary=f"Revoked session {session_id}.",
            object_type="auth_session",
            object_id=session_id,
            metadata={},
        )

    def auth_session_active(self, session_id: str) -> bool:
        now = _now()
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(auth_sessions.c.session_id).where(
                        (auth_sessions.c.session_id == session_id)
                        & (auth_sessions.c.revoked_at.is_(None))
                        & (auth_sessions.c.expires_at > now)
                    )
                )
                .mappings()
                .first()
            )
        return row is not None

    def create_service_account_token(
        self,
        *,
        name: str,
        token: str,
        user_id: str,
        created_by_user_id: str,
        scopes: list[str],
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        token_id = f"sat-{uuid.uuid4().hex[:16]}"
        salt, token_hash = hash_token(token)
        with self.engine.begin() as connection:
            connection.execute(
                insert(service_account_tokens).values(
                    token_id=token_id,
                    name=name,
                    token_hash=token_hash,
                    token_salt=salt,
                    user_id=user_id,
                    created_by_user_id=created_by_user_id,
                    scopes_json=scopes,
                    created_at=_now(),
                    expires_at=expires_at,
                    revoked_at=None,
                    last_used_at=None,
                    metadata_json=_redact_json(metadata or {}),
                )
            )
        self.write_audit(
            "service_account_token_created",
            actor_user_id=created_by_user_id,
            summary=f"Created service account token {token_id}.",
            object_type="service_account_token",
            object_id=token_id,
            metadata={"token_id": token_id, "scopes": scopes},
        )
        return token_id

    def authenticate_service_account_token(self, token: str) -> UserAccount | None:
        now = _now()
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(service_account_tokens).where(
                        (service_account_tokens.c.revoked_at.is_(None))
                        & (
                            (service_account_tokens.c.expires_at.is_(None))
                            | (service_account_tokens.c.expires_at > now)
                        )
                    )
                )
                .mappings()
                .fetchall()
            )
            for row in rows:
                if verify_token_hash(
                    token,
                    salt=str(row["token_salt"]),
                    expected_hash=str(row["token_hash"]),
                ):
                    connection.execute(
                        update(service_account_tokens)
                        .where(service_account_tokens.c.token_id == row["token_id"])
                        .values(last_used_at=now)
                    )
                    user = self.get_user(str(row["user_id"]))
                    if user is None or not user.is_active:
                        return None
                    user.auth_provider = "service_account"
                    user.metadata = {
                        **user.metadata,
                        "service_account_token_id": row["token_id"],
                        "scopes": list(row["scopes_json"] or []),
                    }
                    return user
        return None

    def revoke_service_account_token(
        self,
        *,
        token_id: str,
        actor_user_id: str,
    ) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(service_account_tokens)
                .where(service_account_tokens.c.token_id == token_id)
                .values(revoked_at=_now())
            )
        revoked = result.rowcount > 0
        if revoked:
            self.write_audit(
                "service_account_token_revoked",
                actor_user_id=actor_user_id,
                summary=f"Revoked service account token {token_id}.",
                object_type="service_account_token",
                object_id=token_id,
                metadata={"token_id": token_id},
            )
        return revoked

    def list_service_account_tokens(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        service_account_tokens.c.token_id,
                        service_account_tokens.c.name,
                        service_account_tokens.c.user_id,
                        service_account_tokens.c.created_by_user_id,
                        service_account_tokens.c.scopes_json,
                        service_account_tokens.c.created_at,
                        service_account_tokens.c.expires_at,
                        service_account_tokens.c.revoked_at,
                        service_account_tokens.c.last_used_at,
                        service_account_tokens.c.metadata_json,
                    ).order_by(service_account_tokens.c.created_at.desc())
                )
                .mappings()
                .fetchall()
            )
        return [
            {
                "token_id": str(row["token_id"]),
                "name": str(row["name"]),
                "user_id": str(row["user_id"]),
                "created_by_user_id": str(row["created_by_user_id"]),
                "scopes": list(row["scopes_json"] or []),
                "created_at": _aware(row["created_at"]).isoformat(),
                "expires_at": _aware(row["expires_at"]).isoformat()
                if row["expires_at"]
                else None,
                "revoked_at": _aware(row["revoked_at"]).isoformat()
                if row["revoked_at"]
                else None,
                "last_used_at": _aware(row["last_used_at"]).isoformat()
                if row["last_used_at"]
                else None,
                "metadata": _redact_json(dict(row["metadata_json"] or {})),
            }
            for row in rows
        ]

    def authenticate_user(self, *, email: str, password: str) -> UserAccount:
        normalized = email.strip().lower()
        with self.engine.begin() as connection:
            row = (
                connection.execute(select(users).where(users.c.email == normalized))
                .mappings()
                .first()
            )
            if row is None or not bool(row["is_active"]):
                raise AuthError("Invalid email or password.")
            salt = row["password_salt"]
            password_hash = row["password_hash"]
            if not salt or not password_hash:
                raise AuthError("Invalid email or password.")
            if not PasswordHasher().verify(
                password,
                salt=str(salt),
                expected_hash=str(password_hash),
            ):
                raise AuthError("Invalid email or password.")
            now = _now()
            connection.execute(
                update(users)
                .where(users.c.user_id == row["user_id"])
                .values(last_login_at=now, updated_at=now)
            )
        user = self.get_user(str(row["user_id"]))
        if user is None:
            raise AuthError("Invalid email or password.")
        self.write_audit(
            "user_login",
            actor_user_id=user.user_id,
            summary=f"User {user.email} logged in.",
            object_type="user",
            object_id=user.user_id,
            metadata={},
        )
        return user

    def get_user(self, user_id: str) -> UserAccount | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(users).where(users.c.user_id == user_id))
                .mappings()
                .first()
            )
        return _user(row) if row else None

    def list_users(self) -> list[UserAccount]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(users).order_by(users.c.email)).mappings().fetchall()
        return [_user(row) for row in rows]

    def disable_user(self, user_id: str, *, actor_user_id: str | None = None) -> None:
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                update(users)
                .where(users.c.user_id == user_id)
                .values(is_active=False, updated_at=now)
            )
        self.write_audit(
            "user_disabled",
            actor_user_id=actor_user_id,
            summary=f"Disabled user {user_id}.",
            object_type="user",
            object_id=user_id,
            metadata={"user_id": user_id},
        )

    def activate_user(self, user_id: str, *, actor_user_id: str | None = None) -> None:
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                update(users)
                .where(users.c.user_id == user_id)
                .values(is_active=True, updated_at=now)
            )
        self.write_audit(
            "user_activated",
            actor_user_id=actor_user_id,
            summary=f"Activated user {user_id}.",
            object_type="user",
            object_id=user_id,
            metadata={"user_id": user_id},
        )

    def reset_local_password(
        self,
        *,
        user_id: str,
        new_password: str,
        actor_user_id: str,
    ) -> None:
        salt, password_hash = PasswordHasher().hash_password(new_password)
        now = _now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(users)
                .where(users.c.user_id == user_id)
                .values(password_hash=password_hash, password_salt=salt, updated_at=now)
            )
        if result.rowcount != 1:
            raise PlatformDatabaseError("User not found.")
        self.write_audit(
            "user_password_reset",
            actor_user_id=actor_user_id,
            summary=f"Reset local password for user {user_id}.",
            object_type="user",
            object_id=user_id,
            metadata={"user_id": user_id},
        )

    def delete_user(self, user_id: str, *, actor_user_id: str | None = None) -> None:
        policy = self.get_retention_policy(scope_type="user", scope_id=user_id)
        if policy.legal_hold or not policy.delete_enabled:
            raise PlatformDatabaseError("User deletion is disabled by retention policy.")
        with self.engine.begin() as connection:
            connection.execute(users.delete().where(users.c.user_id == user_id))
        self.write_audit(
            "user_deleted",
            actor_user_id=actor_user_id,
            summary=f"Deleted user {user_id}.",
            object_type="user",
            object_id=user_id,
            metadata={"user_id": user_id},
        )

    def create_organization(
        self,
        *,
        name: str,
        created_by_user_id: str,
        org_id: str | None = None,
    ) -> Organization:
        now = _now()
        organization = Organization(
            org_id=org_id or f"org-{slugify(name)}",
            name=name,
            slug=slugify(name),
            created_at=now,
            updated_at=now,
            metadata={},
        )
        membership_id = f"membership-{uuid.uuid4().hex[:16]}"
        with self.engine.begin() as connection:
            connection.execute(
                insert(organizations).values(
                    org_id=organization.org_id,
                    name=organization.name,
                    slug=organization.slug,
                    created_at=organization.created_at,
                    updated_at=organization.updated_at,
                    metadata_json={},
                )
            )
            connection.execute(
                insert(memberships).values(
                    membership_id=membership_id,
                    user_id=created_by_user_id,
                    org_id=organization.org_id,
                    team_id=None,
                    role="owner",
                    created_at=now,
                    updated_at=now,
                    metadata_json={},
                )
            )
        self.write_audit(
            "organization_created",
            actor_user_id=created_by_user_id,
            org_id=organization.org_id,
            summary=f"Created organization {organization.name}.",
            object_type="organization",
            object_id=organization.org_id,
            metadata={},
        )
        return organization

    def list_organizations(self, *, user_id: str | None = None) -> list[Organization]:
        statement = select(organizations)
        if user_id is not None:
            statement = (
                statement.join(memberships)
                .where(memberships.c.user_id == user_id)
                .distinct()
            )
        statement = statement.order_by(organizations.c.name)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_organization(row) for row in rows]

    def create_team(
        self,
        *,
        org_id: str,
        name: str,
        created_by_user_id: str,
        team_id: str | None = None,
    ) -> Team:
        now = _now()
        team = Team(
            team_id=team_id or f"team-{slugify(name)}",
            org_id=org_id,
            name=name,
            slug=slugify(name),
            created_at=now,
            updated_at=now,
            metadata={},
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(teams).values(
                    team_id=team.team_id,
                    org_id=team.org_id,
                    name=team.name,
                    slug=team.slug,
                    created_at=team.created_at,
                    updated_at=team.updated_at,
                    metadata_json={},
                )
            )
            connection.execute(
                insert(memberships).values(
                    membership_id=f"membership-{uuid.uuid4().hex[:16]}",
                    user_id=created_by_user_id,
                    org_id=org_id,
                    team_id=team.team_id,
                    role="admin",
                    created_at=now,
                    updated_at=now,
                    metadata_json={},
                )
            )
        self.write_audit(
            "team_created",
            actor_user_id=created_by_user_id,
            org_id=org_id,
            summary=f"Created team {team.name}.",
            object_type="team",
            object_id=team.team_id,
            metadata={"team_id": team.team_id},
        )
        return team

    def add_membership(
        self,
        *,
        user_id: str,
        org_id: str,
        role: str,
        team_id: str | None = None,
        actor_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        membership_id = f"membership-{uuid.uuid4().hex[:16]}"
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                insert(memberships).values(
                    membership_id=membership_id,
                    user_id=user_id,
                    org_id=org_id,
                    team_id=team_id,
                    role=role,
                    created_at=now,
                    updated_at=now,
                    metadata_json=_redact_json(metadata or {}),
                )
            )
        self.write_audit(
            "membership_added",
            actor_user_id=actor_user_id,
            org_id=org_id,
            summary=f"Added membership for user {user_id}.",
            object_type="membership",
            object_id=membership_id,
            metadata={"user_id": user_id, "team_id": team_id, "role": role},
        )
        return membership_id

    def list_memberships(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(memberships)
        if org_id is not None:
            statement = statement.where(memberships.c.org_id == org_id)
        if user_id is not None:
            statement = statement.where(memberships.c.user_id == user_id)
        if team_id is not None:
            statement = statement.where(memberships.c.team_id == team_id)
        statement = statement.order_by(memberships.c.created_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_public_row(row) for row in rows]

    def remove_membership(self, membership_id: str, *, actor_user_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                memberships.delete().where(memberships.c.membership_id == membership_id)
            )
        removed = result.rowcount > 0
        if removed:
            self.write_audit(
                "membership_removed",
                actor_user_id=actor_user_id,
                summary=f"Removed membership {membership_id}.",
                object_type="membership",
                object_id=membership_id,
                metadata={"membership_id": membership_id},
            )
        return removed

    def grant_project_permission(
        self,
        *,
        project_id: str,
        role: str,
        actor_user_id: str,
        user_id: str | None = None,
        org_id: str | None = None,
        team_id: str | None = None,
    ) -> ProjectPermission:
        if sum(value is not None for value in [user_id, org_id, team_id]) != 1:
            raise PlatformDatabaseError("Grant exactly one user, organization, or team permission.")
        if role not in {"owner", "project_owner", "editor", "reviewer", "viewer", "runner"}:
            raise PlatformDatabaseError("Invalid project role.")
        principal_type, principal_id = _principal(user_id=user_id, org_id=org_id, team_id=team_id)
        permission = ProjectPermission(
            permission_id=f"perm-{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            principal_type=cast(PrincipalType, principal_type),
            principal_id=principal_id,
            role="project_owner" if role == "owner" else role,  # type: ignore[arg-type]
            granted_by=actor_user_id,
            granted_at=_now(),
            metadata={},
        )
        with self.engine.begin() as connection:
            connection.execute(
                project_permissions.delete().where(
                    (project_permissions.c.project_id == project_id)
                    & (project_permissions.c.principal_type == permission.principal_type)
                    & (project_permissions.c.principal_id == permission.principal_id)
                )
            )
            connection.execute(
                insert(project_permissions).values(
                    permission_id=permission.permission_id,
                    project_id=permission.project_id,
                    principal_type=permission.principal_type,
                    principal_id=permission.principal_id,
                    role=permission.role,
                    granted_by=permission.granted_by,
                    granted_at=permission.granted_at,
                    metadata_json={},
                )
            )
        self.write_audit(
            "project_permission_granted",
            actor_user_id=actor_user_id,
            project_id=project_id,
            summary=f"Granted {permission.role} access to project {project_id}.",
            object_type="project_permission",
            object_id=permission.permission_id,
            metadata={
                "principal_type": permission.principal_type,
                "principal_id": permission.principal_id,
                "role": permission.role,
            },
        )
        return permission

    def project_permissions(self, project_id: str) -> list[ProjectPermission]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(project_permissions)
                    .where(project_permissions.c.project_id == project_id)
                    .order_by(project_permissions.c.granted_at)
                )
                .mappings()
                .fetchall()
            )
        return [_permission(row) for row in rows]

    def add_project_comment(
        self,
        *,
        project_id: str,
        author_user_id: str,
        body: str,
        org_id: str = "default",
        object_type: str = "project",
        object_id: str | None = None,
        run_id: str | None = None,
        candidate_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectComment:
        from molecule_ranker.platform.rbac import has_permission

        author = self.get_user(author_user_id)
        if author is None or not author.is_active:
            raise PlatformDatabaseError("Comment author does not exist or is inactive.")
        if not has_permission(author, "review:write", project_id=project_id, database=self):
            raise PermissionError("Comment author lacks review:write permission.")
        cleaned_body = redact_secrets(body).strip()
        if not cleaned_body:
            raise PlatformDatabaseError("Comment body must not be empty.")
        comment_object_id = object_id or candidate_id or run_id or project_id
        mentions = self._mentioned_user_ids(cleaned_body)
        now = _now()
        comment = ProjectComment(
            comment_id=f"comment-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            project_id=project_id,
            object_type=object_type,  # type: ignore[arg-type]
            object_id=comment_object_id,
            author_user_id=author_user_id,
            body=cleaned_body,
            run_id=run_id,
            candidate_id=candidate_id,
            mentions=mentions,
            created_at=now,
            updated_at=now,
            metadata={
                "is_biomedical_evidence": False,
                "changes_scores": False,
                **_redact_json(metadata or {}),
            },
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(project_comments).values(
                    comment_id=comment.comment_id,
                    org_id=comment.org_id,
                    project_id=comment.project_id,
                    object_type=comment.object_type,
                    object_id=comment.object_id,
                    author_user_id=comment.author_user_id,
                    body=comment.body,
                    run_id=comment.run_id,
                    candidate_id=comment.candidate_id,
                    mentions_json=comment.mentions,
                    created_at=comment.created_at,
                    updated_at=comment.updated_at,
                    metadata_json=comment.metadata,
                )
            )
        summary = f"Commented on {comment.object_type} {comment.object_id}."
        self._insert_activity(
            org_id=org_id,
            project_id=project_id,
            actor_user_id=author_user_id,
            activity_type="comment_added",
            object_type="project_comment",
            object_id=comment.comment_id,
            summary=summary,
            metadata={
                "comment_object_type": comment.object_type,
                "comment_object_id": comment.object_id,
            },
        )
        self.write_audit(
            "project_comment_added",
            actor_user_id=author_user_id,
            org_id=org_id,
            project_id=project_id,
            summary=summary,
            object_type="project_comment",
            object_id=comment.comment_id,
            metadata={
                "comment_id": comment.comment_id,
                "object_type": comment.object_type,
                "object_id": comment.object_id,
                "mentions": mentions,
                "is_biomedical_evidence": False,
            },
        )
        for recipient_user_id in mentions:
            if recipient_user_id == author_user_id:
                continue
            self._insert_notification(
                org_id=org_id,
                recipient_user_id=recipient_user_id,
                actor_user_id=author_user_id,
                project_id=project_id,
                event_type="mention",
                title="You were mentioned in a project comment",
                body=f"{author.display_name or author.email} mentioned you: {cleaned_body[:160]}",
                target_type="project_comment",
                target_id=comment.comment_id,
                metadata={"comment_id": comment.comment_id},
            )
        return comment

    def list_project_comments(
        self,
        *,
        project_id: str,
        object_type: str | None = None,
        object_id: str | None = None,
        run_id: str | None = None,
        candidate_id: str | None = None,
        limit: int = 100,
    ) -> list[ProjectComment]:
        statement = select(project_comments).where(project_comments.c.project_id == project_id)
        if object_type is not None:
            statement = statement.where(project_comments.c.object_type == object_type)
        if object_id is not None:
            statement = statement.where(project_comments.c.object_id == object_id)
        if run_id is not None:
            statement = statement.where(project_comments.c.run_id == run_id)
        if candidate_id is not None:
            statement = statement.where(project_comments.c.candidate_id == candidate_id)
        statement = statement.order_by(project_comments.c.created_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_project_comment(row) for row in rows]

    def create_assignment(
        self,
        *,
        project_id: str,
        assigned_to_user_id: str,
        assigned_by_user_id: str,
        object_type: str = "review_item",
        object_id: str,
        org_id: str = "default",
        run_id: str | None = None,
        candidate_id: str | None = None,
        due_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Assignment:
        from molecule_ranker.platform.rbac import has_permission

        actor = self.get_user(assigned_by_user_id)
        assignee = self.get_user(assigned_to_user_id)
        if actor is None or not actor.is_active:
            raise PlatformDatabaseError("Assigning user does not exist or is inactive.")
        if assignee is None or not assignee.is_active:
            raise PlatformDatabaseError("Assigned user does not exist or is inactive.")
        if not has_permission(actor, "review:write", project_id=project_id, database=self):
            raise PermissionError("Assignment requires review:write permission.")
        now = _now()
        assignment = Assignment(
            assignment_id=f"assign-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            project_id=project_id,
            object_type=object_type,  # type: ignore[arg-type]
            object_id=object_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_by_user_id=assigned_by_user_id,
            status="open",
            run_id=run_id,
            candidate_id=candidate_id,
            due_at=due_at,
            created_at=now,
            updated_at=now,
            metadata={
                "grants_permissions": False,
                **_redact_json(metadata or {}),
            },
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(assignments).values(
                    assignment_id=assignment.assignment_id,
                    org_id=assignment.org_id,
                    project_id=assignment.project_id,
                    object_type=assignment.object_type,
                    object_id=assignment.object_id,
                    assigned_to_user_id=assignment.assigned_to_user_id,
                    assigned_by_user_id=assignment.assigned_by_user_id,
                    status=assignment.status,
                    run_id=assignment.run_id,
                    candidate_id=assignment.candidate_id,
                    due_at=assignment.due_at,
                    created_at=assignment.created_at,
                    updated_at=assignment.updated_at,
                    metadata_json=assignment.metadata,
                )
            )
        summary = f"Assigned {assignment.object_type} {assignment.object_id}."
        self._insert_activity(
            org_id=org_id,
            project_id=project_id,
            actor_user_id=assigned_by_user_id,
            activity_type="assignment_created",
            object_type="assignment",
            object_id=assignment.assignment_id,
            summary=summary,
            metadata={"assigned_to_user_id": assigned_to_user_id, "grants_permissions": False},
        )
        self._insert_notification(
            org_id=org_id,
            recipient_user_id=assigned_to_user_id,
            actor_user_id=assigned_by_user_id,
            project_id=project_id,
            event_type="assignment",
            title="Review item assigned",
            body=f"{actor.display_name or actor.email} assigned {object_type} {object_id}.",
            target_type="assignment",
            target_id=assignment.assignment_id,
            metadata={"assignment_id": assignment.assignment_id, "grants_permissions": False},
        )
        self.write_audit(
            "assignment_created",
            actor_user_id=assigned_by_user_id,
            org_id=org_id,
            project_id=project_id,
            summary=summary,
            object_type="assignment",
            object_id=assignment.assignment_id,
            metadata={
                "assignment_id": assignment.assignment_id,
                "assigned_to_user_id": assigned_to_user_id,
                "grants_permissions": False,
            },
        )
        return assignment

    def list_assignments(
        self,
        *,
        project_id: str | None = None,
        assigned_to_user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Assignment]:
        statement = select(assignments)
        if project_id is not None:
            statement = statement.where(assignments.c.project_id == project_id)
        if assigned_to_user_id is not None:
            statement = statement.where(assignments.c.assigned_to_user_id == assigned_to_user_id)
        if status is not None:
            statement = statement.where(assignments.c.status == status)
        statement = statement.order_by(assignments.c.created_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_assignment(row) for row in rows]

    def list_notifications(
        self,
        *,
        user_id: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[Notification]:
        statement = select(notifications).where(notifications.c.recipient_user_id == user_id)
        if unread_only:
            statement = statement.where(notifications.c.is_read.is_(False))
        statement = statement.order_by(notifications.c.created_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_notification(row) for row in rows]

    def list_activity(
        self,
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[ActivityFeedItem]:
        statement = select(activity_feed)
        if project_id is not None:
            statement = statement.where(activity_feed.c.project_id == project_id)
        statement = statement.order_by(activity_feed.c.created_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_activity(row) for row in rows]

    def effective_project_role(self, *, user_id: str, project_id: str) -> str | None:
        user = self.get_user(user_id)
        if user and user.is_admin:
            return "project_owner"
        team_ids: list[str] = []
        org_ids: list[str] = []
        with self.engine.connect() as connection:
            membership_rows = (
                connection.execute(select(memberships).where(memberships.c.user_id == user_id))
                .mappings()
                .fetchall()
            )
            for row in membership_rows:
                org_ids.append(str(row["org_id"]))
                if row["team_id"]:
                    team_ids.append(str(row["team_id"]))
            clauses = [
                (project_permissions.c.principal_type == "user")
                & (project_permissions.c.principal_id == user_id)
            ]
            if org_ids:
                clauses.append(
                    (project_permissions.c.principal_type == "org")
                    & project_permissions.c.principal_id.in_(org_ids)
                )
            if team_ids:
                clauses.append(
                    (project_permissions.c.principal_type == "team")
                    & project_permissions.c.principal_id.in_(team_ids)
                )
            rows = (
                connection.execute(
                    select(project_permissions.c.role).where(
                        (project_permissions.c.project_id == project_id) & or_(*clauses)
                    )
                )
                .mappings()
                .fetchall()
            )
        rank = {"viewer": 1, "reviewer": 2, "runner": 2, "editor": 3, "project_owner": 4}
        return max(
            (str(row["role"]) for row in rows),
            key=lambda role: rank.get(role, 0),
            default=None,
        )

    def enqueue_job(
        self,
        *,
        job_type: str,
        requested_by_user_id: str,
        project_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = _now()
        payload = _redact_json(payload or {})
        job = JobRecord(
            job_id=f"job-{uuid.uuid4().hex[:16]}",
            job_type=job_type,
            project_id=project_id,
            requested_by_user_id=requested_by_user_id,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(platform_jobs).values(
                    job_id=job.job_id,
                    org_id=str(payload.get("org_id") or "default"),
                    project_id=project_id,
                    requested_by_user_id=requested_by_user_id,
                    job_type=job_type,
                    status="queued",
                    priority=str(payload.get("priority") or "normal"),
                    config_snapshot_json=payload,
                    created_at=now,
                    started_at=None,
                    completed_at=None,
                    result_artifact_ids_json=[],
                    error_summary=None,
                    metadata_json={},
                    updated_at=now,
                    attempts=0,
                    result_json=None,
                )
            )
        self.write_audit(
            "job_enqueued",
            actor_user_id=requested_by_user_id,
            project_id=project_id,
            summary=f"Enqueued {job_type}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"job_id": job.job_id},
        )
        return job

    def next_pending_job(self, *, job_types: set[str] | None = None) -> JobRecord | None:
        statement = select(platform_jobs).where(platform_jobs.c.status == "queued")
        if job_types:
            statement = statement.where(platform_jobs.c.job_type.in_(job_types))
        statement = statement.order_by(platform_jobs.c.created_at).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _job(row) if row else None

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(platform_jobs).where(platform_jobs.c.job_id == job_id)
            ).mappings().first()
        return _job(row) if row else None

    def update_job(self, job: JobRecord) -> JobRecord:
        job.updated_at = _now()
        status = "queued" if job.status == "pending" else job.status
        with self.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status=status,
                    result_json=_redact_json(job.result) if job.result is not None else None,
                    error_summary=redact_secrets(job.error) or None,
                    attempts=job.attempts,
                    updated_at=job.updated_at,
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                )
            )
        return job

    def write_audit(
        self,
        event_type: str,
        *,
        summary: str,
        actor_user_id: str | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
    ) -> AuditEvent:
        redacted_metadata = _redact_json(metadata or {})
        event = AuditEvent(
            event_id=f"evt-{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            actor_user_id=actor_user_id,
            project_id=project_id,
            org_id=org_id,
            object_type=object_type or event_type,
            object_id=object_id or project_id or org_id or "platform",
            summary=redact_secrets(summary),
            metadata=redacted_metadata,
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(platform_audit_events).values(
                    event_id=event.event_id,
                    actor_user_id=event.actor_user_id,
                    org_id=event.org_id,
                    project_id=event.project_id,
                    event_type=event.event_type,
                    object_type=event.object_type,
                    object_id=event.object_id,
                    timestamp=event.timestamp,
                    ip_address=event.ip_address,
                    user_agent=event.user_agent,
                    summary=event.summary,
                    before_json=event.before,
                    after_json=event.after,
                    metadata_json=event.metadata,
                )
            )
        return event

    def list_audit_events(
        self,
        *,
        project_id: str | None = None,
        actor_user_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        statement = select(platform_audit_events)
        if project_id is not None:
            statement = statement.where(platform_audit_events.c.project_id == project_id)
        if actor_user_id is not None:
            statement = statement.where(platform_audit_events.c.actor_user_id == actor_user_id)
        statement = statement.order_by(platform_audit_events.c.timestamp.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_audit(row) for row in rows]

    def list_failed_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(platform_jobs)
                    .where(platform_jobs.c.status.in_(["failed", "partial", "guardrail_failed"]))
                    .order_by(platform_jobs.c.updated_at.desc())
                    .limit(limit)
                )
                .mappings()
                .fetchall()
            )
        return [_public_row(row) for row in rows]

    def codex_worker_status(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(codex_worker_jobs)).mappings().fetchall()
            queued_codex_jobs = connection.execute(
                select(func.count())
                .select_from(platform_jobs)
                .where(
                    (platform_jobs.c.job_type == "codex_task")
                    & (platform_jobs.c.status == "queued")
                )
            ).scalar_one()
        status_counts: dict[str, int] = {}
        for row in rows:
            status = str(row["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        latest = max(
            (_aware(row["created_at"]) for row in rows),
            default=None,
        )
        return {
            "queued_codex_jobs": int(queued_codex_jobs),
            "worker_job_count": len(rows),
            "status_counts": status_counts,
            "latest_worker_job_at": latest.isoformat() if latest else None,
        }

    def create_integration_credential(
        self,
        request: IntegrationCredentialCreate,
        *,
        actor_user_id: str | None = None,
    ) -> IntegrationCredentialRef:
        credential_id = f"cred-{uuid.uuid4().hex[:16]}"
        backend = "platform_hash"
        key_ref = None
        secret_hash = None
        secret_salt = None
        if request.secret_value:
            secret_salt, secret_hash = hash_token(request.secret_value)
        elif request.secret_env_var:
            backend = "env"
            key_ref = request.secret_env_var
        elif request.vault_ref:
            backend = "vault"
            key_ref = request.vault_ref
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                insert(integration_credentials).values(
                    credential_id=credential_id,
                    connector_id=request.connector_id,
                    name=request.name,
                    backend=backend,
                    key_ref=key_ref,
                    secret_hash=secret_hash,
                    secret_salt=secret_salt,
                    created_by_user_id=actor_user_id,
                    created_at=now,
                    revoked_at=None,
                    metadata_json=_redact_json(request.metadata),
                )
            )
        self.write_audit(
            "integration_credential_created",
            actor_user_id=actor_user_id,
            summary=f"Created integration credential {credential_id}.",
            object_type="integration_credential",
            object_id=credential_id,
            metadata={"credential_id": credential_id, "backend": backend},
        )
        return IntegrationCredentialRef(
            credential_id=credential_id,
            backend=backend,  # type: ignore[arg-type]
            key_ref=key_ref,
            configured=True,
            created_at=now,
        )

    def create_integration_connector(
        self,
        connector: ConnectorConfig,
        *,
        actor_user_id: str | None = None,
        org_id: str = "default",
        project_id: str | None = None,
    ) -> ConnectorConfig:
        now = _now()
        stored = connector.model_copy(update={"created_at": now, "updated_at": now})
        with self.engine.begin() as connection:
            connection.execute(
                insert(integration_connectors).values(
                    connector_id=stored.connector_id,
                    org_id=org_id,
                    project_id=project_id,
                    name=stored.name,
                    provider=stored.provider,
                    kind=stored.kind,
                    mode=stored.mode,
                    direction=stored.direction,
                    base_url=stored.base_url,
                    credential_ref_json=stored.credential_ref.model_dump(mode="json")
                    if stored.credential_ref
                    else None,
                    config_json=_redact_json(stored.config),
                    allow_writes=stored.allow_writes,
                    explicit_write_permission=stored.explicit_write_permission,
                    sandbox=stored.sandbox,
                    created_by_user_id=actor_user_id,
                    created_at=stored.created_at,
                    updated_at=stored.updated_at,
                    metadata_json=_redact_json(stored.metadata),
                )
            )
        self.write_audit(
            "integration_connector_created",
            actor_user_id=actor_user_id,
            org_id=org_id,
            project_id=project_id,
            summary=f"Created {stored.provider} integration connector {stored.connector_id}.",
            object_type="integration_connector",
            object_id=stored.connector_id,
            metadata={
                "connector_id": stored.connector_id,
                "provider": stored.provider,
                "mode": stored.mode,
                "allow_writes": stored.allow_writes,
            },
        )
        return stored

    def get_integration_connector(self, connector_id: str) -> ConnectorConfig | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(integration_connectors).where(
                        integration_connectors.c.connector_id == connector_id
                    )
                )
                .mappings()
                .first()
            )
        return _connector_config(row) if row else None

    def list_integration_connectors(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> list[ConnectorConfig]:
        statement = select(integration_connectors)
        if org_id is not None:
            statement = statement.where(integration_connectors.c.org_id == org_id)
        if project_id is not None:
            statement = statement.where(integration_connectors.c.project_id == project_id)
        statement = statement.order_by(integration_connectors.c.created_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_connector_config(row) for row in rows]

    def start_integration_sync_job(
        self,
        *,
        connector_id: str,
        actor_user_id: str | None = None,
        org_id: str = "default",
        project_id: str | None = None,
        direction: str = "import",
        mode: str = "dry_run",
        metadata: dict[str, Any] | None = None,
    ) -> SyncJobRecord:
        sync_job = SyncJobRecord(
            connector_id=connector_id,
            org_id=org_id,
            project_id=project_id,
            direction=direction,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            status="running",
            started_at=_now(),
            metadata=_redact_json(metadata or {}),
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(integration_sync_jobs).values(
                    sync_job_id=sync_job.sync_job_id,
                    connector_id=sync_job.connector_id,
                    org_id=sync_job.org_id,
                    project_id=sync_job.project_id,
                    direction=sync_job.direction,
                    mode=sync_job.mode,
                    status=sync_job.status,
                    started_at=sync_job.started_at,
                    completed_at=None,
                    rows_seen=0,
                    rows_valid=0,
                    rows_rejected=0,
                    contract_report_json=None,
                    metadata_json=sync_job.metadata,
                )
            )
        self._write_sync_audit(
            sync_job.sync_job_id,
            connector_id=connector_id,
            event_type="sync_started",
            summary=f"Started {direction} sync in {mode} mode.",
            metadata={"actor_user_id": actor_user_id, "project_id": project_id},
        )
        return sync_job

    def complete_integration_sync_job(
        self,
        sync_job: SyncJobRecord,
        *,
        records: list[ExternalRecordEnvelope] | None = None,
    ) -> SyncJobRecord:
        completed = sync_job.model_copy(update={"completed_at": _now()})
        with self.engine.begin() as connection:
            connection.execute(
                update(integration_sync_jobs)
                .where(integration_sync_jobs.c.sync_job_id == completed.sync_job_id)
                .values(
                    status=completed.status,
                    completed_at=completed.completed_at,
                    rows_seen=completed.rows_seen,
                    rows_valid=completed.rows_valid,
                    rows_rejected=completed.rows_rejected,
                    contract_report_json=completed.contract_report.model_dump(mode="json")
                    if completed.contract_report
                    else None,
                    metadata_json=_redact_json(completed.metadata),
                )
            )
            for record in records or []:
                connection.execute(
                    insert(integration_provenance_records).values(
                        provenance_record_id=f"prov-{uuid.uuid4().hex[:16]}",
                        sync_job_id=record.provenance.sync_job_id,
                        connector_id=completed.connector_id,
                        record_type=record.record_type,
                        source_system=record.provenance.source_system,
                        source_record_id=record.provenance.source_record_id,
                        source_updated_at=record.provenance.source_updated_at,
                        imported_at=record.provenance.imported_at,
                        payload_json=_redact_json(record.payload),
                        raw_metadata_json=_redact_json(record.provenance.raw_metadata),
                    )
                )
        self._write_sync_audit(
            completed.sync_job_id,
            connector_id=completed.connector_id,
            event_type="sync_completed",
            summary=f"Completed sync with status {completed.status}.",
            metadata={
                "rows_seen": completed.rows_seen,
                "rows_valid": completed.rows_valid,
                "rows_rejected": completed.rows_rejected,
            },
        )
        return completed

    def list_integration_sync_jobs(
        self,
        *,
        connector_id: str | None = None,
        limit: int = 100,
    ) -> list[SyncJobRecord]:
        statement = select(integration_sync_jobs)
        if connector_id is not None:
            statement = statement.where(integration_sync_jobs.c.connector_id == connector_id)
        statement = statement.order_by(integration_sync_jobs.c.started_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_sync_job(row) for row in rows]

    def list_integration_sync_audit_logs(
        self,
        *,
        sync_job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = select(integration_sync_audit_logs)
        if sync_job_id is not None:
            statement = statement.where(integration_sync_audit_logs.c.sync_job_id == sync_job_id)
        statement = statement.order_by(integration_sync_audit_logs.c.timestamp.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_public_row(row) for row in rows]

    def save_external_id_mappings(
        self,
        mappings: list[ExternalIdMapping],
    ) -> list[ExternalIdMapping]:
        with self.engine.begin() as connection:
            for mapping in mappings:
                connection.execute(
                    insert(external_id_mappings).values(
                        mapping_id=mapping.mapping_id,
                        connector_id=mapping.connector_id,
                        internal_id=mapping.internal_id,
                        external_id=mapping.external_id,
                        source_system=mapping.source_system,
                        source_record_id=mapping.source_record_id,
                        mapping_method=mapping.mapping_method,
                        status=mapping.status,
                        confidence=int(mapping.confidence * 1000),
                        validation_evidence_json=_redact_json(mapping.validation_evidence),
                        created_at=mapping.created_at,
                    )
                )
        for mapping in mappings:
            self.write_audit(
                "external_id_mapping_saved",
                summary=f"Saved external ID mapping {mapping.mapping_id}.",
                object_type="external_id_mapping",
                object_id=mapping.mapping_id,
                metadata={
                    "connector_id": mapping.connector_id,
                    "mapping_method": mapping.mapping_method,
                    "status": mapping.status,
                },
            )
        return mappings

    def integration_dashboard_summary(self) -> dict[str, Any]:
        return {
            "connectors": [
                connector.model_dump(mode="json", exclude={"credential_ref"})
                for connector in self.list_integration_connectors()
            ],
            "sync_jobs": [job.model_dump(mode="json") for job in self.list_integration_sync_jobs()],
            "sync_audit_logs": self.list_integration_sync_audit_logs(limit=20),
        }

    def _write_sync_audit(
        self,
        sync_job_id: str,
        *,
        connector_id: str,
        event_type: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(integration_sync_audit_logs).values(
                    sync_audit_id=f"sync-audit-{uuid.uuid4().hex[:16]}",
                    sync_job_id=sync_job_id,
                    connector_id=connector_id,
                    event_type=event_type,
                    timestamp=_now(),
                    summary=redact_secrets(summary),
                    metadata_json=_redact_json(metadata),
                )
            )

    def configure_platform_settings(
        self,
        *,
        actor_user_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        redacted = _redact_json(settings)
        self.write_audit(
            "platform_settings_configured",
            actor_user_id=actor_user_id,
            summary="Configured platform settings.",
            object_type="platform_settings",
            object_id="platform",
            metadata={"settings": redacted},
        )
        return redacted

    def get_retention_policy(self, *, scope_type: str, scope_id: str) -> RetentionPolicy:
        return RetentionPolicy(scope_type=scope_type, scope_id=scope_id)  # type: ignore[arg-type]

    def set_retention_policy(
        self,
        policy: RetentionPolicy,
        *,
        actor_user_id: str,
    ) -> RetentionPolicy:
        self.write_audit(
            "retention_policy_updated",
            actor_user_id=actor_user_id,
            summary=f"Updated retention policy for {policy.scope_type}:{policy.scope_id}.",
            object_type="retention_policy",
            object_id=f"{policy.scope_type}:{policy.scope_id}",
            metadata=policy.model_dump(mode="json"),
        )
        return policy

    def export_user_data(self, user_id: str) -> dict[str, Any]:
        policy = self.get_retention_policy(scope_type="user", scope_id=user_id)
        if not policy.export_enabled:
            raise PlatformDatabaseError("User export is disabled by retention policy.")
        user = self.get_user(user_id)
        if user is None:
            raise PlatformDatabaseError("User not found.")
        with self.engine.connect() as connection:
            membership_rows = (
                connection.execute(select(memberships).where(memberships.c.user_id == user_id))
                .mappings()
                .fetchall()
            )
            permission_rows = (
                connection.execute(
                    select(project_permissions).where(
                        (project_permissions.c.principal_type == "user")
                        & (project_permissions.c.principal_id == user_id)
                    )
                )
                .mappings()
                .fetchall()
            )
            job_rows = (
                connection.execute(
                    select(platform_jobs).where(platform_jobs.c.requested_by_user_id == user_id)
                )
                .mappings()
                .fetchall()
            )
        return {
            "user": user.model_dump(mode="json"),
            "memberships": [_public_row(row) for row in membership_rows],
            "project_permissions": [
                _permission(row).model_dump(mode="json") for row in permission_rows
            ],
            "jobs": [_job(row).model_dump(mode="json") for row in job_rows],
            "audit_events": [
                event.model_dump(mode="json")
                for event in self.list_audit_events(actor_user_id=user_id)
            ],
        }

    def health(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            pending_jobs = connection.execute(
                select(func.count())
                .select_from(platform_jobs)
                .where(platform_jobs.c.status == "queued")
            ).scalar_one()
            users_count = connection.execute(select(func.count()).select_from(users)).scalar_one()
        return {
            "ok": True,
            "database": self.database_kind,
            "database_url": self.safe_database_url,
            "users": users_count,
            "pending_jobs": pending_jobs,
        }

    def _mentioned_user_ids(self, body: str) -> list[str]:
        lowered = body.lower()
        mentioned: list[str] = []
        for user in self.list_users():
            display = (user.display_name or "").strip().lower()
            display_slug = display.replace(" ", ".")
            tokens = {user.email.lower()}
            if display:
                tokens.update({display, f"@{display}", f"@{display_slug}"})
            if any(token and token in lowered for token in tokens):
                mentioned.append(user.user_id)
        return sorted(set(mentioned))

    def _insert_notification(
        self,
        *,
        org_id: str,
        recipient_user_id: str,
        actor_user_id: str | None,
        project_id: str | None,
        event_type: str,
        title: str,
        body: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any],
    ) -> Notification:
        notification = Notification(
            notification_id=f"notif-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            actor_user_id=actor_user_id,
            project_id=project_id,
            event_type=event_type,
            title=redact_secrets(title),
            body=redact_secrets(body),
            target_type=target_type,
            target_id=target_id,
            metadata=_redact_json(metadata),
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(notifications).values(
                    notification_id=notification.notification_id,
                    org_id=notification.org_id,
                    recipient_user_id=notification.recipient_user_id,
                    actor_user_id=notification.actor_user_id,
                    project_id=notification.project_id,
                    event_type=notification.event_type,
                    title=notification.title,
                    body=notification.body,
                    target_type=notification.target_type,
                    target_id=notification.target_id,
                    is_read=notification.is_read,
                    created_at=notification.created_at,
                    read_at=notification.read_at,
                    metadata_json=notification.metadata,
                )
            )
        return notification

    def _insert_activity(
        self,
        *,
        org_id: str,
        project_id: str | None,
        actor_user_id: str | None,
        activity_type: str,
        object_type: str,
        object_id: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> ActivityFeedItem:
        item = ActivityFeedItem(
            activity_id=f"activity-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            object_type=object_type,
            object_id=object_id,
            summary=redact_secrets(summary),
            metadata=_redact_json(metadata),
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(activity_feed).values(
                    activity_id=item.activity_id,
                    org_id=item.org_id,
                    project_id=item.project_id,
                    actor_user_id=item.actor_user_id,
                    activity_type=item.activity_type,
                    object_type=item.object_type,
                    object_id=item.object_id,
                    summary=item.summary,
                    created_at=item.created_at,
                    metadata_json=item.metadata,
                )
            )
        return item

    def _record_migration(self, version: str) -> None:
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(schema_migrations.c.version).where(schema_migrations.c.version == version)
            ).first()
            if exists is None:
                connection.execute(
                    insert(schema_migrations).values(version=version, applied_at=_now())
                )

    def _resolve_database_url(self, *, database_url: str | None, db_path: Path | None) -> str:
        if database_url:
            url = make_url(database_url)
            if url.drivername in {"postgres"}:
                return str(url.set(drivername="postgresql+psycopg"))
            if url.drivername.startswith("postgresql"):
                return str(url)
            if url.drivername.startswith("sqlite"):
                return str(url)
            raise PlatformDatabaseError("Unsupported platform database URL.")
        path = (db_path or self.root_dir / ".molecule-ranker" / "platform.sqlite").resolve()
        return f"sqlite:///{path}"


def _now() -> datetime:
    return datetime.now(UTC)


def _redact_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(redact_secrets(json.dumps(value, sort_keys=True, default=str)))


def _principal(
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    team_id: str | None = None,
) -> tuple[str, str]:
    if user_id is not None:
        return "user", user_id
    if team_id is not None:
        return "team", team_id
    if org_id is not None:
        return "org", org_id
    raise PlatformDatabaseError("Missing project permission principal.")


def _user(row: Any) -> UserAccount:
    return UserAccount(
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        display_name=row["display_name"],
        is_active=bool(row["is_active"]),
        is_admin=bool(row["is_admin"]),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        last_login_at=_aware(row["last_login_at"]) if row["last_login_at"] else None,
        auth_provider=str(row["auth_provider"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _organization(row: Any) -> Organization:
    return Organization(
        org_id=str(row["org_id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _permission(row: Any) -> ProjectPermission:
    return ProjectPermission(
        permission_id=str(row["permission_id"]),
        project_id=str(row["project_id"]),
        principal_type=str(row["principal_type"]),  # type: ignore[arg-type]
        principal_id=str(row["principal_id"]),
        role=str(row["role"]),  # type: ignore[arg-type]
        granted_by=str(row["granted_by"]),
        granted_at=_aware(row["granted_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _audit(row: Any) -> AuditEvent:
    return AuditEvent(
        event_id=str(row["event_id"]),
        actor_user_id=row["actor_user_id"],
        org_id=row["org_id"],
        project_id=row["project_id"],
        event_type=str(row["event_type"]),
        object_type=str(row["object_type"]),
        object_id=str(row["object_id"]),
        timestamp=_aware(row["timestamp"]),
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        summary=str(row["summary"]),
        before=row["before_json"],
        after=row["after_json"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _project_comment(row: Any) -> ProjectComment:
    return ProjectComment(
        comment_id=str(row["comment_id"]),
        org_id=str(row["org_id"]),
        project_id=str(row["project_id"]),
        object_type=str(row["object_type"]),  # type: ignore[arg-type]
        object_id=str(row["object_id"]),
        author_user_id=str(row["author_user_id"]),
        body=str(row["body"]),
        run_id=row["run_id"],
        candidate_id=row["candidate_id"],
        mentions=list(row["mentions_json"] or []),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _assignment(row: Any) -> Assignment:
    return Assignment(
        assignment_id=str(row["assignment_id"]),
        org_id=str(row["org_id"]),
        project_id=str(row["project_id"]),
        object_type=str(row["object_type"]),  # type: ignore[arg-type]
        object_id=str(row["object_id"]),
        assigned_to_user_id=str(row["assigned_to_user_id"]),
        assigned_by_user_id=str(row["assigned_by_user_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        run_id=row["run_id"],
        candidate_id=row["candidate_id"],
        due_at=_aware(row["due_at"]) if row["due_at"] else None,
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _notification(row: Any) -> Notification:
    return Notification(
        notification_id=str(row["notification_id"]),
        org_id=str(row["org_id"]),
        recipient_user_id=str(row["recipient_user_id"]),
        actor_user_id=row["actor_user_id"],
        project_id=row["project_id"],
        event_type=str(row["event_type"]),
        title=str(row["title"]),
        body=str(row["body"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        is_read=bool(row["is_read"]),
        created_at=_aware(row["created_at"]),
        read_at=_aware(row["read_at"]) if row["read_at"] else None,
        metadata=dict(row["metadata_json"] or {}),
    )


def _activity(row: Any) -> ActivityFeedItem:
    return ActivityFeedItem(
        activity_id=str(row["activity_id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        actor_user_id=row["actor_user_id"],
        activity_type=str(row["activity_type"]),
        object_type=str(row["object_type"]),
        object_id=str(row["object_id"]),
        summary=str(row["summary"]),
        created_at=_aware(row["created_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _connector_config(row: Any) -> ConnectorConfig:
    credential_ref = row["credential_ref_json"]
    return ConnectorConfig(
        connector_id=str(row["connector_id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),  # type: ignore[arg-type]
        kind=str(row["kind"]),  # type: ignore[arg-type]
        mode=str(row["mode"]),  # type: ignore[arg-type]
        direction=str(row["direction"]),  # type: ignore[arg-type]
        base_url=row["base_url"],
        credential_ref=IntegrationCredentialRef.model_validate(credential_ref)
        if credential_ref
        else None,
        config=dict(row["config_json"] or {}),
        allow_writes=bool(row["allow_writes"]),
        explicit_write_permission=bool(row["explicit_write_permission"]),
        sandbox=bool(row["sandbox"]),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        metadata=dict(row["metadata_json"] or {}),
    )


def _sync_job(row: Any) -> SyncJobRecord:
    return SyncJobRecord(
        sync_job_id=str(row["sync_job_id"]),
        connector_id=str(row["connector_id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        direction=str(row["direction"]),  # type: ignore[arg-type]
        mode=str(row["mode"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        started_at=_aware(row["started_at"]) if row["started_at"] else None,
        completed_at=_aware(row["completed_at"]) if row["completed_at"] else None,
        rows_seen=int(row["rows_seen"] or 0),
        rows_valid=int(row["rows_valid"] or 0),
        rows_rejected=int(row["rows_rejected"] or 0),
        contract_report=row["contract_report_json"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _job(row: Any) -> JobRecord:
    status = "pending" if row["status"] == "queued" else str(row["status"])
    return JobRecord(
        job_id=str(row["job_id"]),
        job_type=str(row["job_type"]),
        status=status,  # type: ignore[arg-type]
        project_id=row["project_id"],
        requested_by_user_id=row["requested_by_user_id"],
        payload=dict(row["config_snapshot_json"] or {}),
        result=row["result_json"],
        error=str(row["error_summary"] or ""),
        attempts=int(row["attempts"] or 0),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        started_at=_aware(row["started_at"]) if row["started_at"] else None,
        completed_at=_aware(row["completed_at"]) if row["completed_at"] else None,
    )


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _public_row(row: Any) -> dict[str, Any]:
    return {key: value for key, value in dict(row).items() if "password" not in key.lower()}


__all__ = [
    "PlatformDatabase",
    "PlatformDatabaseError",
    "REQUIRED_TABLES",
    "SCHEMA_VERSION",
    "activity_feed",
    "assignments",
    "external_id_mappings",
    "integration_connectors",
    "integration_credentials",
    "integration_provenance_records",
    "integration_sync_audit_logs",
    "integration_sync_jobs",
    "metadata",
    "notifications",
    "project_comments",
]
