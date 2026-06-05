from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.copilot.cli_state import CoPilotCLIStateStore
from molecule_ranker.copilot.schemas import CoPilotAction

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _pending_action() -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id="action-review-1",
        campaign_id="camp-cli",
        trigger_id="trigger-cli",
        action_type="create_review_request",
        tool_name=None,
        tool_args={},
        side_effect_level="db_write",
        risk_level="medium",
        requires_approval=True,
        approval_reason="Human review required.",
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata={},
    )


def test_copilot_cli_help_works():
    result = CliRunner().invoke(app, ["copilot", "--help"])

    assert result.exit_code == 0
    for command in [
        "start",
        "stop",
        "status",
        "check",
        "events",
        "triggers",
        "actions",
        "approve-action",
        "reject-action",
        "status-update",
        "eval",
    ]:
        assert command in result.stdout


def test_copilot_start_dry_run_does_not_persist_session():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            app,
            [
                "copilot",
                "start",
                "--campaign-id",
                "camp-cli",
                "--autonomy",
                "observe_only",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["dry_run"] is True
        assert payload["session"]["campaign_id"] == "camp-cli"
        assert CoPilotCLIStateStore().list_sessions() == []


def test_copilot_check_runs_one_monitoring_cycle():
    runner = CliRunner()
    with runner.isolated_filesystem():
        start = runner.invoke(
            app,
            ["copilot", "start", "--campaign-id", "camp-cli", "--autonomy", "observe_only"],
        )
        check = runner.invoke(app, ["copilot", "check", "--campaign-id", "camp-cli"])

        assert start.exit_code == 0
        assert check.exit_code == 0
        payload = json.loads(check.stdout)
        assert payload["campaign_id"] == "camp-cli"
        assert payload["events_detected"] == 1
        assert payload["triggers_routed"] == 1
        assert CoPilotCLIStateStore().list_events(campaign_id="camp-cli")


def test_copilot_approve_action_command_updates_action():
    runner = CliRunner()
    with runner.isolated_filesystem():
        store = CoPilotCLIStateStore()
        store.upsert_action(_pending_action())

        result = runner.invoke(
            app,
            [
                "copilot",
                "approve-action",
                "--action-id",
                "action-review-1",
                "--reviewer-id",
                "reviewer-1",
                "--rationale",
                "Source-backed planning request is acceptable.",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "approved"
        assert payload["metadata"]["approved_by"] == "reviewer-1"
        assert payload["metadata"]["approval_rationale"] == (
            "Source-backed planning request is acceptable."
        )


def test_copilot_status_update_generates_markdown_file():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(app, ["copilot", "start", "--campaign-id", "camp-cli"])
        runner.invoke(app, ["copilot", "check", "--campaign-id", "camp-cli"])

        result = runner.invoke(
            app,
            [
                "copilot",
                "status-update",
                "--campaign-id",
                "camp-cli",
                "--output",
                "copilot_status_update.md",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["output"] == "copilot_status_update.md"
        with open("copilot_status_update.md") as handle:
            content = handle.read()
        assert "# Co-Pilot Status Update: camp-cli" in content
        assert "camp-cli:scheduled_check" in content
