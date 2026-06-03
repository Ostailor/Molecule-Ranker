from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

SKILL = RuntimeSkillSpec(
    skill_name="rank_and_review",
    description="Run source-backed ranking, summarize results, and create a review workspace.",
    input_schema=_object_schema(
        {
            "disease": {"type": "string"},
            "project_id": {"type": "string"},
            "source_artifact_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="ranking",
            tool_name="run_ranking",
            expected_outputs=["ranking_artifact"],
        ),
        RuntimeSkillStepTemplate(
            action_type="ranking_summary",
            tool_name="summarize_ranking",
            expected_outputs=["ranking_summary"],
        ),
        RuntimeSkillStepTemplate(
            action_type="review_workspace",
            tool_name="create_review_workspace",
            expected_outputs=["review_workspace"],
        ),
    ],
    required_tools=["run_ranking", "summarize_ranking", "create_review_workspace"],
    required_permissions=["run:create", "run:read", "review:write"],
    approval_requirements=[],
    expected_artifacts=["ranking_artifact", "ranking_summary", "review_workspace"],
    guardrails=[
        "Ranking must be source-backed.",
        "Review workspace is for expert assessment, not automatic approval.",
        "Codex output is not biomedical evidence.",
    ],
)

__all__ = ["SKILL"]
