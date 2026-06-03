from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

BUILD_GRAPH_AND_HYPOTHESES = RuntimeSkillSpec(
    skill_name="build_graph_and_hypotheses",
    description="Build the knowledge graph, detect issues, and generate ranked hypotheses.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "source_artifact_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="build_graph",
            tool_name="build_graph",
            expected_outputs=["knowledge_graph"],
        ),
        RuntimeSkillStepTemplate(
            action_type="detect_contradictions",
            tool_name="detect_contradictions",
            expected_outputs=["contradiction_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="generate_hypotheses",
            tool_name="generate_hypotheses",
            expected_outputs=["hypotheses"],
        ),
        RuntimeSkillStepTemplate(
            action_type="rank_hypotheses",
            tool_name="rank_hypotheses",
            expected_outputs=["ranked_hypotheses"],
        ),
        RuntimeSkillStepTemplate(
            action_type="create_research_questions",
            tool_name="create_research_questions",
            expected_outputs=["research_questions"],
        ),
    ],
    required_tools=[
        "build_graph",
        "detect_contradictions",
        "generate_hypotheses",
        "rank_hypotheses",
        "create_research_questions",
    ],
    required_permissions=[
        "graph:build",
        "graph:read",
        "hypotheses:generate",
        "hypotheses:rank",
        "hypotheses:write",
    ],
    approval_requirements=[],
    expected_artifacts=[
        "knowledge_graph",
        "contradiction_report",
        "hypotheses",
        "ranked_hypotheses",
        "research_questions",
    ],
    guardrails=[
        "Graph edges and hypotheses must preserve provenance.",
        "Hypotheses are not EvidenceItem records.",
        "Contradictions must be surfaced, not hidden.",
    ],
)

OPTIMIZE_PORTFOLIO_AND_CAMPAIGN = RuntimeSkillSpec(
    skill_name="optimize_portfolio_and_campaign",
    description="Build portfolio candidates, optimize scenarios, and prepare campaign planning.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "campaign_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="build_portfolio_candidates",
            tool_name="build_portfolio_candidates",
            expected_outputs=["portfolio_candidates"],
        ),
        RuntimeSkillStepTemplate(
            action_type="optimize_portfolio",
            tool_name="optimize_portfolio",
            expected_outputs=["portfolio_optimization"],
        ),
        RuntimeSkillStepTemplate(
            action_type="run_scenarios",
            tool_name="run_scenarios",
            expected_outputs=["portfolio_scenarios"],
        ),
        RuntimeSkillStepTemplate(
            action_type="plan_campaign",
            tool_name="plan_campaign",
            approval_requirements=["campaign_advance", "stage_gate"],
            expected_outputs=["campaign_plan"],
        ),
    ],
    required_tools=[
        "build_portfolio_candidates",
        "optimize_portfolio",
        "run_scenarios",
        "plan_campaign",
    ],
    required_permissions=[
        "portfolio:run",
        "campaign:plan",
    ],
    approval_requirements=[],
    expected_artifacts=[
        "portfolio_candidates",
        "portfolio_optimization",
        "portfolio_scenarios",
        "campaign_plan",
    ],
    guardrails=[
        "Portfolio optimization cannot approve stage gates.",
        "Campaign advancement requires human approval.",
        "Generated molecule follow-up requires review gates.",
    ],
)

__all__ = ["BUILD_GRAPH_AND_HYPOTHESES", "OPTIMIZE_PORTFOLIO_AND_CAMPAIGN"]
