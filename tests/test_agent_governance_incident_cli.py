from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.agent_governance.incidents import (
    AgentIncidentManager,
    IncidentStore,
    IncidentTriggerEvent,
)
from molecule_ranker.cli import app


def test_governance_incident_cli_list_show_assign_resolve_and_export() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        state_path = Path("incidents.json")
        manager = AgentIncidentManager(store=IncidentStore(state_path))
        incident = manager.create_incident_from_trigger(
            IncidentTriggerEvent(
                trigger_type="unauthorized_tool_attempt",
                agent_id="agent-1",
                summary="Unauthorized tool attempt with token=secretsecret.",
            )
        )

        listed = runner.invoke(
            app,
            [
                "governance",
                "incident",
                "list",
                "--state-path",
                str(state_path),
            ],
        )
        shown = runner.invoke(
            app,
            [
                "governance",
                "incident",
                "show",
                "--incident-id",
                incident.incident_id,
                "--state-path",
                str(state_path),
            ],
        )
        assigned = runner.invoke(
            app,
            [
                "governance",
                "incident",
                "assign",
                "--incident-id",
                incident.incident_id,
                "--assigned-to",
                "admin-1",
                "--assigned-by",
                "lead-1",
                "--state-path",
                str(state_path),
            ],
        )
        exported = runner.invoke(
            app,
            [
                "governance",
                "incident",
                "export",
                "--incident-id",
                incident.incident_id,
                "--output",
                "incident-report.json",
                "--state-path",
                str(state_path),
            ],
        )
        resolved = runner.invoke(
            app,
            [
                "governance",
                "incident",
                "resolve",
                "--incident-id",
                incident.incident_id,
                "--resolved-by",
                "admin-1",
                "--rationale",
                "Reviewed and blocked.",
                "--state-path",
                str(state_path),
            ],
        )

        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["incident_id"] == incident.incident_id
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["incident_id"] == incident.incident_id
        assert assigned.exit_code == 0, assigned.output
        assert json.loads(assigned.output)["assigned_to"] == "admin-1"
        assert exported.exit_code == 0, exported.output
        assert json.loads(exported.output)["redacted"] is True
        report_text = Path("incident-report.json").read_text(encoding="utf-8")
        assert "secretsecret" not in report_text
        assert resolved.exit_code == 0, resolved.output
        assert json.loads(resolved.output)["status"] == "resolved"
