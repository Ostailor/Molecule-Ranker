from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.platform.schemas import (
    ActivityFeedItem,
    Assignment,
    CodexWorkerJob,
    Membership,
    Notification,
    Organization,
    PlatformAuditEvent,
    PlatformJob,
    ProjectComment,
    ProjectPermission,
    Team,
    UserAccount,
)


def test_platform_identity_and_tenancy_schemas_use_aware_timestamps() -> None:
    user = UserAccount(
        user_id="user-1",
        email="Scientist@Example.com",
        display_name=None,
        is_active=True,
        is_admin=False,
        auth_provider="local_password",
    )
    org = Organization(org_id="org-1", name="Discovery", slug="discovery")
    team = Team(team_id="team-1", org_id=org.org_id, name="Review", slug="review")
    membership = Membership(
        membership_id="member-1",
        user_id=user.user_id,
        org_id=org.org_id,
        team_id=team.team_id,
        role="scientist",
    )

    assert user.email == "scientist@example.com"
    assert user.created_at.tzinfo is not None
    assert org.updated_at.tzinfo is not None
    assert team.org_id == "org-1"
    assert membership.role == "scientist"


def test_platform_permission_audit_job_and_codex_worker_schemas() -> None:
    permission = ProjectPermission(
        permission_id="perm-1",
        project_id="project-1",
        principal_type="team",
        principal_id="team-1",
        role="reviewer",
        granted_by="user-admin",
    )
    audit = PlatformAuditEvent(
        event_id="event-1",
        actor_user_id="user-admin",
        org_id="org-1",
        project_id="project-1",
        event_type="project.permission.granted",
        object_type="project_permission",
        object_id=permission.permission_id,
        summary="Granted reviewer access.",
        before=None,
        after=permission.model_dump(mode="json"),
    )
    job = PlatformJob(
        job_id="job-1",
        org_id="org-1",
        project_id="project-1",
        requested_by_user_id="user-admin",
        job_type="codex_task",
        status="queued",
        priority="normal",
        config_snapshot={"task_type": "summarize_project"},
    )
    codex_job = CodexWorkerJob(
        codex_job_id="codex-job-1",
        platform_job_id=job.job_id,
        org_id="org-1",
        project_id="project-1",
        requested_by_user_id="user-admin",
        task_type="summarize_project",
        codex_task_id="codex-task-1",
        status="queued",
        allowed_artifact_ids=["artifact-1"],
        allowed_commands=[],
        forbidden_commands=["curl"],
        transcript_artifact_id=None,
        guardrail_status="pending",
    )

    assert permission.project_id == "project-1"
    assert permission.principal_type == "team"
    assert audit.timestamp.tzinfo is not None
    assert job.result_artifact_ids == []
    assert codex_job.allowed_artifact_ids == ["artifact-1"]


def test_collaboration_schemas_capture_non_evidence_records() -> None:
    comment = ProjectComment(
        comment_id="comment-1",
        project_id="project-1",
        object_type="candidate",
        object_id="Rasagiline",
        author_user_id="user-1",
        body="Review this item.",
        mentions=["user-2"],
        metadata={"is_biomedical_evidence": False, "changes_scores": False},
    )
    assignment = Assignment(
        assignment_id="assign-1",
        project_id="project-1",
        object_type="candidate",
        object_id="Rasagiline",
        assigned_to_user_id="user-2",
        assigned_by_user_id="user-1",
        metadata={"grants_permissions": False},
    )
    notification = Notification(
        notification_id="notif-1",
        recipient_user_id="user-2",
        actor_user_id="user-1",
        project_id="project-1",
        event_type="mention",
        title="Mention",
        body="You were mentioned.",
        target_type="project_comment",
        target_id=comment.comment_id,
    )
    activity = ActivityFeedItem(
        activity_id="activity-1",
        project_id="project-1",
        actor_user_id="user-1",
        activity_type="comment_added",
        object_type="project_comment",
        object_id=comment.comment_id,
        summary="Commented.",
    )

    assert comment.metadata["is_biomedical_evidence"] is False
    assert assignment.metadata["grants_permissions"] is False
    assert notification.is_read is False
    assert activity.created_at.tzinfo is not None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            Membership,
            {
                "membership_id": "member-1",
                "user_id": "user-1",
                "org_id": "org-1",
                "role": "superuser",
            },
        ),
        (
            ProjectPermission,
            {
                "permission_id": "perm-1",
                "project_id": "project-1",
                "principal_type": "workspace",
                "principal_id": "team-1",
                "role": "reviewer",
                "granted_by": "user-admin",
            },
        ),
        (
            PlatformJob,
            {
                "job_id": "job-1",
                "org_id": "org-1",
                "requested_by_user_id": "user-1",
                "job_type": "shell",
                "status": "queued",
                "priority": "normal",
            },
        ),
        (
            PlatformJob,
            {
                "job_id": "job-1",
                "org_id": "org-1",
                "requested_by_user_id": "user-1",
                "job_type": "ranking",
                "status": "waiting",
                "priority": "normal",
            },
        ),
    ],
)
def test_platform_schemas_reject_disallowed_values(model: type, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model(**payload)


def test_platform_schemas_reject_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        UserAccount(
            user_id="user-1",
            email="user@example.com",
            is_active=True,
            is_admin=False,
            created_at=datetime(2026, 1, 1),
            updated_at=datetime.now(UTC),
            auth_provider="local_password",
        )
