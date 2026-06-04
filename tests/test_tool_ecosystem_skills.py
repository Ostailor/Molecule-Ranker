from __future__ import annotations

import pytest

from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2
from molecule_ranker.tool_ecosystem.skills import (
    SkillPackExpansionError,
    expand_skill_to_plan,
    get_builtin_skill,
    get_builtin_skill_pack,
    list_builtin_skill_packs,
    validate_skill_pack,
)


def test_builtin_skill_manifests_are_valid() -> None:
    registry = ToolRegistryV2.default()
    packs = list_builtin_skill_packs()

    assert {pack.name for pack in packs} == {
        "discovery_basics",
        "generation_triage",
        "review_and_handoff",
        "experiment_feedback",
        "graph_hypothesis_campaign",
        "eval_and_readiness",
    }
    for pack in packs:
        validate_skill_pack(pack, registry=registry)
        assert pack.metadata["deterministic_expansion"] is True
        assert pack.required_tools
        assert pack.guardrails
        for raw_skill in pack.skills:
            assert raw_skill["input_schema"]["type"] == "object"
            assert raw_skill["output_artifacts"]
            assert raw_skill["required_tools"]
            assert raw_skill["required_permissions"]
            assert raw_skill["guardrails"]


def test_skill_expands_to_valid_runtime_action_plan() -> None:
    registry = ToolRegistryV2.default()
    skill = get_builtin_skill("rank_disease")

    plan = expand_skill_to_plan(
        skill,
        session_id="session-1",
        user_goal="rank disease candidates",
        inputs={"disease": "asthma", "project_id": "project-1"},
        registry=registry,
    )

    assert isinstance(plan, RuntimeActionPlan)
    assert plan.validated is True
    assert plan.created_by == "deterministic_template"
    assert plan.expected_artifacts == ["ranking_artifact", "ranking_summary"]
    assert [step.tool_name for step in plan.steps] == [
        "builtins.ranking.run_ranking",
        "builtins.ranking.summarize_ranking",
    ]
    assert plan.metadata["codex_selected_skill"] is True
    assert set(plan.metadata["tool_specs"]) == {
        "builtins.ranking.run_ranking",
        "builtins.ranking.summarize_ranking",
    }


def test_approval_requirements_are_preserved_in_expanded_plan() -> None:
    registry = ToolRegistryV2.default()

    plan = expand_skill_to_plan(
        "campaign",
        session_id="session-2",
        user_goal="prepare campaign plan",
        inputs={"project_id": "project-1", "campaign_id": "campaign-1"},
        registry=registry,
    )

    assert plan.risk_level == "high"
    assert plan.required_approvals == ["campaign_advance", "stage_gate"]
    assert plan.steps[0].requires_approval is True
    assert plan.steps[0].metadata["approval_gates"] == ["campaign_advance", "stage_gate"]


def test_unavailable_tool_blocks_skill_expansion() -> None:
    registry = ToolRegistryV2.default()
    registry.disable_tool("builtins.ranking.run_ranking")

    with pytest.raises(SkillPackExpansionError, match="requires unavailable tool"):
        expand_skill_to_plan(
            "rank_disease",
            session_id="session-3",
            user_goal="rank disease candidates",
            registry=registry,
        )


def test_skill_pack_lookup_and_validation() -> None:
    pack = get_builtin_skill_pack("generation_triage")

    validate_skill_pack(pack)

    assert pack.skill_pack_id == "builtins.skill_pack.generation_triage"
    assert {skill["skill_id"] for skill in pack.skills} == {
        "design_plan",
        "generation",
        "developability",
        "experiment_readiness",
    }
