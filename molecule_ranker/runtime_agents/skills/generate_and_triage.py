from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

SKILL = RuntimeSkillSpec(
    skill_name="generate_and_triage",
    description="Run the generation pipeline and triage generated hypotheses for developability.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "generation_objective_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="generation",
            tool_name="run_generation",
            expected_outputs=["generated_molecule_hypotheses"],
        ),
        RuntimeSkillStepTemplate(
            action_type="developability",
            tool_name="run_developability",
            expected_outputs=["developability_artifact"],
        ),
        RuntimeSkillStepTemplate(
            action_type="developability_summary",
            tool_name="assess_developability_artifact",
            expected_outputs=["developability_summary"],
        ),
        RuntimeSkillStepTemplate(
            action_type="review_workspace",
            tool_name="create_review_workspace",
            expected_outputs=["review_workspace"],
        ),
    ],
    required_tools=[
        "run_generation",
        "run_developability",
        "assess_developability_artifact",
        "create_review_workspace",
    ],
    required_permissions=[
        "generation:run",
        "developability:run",
        "developability:read",
        "review:write",
    ],
    approval_requirements=[],
    expected_artifacts=[
        "generated_molecule_hypotheses",
        "developability_artifact",
        "developability_summary",
        "review_workspace",
    ],
    guardrails=[
        "Generated molecules remain computational hypotheses.",
        "Do not claim generated molecules are active, safe, binding, or synthesizable.",
        "Generated molecules cannot advance to assay or campaign without review gates.",
    ],
)

__all__ = ["SKILL"]
