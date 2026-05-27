from __future__ import annotations

from pathlib import Path

import pytest

from molecule_ranker.platform.database import PlatformDatabase


def test_comment_added_and_activity_recorded(tmp_path: Path) -> None:
    database, owner, _reviewer, _viewer = _database_with_collaborators(tmp_path)

    comment = database.add_project_comment(
        project_id="project-1",
        author_user_id=owner.user_id,
        body="Please review this candidate.",
        object_type="candidate",
        object_id="Rasagiline",
        candidate_id="Rasagiline",
    )

    comments = database.list_project_comments(project_id="project-1")
    activity = database.list_activity(project_id="project-1")
    assert comments[0].comment_id == comment.comment_id
    assert comments[0].metadata["is_biomedical_evidence"] is False
    assert comments[0].metadata["changes_scores"] is False
    assert activity[0].activity_type == "comment_added"


def test_mention_creates_notification(tmp_path: Path) -> None:
    database, owner, reviewer, _viewer = _database_with_collaborators(tmp_path)

    database.add_project_comment(
        project_id="project-1",
        author_user_id=owner.user_id,
        body=f"Can {reviewer.email} check this review item?",
        object_type="project",
    )

    notifications = database.list_notifications(user_id=reviewer.user_id)
    assert len(notifications) == 1
    assert notifications[0].event_type == "mention"
    assert notifications[0].target_type == "project_comment"


def test_assignment_requires_review_write_permission(tmp_path: Path) -> None:
    database, _owner, reviewer, viewer = _database_with_collaborators(tmp_path)

    with pytest.raises(PermissionError):
        database.create_assignment(
            project_id="project-1",
            assigned_to_user_id=reviewer.user_id,
            assigned_by_user_id=viewer.user_id,
            object_type="candidate",
            object_id="Rasagiline",
        )

    assignment = database.create_assignment(
        project_id="project-1",
        assigned_to_user_id=viewer.user_id,
        assigned_by_user_id=reviewer.user_id,
        object_type="candidate",
        object_id="Rasagiline",
    )

    assert assignment.metadata["grants_permissions"] is False
    assert database.effective_project_role(
        user_id=viewer.user_id,
        project_id="project-1",
    ) == "viewer"
    assert database.list_notifications(user_id=viewer.user_id)[0].event_type == "assignment"


def _database_with_collaborators(tmp_path: Path):
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    owner = database.create_user(email="owner@example.com", password="Owner-password-1")
    reviewer = database.create_user(
        email="reviewer@example.com",
        password="Reviewer-password-1",
        display_name="Review Lead",
    )
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="project_owner",
        actor_user_id=owner.user_id,
        user_id=owner.user_id,
    )
    database.grant_project_permission(
        project_id="project-1",
        role="reviewer",
        actor_user_id=owner.user_id,
        user_id=reviewer.user_id,
    )
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=owner.user_id,
        user_id=viewer.user_id,
    )
    return database, owner, reviewer, viewer
