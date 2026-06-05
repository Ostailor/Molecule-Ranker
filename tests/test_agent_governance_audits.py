from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.agent_governance import (
    AgentGovernanceAuditAnalyticsBuilder,
    GovernanceAuditEvent,
)
from molecule_ranker.cli import app

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_governance_audit_report_generated(tmp_path: Path) -> None:
    builder = AgentGovernanceAuditAnalyticsBuilder()

    analytics = builder.build_report(
        events=_events(),
        org_id="org-1",
        project_id="project-1",
        period="weekly",
    )
    exported = builder.write_outputs(analytics, tmp_path)

    assert analytics.report.project_id == "project-1"
    assert analytics.report.total_tool_calls == 3
    assert analytics.report.guardrail_failures == 2
    assert analytics.report.approval_requests == 4
    assert analytics.report.approval_rejections == 1
    assert exported.json_path.name == "governance_report.json"
    assert exported.markdown_path.name == "governance_report.md"
    assert exported.json_path.exists()
    assert exported.markdown_path.exists()


def test_governance_audit_trends_computed() -> None:
    analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
        events=_events(),
        project_id="project-1",
        period="weekly",
    )

    assert analytics.policy_violation_trends == {"2026-W23": 2}
    assert analytics.budget_usage_trends == {"2026-W23": 42.0}
    assert analytics.incident_trends == {"2026-W23": 1}
    assert analytics.autonomy_level_distribution == {"execute_safe_tools": 2}
    assert analytics.tool_package_risk_distribution == {"high": 1}
    assert analytics.repair_rates == {"failure": 0.5, "success": 0.5}
    assert analytics.copilot_action_outcomes == {"blocked": 1, "completed": 2}
    assert analytics.subagent_disagreement_rates == {"agent-1": 0.5}
    assert analytics.codex_task_failure_rates == {"agent-1": 0.25}


def test_governance_audit_report_redacts_secrets(tmp_path: Path) -> None:
    secret_agent = "agent-token=supersecretvalue"
    event = GovernanceAuditEvent(
        event_id="event-secret",
        event_type="tool_call",
        occurred_at=NOW,
        agent_id=secret_agent,
        org_id="org-1",
        project_id="project-1",
        count=1,
        metadata={"raw": "api_key=anothersecretvalue"},
    )

    analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
        events=[event],
        org_id="org-1",
        project_id="project-1",
        period="weekly",
    )
    exported = AgentGovernanceAuditAnalyticsBuilder().write_outputs(analytics, tmp_path)
    json_text = exported.json_path.read_text(encoding="utf-8")
    markdown_text = exported.markdown_path.read_text(encoding="utf-8")

    assert "supersecretvalue" not in json_text
    assert "supersecretvalue" not in markdown_text
    assert "anothersecretvalue" not in json_text
    assert "[REDACTED" in json_text


def test_governance_report_cli_writes_outputs() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        events_path = Path("events.json")
        events_path.write_text(
            json.dumps(
                {
                    "events": [
                        event.model_dump(mode="json")
                        for event in _events()
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "governance",
                "report",
                "--project-id",
                "project-1",
                "--events-path",
                str(events_path),
                "--output-dir",
                "reports",
            ],
        )
        analytics = runner.invoke(
            app,
            [
                "governance",
                "analytics",
                "--period",
                "weekly",
                "--project-id",
                "project-1",
                "--events-path",
                str(events_path),
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["json_path"] == "reports/governance_report.json"
        assert Path("reports/governance_report.md").exists()
        assert analytics.exit_code == 0, analytics.output
        assert json.loads(analytics.output)["report"]["total_tool_calls"] == 3


def _events() -> list[GovernanceAuditEvent]:
    return [
        _event("tool-1", "tool_call", agent_id="agent-1", count=3),
        _event(
            "guardrail-1",
            "guardrail_failure",
            agent_id="agent-1",
            role="ranking",
            count=2,
        ),
        _event("approval-1", "approval_request", agent_id="agent-1", count=4),
        _event("approval-reject-1", "approval_rejection", agent_id="agent-1", count=1),
        _event("policy-1", "policy_violation", agent_id="agent-1", count=2),
        _event("budget-1", "budget_usage", agent_id="agent-1", value=42.0),
        _event("budget-violation-1", "budget_violation", agent_id="agent-1"),
        _event("incident-1", "incident_opened", agent_id="agent-1"),
        _event("incident-2", "incident_resolved", agent_id="agent-1"),
        _event(
            "autonomy-1",
            "autonomy_level",
            agent_id="agent-1",
            autonomy_level="execute_safe_tools",
            count=2,
        ),
        _event("tool-risk-1", "tool_package_risk", risk_level="high"),
        _event("override-1", "human_override", agent_id="agent-1", count=2),
        _event("repair-1", "repair_outcome", agent_id="agent-1", outcome="success"),
        _event("repair-2", "repair_outcome", agent_id="agent-1", outcome="failure"),
        _event("copilot-1", "copilot_action", agent_id="agent-1", outcome="completed", count=2),
        _event("copilot-2", "copilot_action", agent_id="agent-1", outcome="blocked"),
        _event("subagent-1", "subagent_disagreement", agent_id="agent-1", outcome="agreement"),
        _event(
            "subagent-2",
            "subagent_disagreement",
            agent_id="agent-1",
            outcome="disagreement",
        ),
        _event("codex-1", "codex_task", agent_id="agent-1", outcome="success", count=3),
        _event("codex-2", "codex_task", agent_id="agent-1", outcome="failed"),
    ]


def _event(
    event_id: str,
    event_type: str,
    *,
    occurred_at: datetime | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    project_id: str = "project-1",
    org_id: str = "org-1",
    count: int = 1,
    value: float = 0.0,
    autonomy_level: str | None = None,
    outcome: str | None = None,
    risk_level: str | None = None,
) -> GovernanceAuditEvent:
    return GovernanceAuditEvent.model_validate(
        {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at or NOW + timedelta(hours=1),
            "agent_id": agent_id,
            "role": role,
            "org_id": org_id,
            "project_id": project_id,
            "count": count,
            "value": value,
            "autonomy_level": autonomy_level,
            "outcome": outcome,
            "risk_level": risk_level,
        }
    )
