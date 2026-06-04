from __future__ import annotations

from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import MultiAgentSession
from molecule_ranker.subagents.skills import (
    builtin_multi_agent_skills,
    expand_multi_agent_skill,
    get_multi_agent_skill,
)

EXPECTED_SKILLS = {
    "diagnose_project",
    "improve_generated_candidates",
    "analyze_failed_campaign",
    "prepare_review_packet",
    "evaluate_platform_performance",
    "integration_sync_review",
    "end_to_end_discovery_ops",
}
HIGH_RISK_SKILLS = {
    "improve_generated_candidates",
    "analyze_failed_campaign",
    "integration_sync_review",
    "end_to_end_discovery_ops",
}
GUARDRAIL_SENTINEL_SKILLS = EXPECTED_SKILLS - {"evaluate_platform_performance"}


def test_skills_expand_to_valid_multi_agent_sessions() -> None:
    skills = builtin_multi_agent_skills()
    registry = SubagentRegistry()

    assert set(skills) == EXPECTED_SKILLS
    for skill_name, skill in skills.items():
        session = expand_multi_agent_skill(
            skill_name,
            user_goal=f"Run {skill_name}.",
            parent_session_id=f"session-{skill_name}",
            registry=registry,
        )

        assert isinstance(session, MultiAgentSession)
        assert session.multi_agent_session_id == f"session-{skill_name}"
        assert session.metadata["skill_name"] == skill_name
        assert [task.assigned_subagent_id for task in session.tasks] == (
            skill.required_subagent_ids
        )
        assert len(session.messages) == len(skill.required_subagent_ids)
        assert session.consensus
        expanded_tools = {
            tool for task in session.tasks for tool in task.allowed_tool_names
        }
        assert set(skill.required_tools).issubset(expanded_tools)
        for task in session.tasks:
            assert task.input_artifact_ids == skill.expected_artifacts
            assert task.expected_output_schema["type"] == "object"


def test_high_risk_skills_require_approvals() -> None:
    skills = builtin_multi_agent_skills()

    for skill_name in HIGH_RISK_SKILLS:
        skill = skills[skill_name]
        session = skill.expand_to_session(parent_session_id=f"session-{skill_name}")

        assert skill.risk_level == "high"
        assert skill.approval_requirements
        assert session.consensus[0].human_review_required is True
        assert all(task.requires_human_approval for task in session.tasks)


def test_guardrail_sentinel_included_where_required() -> None:
    skills = builtin_multi_agent_skills()

    for skill_name in GUARDRAIL_SENTINEL_SKILLS:
        skill = skills[skill_name]
        session = skill.expand_to_session(parent_session_id=f"session-{skill_name}")

        assert "guardrail-sentinel" in skill.required_subagent_ids
        assert "guardrail-sentinel" in session.subagent_ids
        assert any(
            task.assigned_subagent_id == "guardrail-sentinel"
            and "run_guardrail_benchmark" in task.allowed_tool_names
            for task in session.tasks
        )

    platform_skill = get_multi_agent_skill("evaluate_platform_performance")
    assert "guardrail-sentinel" not in platform_skill.required_subagent_ids
