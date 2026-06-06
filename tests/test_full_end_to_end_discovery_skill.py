from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.runtime_agents.skills import (
    expand_skill_to_plan,
    get_runtime_skill,
)
from molecule_ranker.runtime_agents.skills.full_end_to_end_discovery import (
    FullEndToEndDiscoverySkillRequest,
    run_full_end_to_end_discovery_skill,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_full_end_to_end_discovery_runtime_skill_expands_with_approval_gates() -> None:
    skill = get_runtime_skill("full_end_to_end_discovery")

    plan = expand_skill_to_plan(
        skill,
        session_id="session-1",
        user_goal="Run a governed end-to-end discovery workflow.",
        inputs={
            "disease_name": "Example disease",
            "project_id": "project-1",
            "mode": "mocked",
        },
        user_permissions=set(skill.required_permissions),
    )

    assert plan.validated is True
    assert plan.metadata["runtime_skill"]["skill_name"] == "full_end_to_end_discovery"
    assert "generated_molecule_export" in plan.required_approvals
    assert "stage_gate" in plan.required_approvals
    assert [step.action_type for step in plan.steps] == [
        "create_or_select_project",
        "ranking",
        "literature",
        "developability",
        "generation",
        "structure_if_configured",
        "model_if_configured",
        "graph_build",
        "hypothesis_generation",
        "portfolio_optimization",
        "campaign_planning",
        "review_workspace",
        "evaluation",
        "result_bundle",
    ]


def test_mocked_full_end_to_end_discovery_skill_succeeds() -> None:
    result = run_full_end_to_end_discovery_skill(
        FullEndToEndDiscoverySkillRequest(
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        ),
        now=lambda: NOW,
    )

    assert result.status == "succeeded"
    assert result.workflow_result is not None
    assert result.workflow_result.workflow.workflow_type == "full_discovery_loop"
    assert result.bundle is not None
    assert result.external_writes_performed == 0
    assert result.bundle.metadata["mode"] == "mocked"


def test_dry_run_full_end_to_end_discovery_skill_has_no_external_writes() -> None:
    result = run_full_end_to_end_discovery_skill(
        FullEndToEndDiscoverySkillRequest(
            mode="dry_run",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
            requested_external_write=True,
        ),
        now=lambda: NOW,
    )

    assert result.status == "succeeded"
    assert result.external_writes_performed == 0
    assert result.planned_external_writes == 1
    assert result.metadata["dry_run_external_write_simulated"] is True


def test_write_mode_full_end_to_end_discovery_skill_needs_approval() -> None:
    result = run_full_end_to_end_discovery_skill(
        FullEndToEndDiscoverySkillRequest(
            mode="write_approved_live",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
            requested_external_write=True,
        ),
        now=lambda: NOW,
    )

    assert result.status == "awaiting_approval"
    assert result.workflow_result is None
    assert result.external_writes_performed == 0
    assert "external_write" in result.required_approvals
