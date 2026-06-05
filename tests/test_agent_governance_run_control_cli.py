from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_governance_control_cli_apply_list_and_clear() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("run-controls.json")
        applied = runner.invoke(
            app,
            [
                "governance",
                "control",
                "apply",
                "--control-type",
                "kill_switch",
                "--applied-by",
                "admin-1",
                "--reason",
                "Incident response.",
                "--project-id",
                "project-1",
                "--session-action",
                "cancel",
                "--state-path",
                str(state_path),
            ],
        )

        assert applied.exit_code == 0, applied.output
        payload = json.loads(applied.output)
        control_id = payload["control_id"]
        assert payload["control_type"] == "kill_switch"
        assert payload["active"] is True

        listed = runner.invoke(
            app,
            [
                "governance",
                "control",
                "list",
                "--active-only",
                "--project-id",
                "project-1",
                "--state-path",
                str(state_path),
            ],
        )
        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["control_id"] == control_id

        cleared = runner.invoke(
            app,
            [
                "governance",
                "control",
                "clear",
                "--control-id",
                control_id,
                "--cleared-by",
                "admin-1",
                "--state-path",
                str(state_path),
            ],
        )
        assert cleared.exit_code == 0, cleared.output
        assert json.loads(cleared.output)["active"] is False

        active = runner.invoke(
            app,
            [
                "governance",
                "control",
                "list",
                "--active-only",
                "--state-path",
                str(state_path),
            ],
        )
        assert active.exit_code == 0, active.output
        assert json.loads(active.output) == []
