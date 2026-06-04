from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.runtime_agents.multi_agent import (
    HUMAN_ONLY_APPROVALS,
    MultiAgentScientificOrchestrator,
    SpecialistAgentOutput,
    SpecialistAgentRegistry,
    validate_multi_agent_output_schema,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_v23_specialist_roster_contains_required_operational_roles() -> None:
    registry = SpecialistAgentRegistry()

    kinds = {agent.kind for agent in registry.list_agents()}

    assert kinds == {
        "program_management",
        "evidence_review",
        "molecule_design",
        "developability_safety",
        "experimental_feedback",
        "predictive_modeling",
        "structure_workflow_review",
        "knowledge_graph_reasoning",
        "hypothesis_generation",
        "portfolio_campaign_planning",
        "integration_operations",
        "evaluation_validation",
        "guardrail_safety_review",
        "platform_operations",
    }
    assert all(agent.output_schema["type"] == "object" for agent in registry.list_agents())
    assert all(
        set(HUMAN_ONLY_APPROVALS).issubset(agent.human_only_approval_types)
        for agent in registry.list_agents()
    )


def test_specialist_delegation_scopes_artifacts_tools_policy_and_audit() -> None:
    registry = RuntimeToolRegistry.default()
    orchestrator = MultiAgentScientificOrchestrator(tool_registry=registry)

    delegation = orchestrator.delegate_task(
        specialist_id="evidence-reviewer",
        objective="Review ranking evidence and literature artifacts.",
        session_id="session-v23",
        delegated_by="program-manager",
        current_artifacts=[{"artifact_id": "ranking-artifact-1", "kind": "ranking"}],
        scoped_artifact_ids=["ranking-artifact-1"],
        requested_tool_names=["summarize_literature"],
        user_permissions=_all_permissions(registry),
    )

    assert delegation.task.status == "planned"
    assert delegation.task.scoped_artifact_ids == ["ranking-artifact-1"]
    assert delegation.plan.validated is True
    assert delegation.plan.steps[0].tool_name == "summarize_literature"
    assert delegation.plan.steps[0].tool_args["artifact_id"] == "ranking-artifact-1"
    assert delegation.plan.metadata["specialist_agent"]["agent_id"] == "evidence-reviewer"
    assert delegation.plan.metadata["sandbox_profile"] == "read_only_runtime"
    assert delegation.audit_events[0].event_type == "specialist_task_delegated"


def test_specialist_delegation_rejects_unknown_artifacts_and_disallowed_tools() -> None:
    registry = RuntimeToolRegistry.default()
    orchestrator = MultiAgentScientificOrchestrator(tool_registry=registry)

    with pytest.raises(ValueError, match="unknown scoped artifacts"):
        orchestrator.delegate_task(
            specialist_id="evidence-reviewer",
            objective="Review evidence.",
            session_id="session-v23",
            delegated_by="program-manager",
            current_artifacts=[{"artifact_id": "known"}],
            scoped_artifact_ids=["missing"],
            user_permissions=_all_permissions(registry),
        )

    with pytest.raises(ValueError, match="cannot use requested tools"):
        orchestrator.delegate_task(
            specialist_id="evidence-reviewer",
            objective="Review evidence.",
            session_id="session-v23",
            delegated_by="program-manager",
            requested_tool_names=["run_sync_write_enabled"],
            user_permissions=_all_permissions(registry),
        )


def test_specialist_execution_preserves_approval_escalation_for_external_writes() -> None:
    registry = RuntimeToolRegistry.default()
    orchestrator = MultiAgentScientificOrchestrator(
        tool_registry=registry,
        tool_handlers={
            spec.tool_name: _tool_handler
            for spec in registry.list_tools()
            if spec.category != "codex"
        },
    )
    delegation = orchestrator.delegate_task(
        specialist_id="integration-operator",
        objective="Run an integration write sync.",
        session_id="session-v23",
        delegated_by="program-manager",
        requested_tool_names=["run_sync_write_enabled"],
        user_permissions=_all_permissions(registry),
    )

    result = orchestrator.execute_delegation(delegation, mode="execute_with_approval", actor="user")

    assert result.execution is not None
    assert result.execution.status == "approval_required"
    assert result.task.status == "awaiting_human_review"
    assert result.output is not None
    assert result.output.escalation_required is True
    assert result.escalations
    assert result.escalations[0].metadata["subagent_cannot_self_approve"] is True


def test_specialist_output_schema_and_peer_critique_guardrails() -> None:
    orchestrator = MultiAgentScientificOrchestrator()
    unsafe = SpecialistAgentOutput(
        task_id="task-1",
        specialist_id="molecule-designer",
        summary="PMID:123456 proves this molecule is active and safe.",
        grounded_artifact_ids=["artifact-1"],
        findings=[],
        recommendations=[],
        limitations=[],
    )

    payload = validate_multi_agent_output_schema(unsafe)
    critique = orchestrator.critique_output(
        reviewer_specialist_id="guardrail-safety-reviewer",
        output=unsafe,
        scoped_artifact_ids=["artifact-1"],
    )

    assert payload["summary"].startswith("PMID")
    assert critique.verdict == "escalate_human"
    assert critique.required_human_review is True
    assert any("fake citation" in issue.lower() for issue in critique.issues)


def test_agent_specialist_cli_commands(tmp_path: Path) -> None:
    runner = CliRunner()

    roster = runner.invoke(app, ["agent", "specialists", "--json"])

    assert roster.exit_code == 0, roster.output
    specialists = json.loads(roster.output)["specialists"]
    assert any(item["agent_id"] == "evidence-reviewer" for item in specialists)

    output_dir = tmp_path / "delegate"
    delegated = runner.invoke(
        app,
        [
            "agent",
            "delegate",
            "--specialist-id",
            "evidence-reviewer",
            "--goal",
            "Review ranking evidence",
            "--artifact-id",
            "ranking-artifact-1",
            "--tool-name",
            "summarize_literature",
            "--autonomy",
            "dry_run",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert delegated.exit_code == 0, delegated.output
    payload = json.loads(delegated.output)
    assert payload["status"] == "planned"
    assert (output_dir / "specialist_delegation_task.json").exists()
    assert (output_dir / "runtime_action_plan.json").exists()
    assert json.loads((output_dir / "runtime_audit_log.json").read_text())[
        0
    ]["event_type"] == "specialist_task_delegated"


def _all_permissions(registry: RuntimeToolRegistry) -> set[str]:
    return {
        permission
        for spec in registry.list_tools()
        for permission in spec.required_permissions
    }


def _tool_handler(step, spec):  # type: ignore[no-untyped-def]
    return {
        "status": "succeeded",
        "output": {"summary": f"{step.tool_name} completed."},
        "artifact_ids": [f"artifact-{step.tool_name}"]
        if spec.side_effect_level == "artifact_write"
        else [],
    }
