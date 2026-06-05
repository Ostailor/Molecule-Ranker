from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from molecule_ranker.agent_governance import AgentAutonomyBudget, AgentRunControl
from molecule_ranker.agent_governance.budgets import BudgetImpact
from molecule_ranker.agent_governance.simulator import (
    AgentPolicySimulationRequest,
    simulate_agent_action,
)
from molecule_ranker.cli import app

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_external_write_simulation_approval_required() -> None:
    result = simulate_agent_action(
        AgentPolicySimulationRequest(
            agent_id="agent-1",
            tool="run_external_sync_write",
            action="run_external_sync_write",
            autonomy_level="execute_with_approval",
            tool_category="integration",
            side_effect_level="external_write",
        )
    )

    assert result.status == "approval_required"
    assert result.allowed is False
    assert result.approval_required is True
    assert "external_write" in result.required_approvals
    assert "integration:write" in result.required_permissions


def test_generated_advancement_simulation_approval_required() -> None:
    result = simulate_agent_action(
        AgentPolicySimulationRequest(
            agent_id="agent-1",
            tool="advance_generated_molecule_to_assay",
            action="advance_generated_molecule_to_assay",
            autonomy_level="execute_with_approval",
            tool_category="campaign",
            metadata={"generated_molecule_advancement": True},
        )
    )

    assert result.status == "approval_required"
    assert result.blocked_reasons == []
    assert "generated_molecule_human_review" in result.required_approvals
    assert "campaign:approve" in result.required_permissions


def test_kill_switch_simulation_blocked() -> None:
    result = simulate_agent_action(
        AgentPolicySimulationRequest(
            agent_id="agent-1",
            tool="run_ranking",
            project_id="project-1",
            run_controls=[
                AgentRunControl(
                    control_id="kill-1",
                    org_id=None,
                    project_id="project-1",
                    agent_id=None,
                    control_type="kill_switch",
                    reason="Emergency stop.",
                    applied_by="admin-1",
                    applied_at=NOW,
                    expires_at=None,
                    active=True,
                    metadata={"session_action": "cancel"},
                )
            ],
        )
    )

    assert result.status == "blocked"
    assert result.allowed is False
    assert any("Kill switch active" in reason for reason in result.blocked_reasons)


def test_budget_exceeded_simulation_blocked() -> None:
    result = simulate_agent_action(
        AgentPolicySimulationRequest(
            agent_id="agent-1",
            tool="run_ranking",
            org_id="org-1",
            project_id="project-1",
            budget_impact=BudgetImpact(tool_calls=1),
            budgets=[
                _budget(
                    max_tool_calls=1,
                    current_usage={"tool_calls": 1},
                )
            ],
        )
    )

    assert result.status == "blocked"
    assert result.allowed is False
    assert "Budget limit would be exceeded." in result.blocked_reasons
    assert any(
        item["step"] == "budget" and item["exceeded_dimensions"] == ["tool_calls"]
        for item in result.policy_trace
    )


def test_governance_simulate_cli_outputs_decision() -> None:
    result = CliRunner().invoke(
        app,
        [
            "governance",
            "simulate",
            "--agent-id",
            "agent-1",
            "--tool",
            "run_external_sync_write",
            "--action",
            "run_external_sync_write",
            "--side-effect-level",
            "external_write",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "approval_required"
    assert "external_write" in payload["required_approvals"]


def _budget(
    *,
    max_tool_calls: int | None = None,
    current_usage: dict[str, object] | None = None,
) -> AgentAutonomyBudget:
    return AgentAutonomyBudget(
        budget_id="budget-1",
        org_id="org-1",
        project_id="project-1",
        campaign_id=None,
        agent_id="agent-1",
        period="daily",
        max_tool_calls=max_tool_calls,
        max_codex_tasks=None,
        max_runtime_minutes=None,
        max_artifact_writes=None,
        max_db_writes=None,
        max_external_reads=None,
        max_external_writes=0,
        max_generation_jobs=None,
        max_docking_jobs=None,
        max_model_training_jobs=None,
        max_campaign_replans=None,
        max_cost_units=None,
        current_usage=current_usage or {},
        reset_at=None,
        enabled=True,
        metadata={},
    )

