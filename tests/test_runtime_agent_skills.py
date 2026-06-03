from __future__ import annotations

import pytest

from molecule_ranker.runtime_agents.skills import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    RuntimeSkillValidationError,
    default_runtime_skills,
    expand_skill_to_plan,
    get_runtime_skill,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

EXPECTED_SKILLS = {
    "rank_and_review",
    "generate_and_triage",
    "import_results_and_replan",
    "build_graph_and_hypotheses",
    "optimize_portfolio_and_campaign",
    "run_evaluation_suite",
    "diagnose_failed_job",
    "generate_support_bundle",
    "integration_dry_run_sync",
}


def test_runtime_skill_schemas_are_valid() -> None:
    registry = RuntimeToolRegistry.default()
    skills = default_runtime_skills()

    assert set(skills) == EXPECTED_SKILLS
    for skill in skills.values():
        assert skill.skill_name in EXPECTED_SKILLS
        assert skill.description
        assert skill.input_schema["type"] == "object"
        assert skill.default_plan_template
        assert skill.required_tools
        assert skill.required_permissions
        assert skill.expected_artifacts
        assert skill.guardrails
        for tool_name in skill.required_tools:
            assert registry.get(tool_name) is not None


def test_skill_expansion_works() -> None:
    skill = get_runtime_skill("rank_and_review")

    plan = expand_skill_to_plan(
        skill,
        session_id="session-1",
        user_goal="Rank Alzheimer disease and create a review workspace.",
        inputs={"disease": "Alzheimer disease", "project_id": "project-1"},
        user_permissions=set(skill.required_permissions),
    )

    assert plan.validated is True
    assert plan.created_by == "deterministic_template"
    assert [step.tool_name for step in plan.steps] == [
        "run_ranking",
        "summarize_ranking",
        "create_review_workspace",
    ]
    assert plan.metadata["runtime_skill"]["skill_name"] == "rank_and_review"
    assert "run_ranking" in plan.metadata["tool_specs"]


def test_skill_approval_requirements_are_preserved() -> None:
    skill = get_runtime_skill("generate_support_bundle")

    plan = expand_skill_to_plan(
        skill,
        session_id="session-1",
        user_goal="Generate support bundle for failed runtime session.",
        user_permissions=set(skill.required_permissions),
    )

    assert "support_bundle_logs" in plan.required_approvals
    assert plan.steps[0].requires_approval is True
    assert plan.steps[0].approval_reason == "Skill requires support_bundle_logs approval."


def test_unsafe_skill_is_blocked() -> None:
    unsafe = RuntimeSkillSpec(
        skill_name="unsafe_sync",
        description="Unsafe external write without approval.",
        input_schema={"type": "object", "additionalProperties": True},
        default_plan_template=[
            RuntimeSkillStepTemplate(
                action_type="run_sync_write_enabled",
                tool_name="run_sync_write_enabled",
                tool_args={},
                expected_outputs=["sync_write"],
            )
        ],
        required_tools=["run_sync_write_enabled"],
        required_permissions=["integration:write"],
        approval_requirements=[],
        expected_artifacts=["sync_write"],
        guardrails=["External writes require approval."],
    )

    with pytest.raises(RuntimeSkillValidationError, match="external write"):
        expand_skill_to_plan(
            unsafe,
            session_id="session-1",
            user_goal="Unsafe sync write.",
            user_permissions={"integration:write"},
        )
