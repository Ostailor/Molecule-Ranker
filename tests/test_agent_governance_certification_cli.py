from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_governance_certification_cli_certify_list_and_revoke() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("certifications.json")
        created = runner.invoke(
            app,
            [
                "governance",
                "certify",
                "--agent-id",
                "agent-1",
                "--certification-type",
                "autonomy_level",
                "--autonomy-level",
                "execute_with_approval",
                "--certified-by",
                "admin-1",
                "--actor-type",
                "admin",
                "--authorized-certification-type",
                "autonomy_level",
                "--eval-suite-name",
                "mock-autonomy-eval",
                "--eval-score",
                "0.93",
                "--eval-artifact-id",
                "eval-artifact-1",
                "--state-path",
                str(state_path),
            ],
        )

        assert created.exit_code == 0, created.output
        payload = json.loads(created.output)
        certification_id = payload["certification"]["certification_id"]
        assert payload["allowed"] is True

        listed = runner.invoke(
            app,
            [
                "governance",
                "certification",
                "list",
                "--agent-id",
                "agent-1",
                "--state-path",
                str(state_path),
            ],
        )
        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["certification_id"] == certification_id

        revoked = runner.invoke(
            app,
            [
                "governance",
                "certification",
                "revoke",
                "--certification-id",
                certification_id,
                "--revoked-by",
                "admin-1",
                "--state-path",
                str(state_path),
            ],
        )
        assert revoked.exit_code == 0, revoked.output
        assert json.loads(revoked.output)["metadata"]["revoked"] is True


def test_governance_certify_cli_blocks_codex_certifier() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            [
                "governance",
                "certify",
                "--agent-id",
                "agent-1",
                "--certification-type",
                "tool_use",
                "--certified-by",
                "codex",
                "--actor-type",
                "codex",
                "--authorized-certification-type",
                "tool_use",
                "--state-path",
                "certifications.json",
            ],
        )

        assert result.exit_code == 1
        assert "Codex cannot certify" in json.loads(result.output)["reason"]
