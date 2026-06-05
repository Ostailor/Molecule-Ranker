from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_governance_grant_cli_create_list_check_and_revoke() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("grants.json")
        create = runner.invoke(
            app,
            [
                "governance",
                "grant",
                "create",
                "--agent-id",
                "agent-1",
                "--agent-type",
                "runtime_agent",
                "--capability",
                "run_ranking",
                "--scope-type",
                "project",
                "--scope-id",
                "project-1",
                "--granted-by",
                "admin-1",
                "--actor-type",
                "admin",
                "--authorized-capability",
                "run_ranking",
                "--state-path",
                str(state_path),
            ],
        )

        assert create.exit_code == 0, create.output
        created = json.loads(create.output)
        grant_id = created["grant"]["grant_id"]
        assert created["allowed"] is True

        listed = runner.invoke(
            app,
            ["governance", "grant", "list", "--state-path", str(state_path)],
        )
        checked = runner.invoke(
            app,
            [
                "governance",
                "grant",
                "check",
                "--agent-id",
                "agent-1",
                "--capability",
                "run_ranking",
                "--scope-type",
                "project",
                "--scope-id",
                "project-1",
                "--state-path",
                str(state_path),
            ],
        )

        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["grant_id"] == grant_id
        assert checked.exit_code == 0, checked.output
        assert json.loads(checked.output)["allowed"] is True

        revoked = runner.invoke(
            app,
            [
                "governance",
                "grant",
                "revoke",
                "--grant-id",
                grant_id,
                "--revoked-by",
                "admin-1",
                "--state-path",
                str(state_path),
            ],
        )
        denied = runner.invoke(
            app,
            [
                "governance",
                "grant",
                "check",
                "--agent-id",
                "agent-1",
                "--capability",
                "run_ranking",
                "--scope-type",
                "project",
                "--scope-id",
                "project-1",
                "--state-path",
                str(state_path),
            ],
        )

        assert revoked.exit_code == 0, revoked.output
        assert json.loads(revoked.output)["status"] == "revoked"
        assert denied.exit_code == 1
        assert json.loads(denied.output)["allowed"] is False


def test_governance_grant_cli_blocks_codex_active_grant() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            [
                "governance",
                "grant",
                "create",
                "--agent-id",
                "agent-1",
                "--agent-type",
                "runtime_agent",
                "--capability",
                "run_ranking",
                "--scope-type",
                "project",
                "--granted-by",
                "codex",
                "--actor-type",
                "codex",
                "--authorized-capability",
                "run_ranking",
                "--state-path",
                "grants.json",
            ],
        )

        assert result.exit_code == 1
        assert "Codex cannot create active" in json.loads(result.output)["reason"]
