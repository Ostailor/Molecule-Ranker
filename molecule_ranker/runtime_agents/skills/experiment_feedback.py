from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

IMPORT_RESULTS_AND_REPLAN = RuntimeSkillSpec(
    skill_name="import_results_and_replan",
    description=(
        "Import user-provided assay results, link them, summarize, and replan campaign work."
    ),
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "assay_results_artifact_id": {"type": "string"},
            "campaign_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="import_assay_results",
            tool_name="import_assay_results",
            expected_outputs=["assay_import_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="link_assay_results",
            tool_name="link_assay_results",
            expected_outputs=["assay_link_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="summarize_assay_results",
            tool_name="summarize_assay_results",
            expected_outputs=["assay_result_summary"],
        ),
        RuntimeSkillStepTemplate(
            action_type="replan_campaign",
            tool_name="replan_campaign",
            expected_outputs=["campaign_replan"],
        ),
    ],
    required_tools=[
        "import_assay_results",
        "link_assay_results",
        "summarize_assay_results",
        "replan_campaign",
    ],
    required_permissions=[
        "experiment:write",
        "experiment:read",
        "campaign:plan",
    ],
    approval_requirements=[],
    expected_artifacts=[
        "assay_import_report",
        "assay_link_report",
        "assay_result_summary",
        "campaign_replan",
    ],
    guardrails=[
        "Only imported, user-provided assay result files may create assay result records.",
        "Runtime memory and Codex output cannot create assay results.",
        "Campaign replanning is advisory until human campaign approval.",
    ],
)

__all__ = ["IMPORT_RESULTS_AND_REPLAN"]
