from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.database import PlatformDatabase, project_workspaces
from molecule_ranker.platform.policy import (
    PolicyEngine,
    project_policy_overrides,
)
from molecule_ranker.platform.schemas import UserAccount


def test_default_policy_blocks_unsafe_action() -> None:
    result = PolicyEngine.default().evaluate(
        "generated_molecule.export",
        {
            "generated_molecule": True,
            "review_approved": False,
        },
    )

    assert not result.allowed
    assert result.status == "blocked"
    assert result.matched_rules[0].rule_id == "generated_molecules_require_review_before_export"
    assert "review" in result.violations[0].lower()


def test_project_override_requires_permission_and_allows_docking(tmp_path: Path) -> None:
    database, admin, scientist = _database_with_project(tmp_path)

    with pytest.raises(PermissionError, match="project:update"):
        project_policy_overrides(
            {"docking.run": {"allow": True, "reason": "Approved for validated null docking."}},
            actor=scientist,
            database=database,
            project_id="project-policy",
        )

    overrides = project_policy_overrides(
        {"docking.run": {"allow": True, "reason": "Approved for validated null docking."}},
        actor=admin,
        database=database,
        project_id="project-policy",
    )
    result = PolicyEngine(project_overrides=overrides).evaluate(
        "docking.run",
        {"project_policy_allows_docking": False},
        user=admin,
        project_id="project-policy",
    )

    assert result.allowed
    assert result.status == "allowed"
    assert result.override_applied == "project"
    assert "Approved for validated null docking." in result.requirements


def test_policy_result_audited_and_redacts_secrets(tmp_path: Path) -> None:
    database, _admin, scientist = _database_with_project(tmp_path)

    result = PolicyEngine.default(database=database).evaluate(
        "codex.run_task",
        {
            "uses_raw_assay_files": True,
            "api_key": "secret-token-value",
        },
        user=scientist,
        project_id="project-policy",
        audit=True,
    )

    assert not result.allowed
    assert result.audit_event_id is not None
    events = database.list_audit_events(actor_user_id=scientist.user_id)
    policy_events = [event for event in events if event.event_type == "policy_evaluated"]
    assert len(policy_events) == 1
    assert policy_events[0].metadata["context"]["api_key"] == "[REDACTED]"
    assert "secret-token-value" not in json.dumps(policy_events[0].model_dump(mode="json"))


def test_policy_cli_list_validate_and_explain() -> None:
    runner = CliRunner()

    listed = runner.invoke(app, ["policy", "list", "--json"])
    validated = runner.invoke(app, ["policy", "validate", "--json"])
    explained = runner.invoke(
        app,
        [
            "policy",
            "explain",
            "--action",
            "support_bundle.generate",
            "--context-json",
            '{"include_codex_transcripts": true}',
            "--json",
        ],
    )

    assert listed.exit_code == 0, listed.stdout
    assert validated.exit_code == 0, validated.stdout
    assert explained.exit_code == 0, explained.stdout
    assert "support_bundles_exclude_codex_transcripts_by_default" in explained.stdout
    assert json.loads(validated.stdout)["valid"] is True


def _database_with_project(tmp_path: Path) -> tuple[PlatformDatabase, UserAccount, UserAccount]:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    admin = database.create_user(
        email="admin@example.com",
        password="Admin-password-1",
        roles=["platform_admin", "user"],
    )
    scientist = database.create_user(email="scientist@example.com", password="User-password-1")
    org = database.create_organization(
        name="Policy Org",
        org_id="org-policy",
        created_by_user_id=admin.user_id,
    )
    with database.engine.begin() as connection:
        now = datetime.now(UTC)
        connection.execute(
            insert(project_workspaces).values(
                project_id="project-policy",
                org_id=org.org_id,
                name="Project Policy",
                root_dir=None,
                created_at=now,
                updated_at=now,
                metadata_json={},
            )
        )
    database.grant_project_permission(
        project_id="project-policy",
        role="project_owner",
        actor_user_id=admin.user_id,
        user_id=admin.user_id,
    )
    database.grant_project_permission(
        project_id="project-policy",
        role="viewer",
        actor_user_id=admin.user_id,
        user_id=scientist.user_id,
    )
    return database, admin, scientist
