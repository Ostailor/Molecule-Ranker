from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.subagents.coordinator import TOOL_CATALOG
from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentMessage,
    SubagentTask,
    TaskRiskLevel,
)

MultiAgentSkillName = Literal[
    "diagnose_project",
    "improve_generated_candidates",
    "analyze_failed_campaign",
    "prepare_review_packet",
    "evaluate_platform_performance",
    "integration_sync_review",
    "end_to_end_discovery_ops",
]

DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "findings", "recommended_next_actions"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array"},
        "recommended_next_actions": {"type": "array"},
    },
}


class MultiAgentSkill(BaseModel):
    skill_name: MultiAgentSkillName
    description: str
    required_subagent_ids: list[str]
    required_tools: list[str]
    expected_artifacts: list[str]
    approval_requirements: list[str]
    guardrail_checks: list[str]
    human_review_points: list[str]
    risk_level: TaskRiskLevel
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def requires_approval(self) -> bool:
        return bool(self.approval_requirements)

    @property
    def requires_guardrail_sentinel(self) -> bool:
        return "guardrail-sentinel" in self.required_subagent_ids

    def expand_to_session(
        self,
        *,
        user_goal: str | None = None,
        parent_session_id: str | None = None,
        registry: SubagentRegistry | None = None,
    ) -> MultiAgentSession:
        active_registry = registry or SubagentRegistry()
        session_id = parent_session_id or f"multi-agent-skill-session-{uuid4().hex[:12]}"
        started_at = _now()
        supervisor_id = (
            "program-manager"
            if "program-manager" in self.required_subagent_ids
            else self.required_subagent_ids[0]
        )
        tasks = [
            _task_for_subagent(
                skill=self,
                session_id=session_id,
                subagent_id=subagent_id,
                index=index,
                registry=active_registry,
            )
            for index, subagent_id in enumerate(self.required_subagent_ids)
        ]
        messages = [
            _task_request_message(
                session_id=session_id,
                supervisor_id=supervisor_id,
                task=task,
            )
            for task in tasks
        ]
        consensus = _initial_consensus(self, session_id=session_id, tasks=tasks)
        return MultiAgentSession(
            multi_agent_session_id=session_id,
            runtime_session_id=None,
            user_goal=user_goal or self.description,
            supervisor_subagent_id=supervisor_id,
            subagent_ids=list(dict.fromkeys([supervisor_id, *self.required_subagent_ids])),
            tasks=tasks,
            messages=messages,
            results=[],
            critiques=[],
            consensus=[consensus],
            status="queued",
            started_at=started_at,
            completed_at=None,
            metadata={
                "skill_name": self.skill_name,
                "required_tools": self.required_tools,
                "expected_artifacts": self.expected_artifacts,
                "approval_requirements": self.approval_requirements,
                "guardrail_checks": self.guardrail_checks,
                "human_review_points": self.human_review_points,
                "risk_level": self.risk_level,
            },
        )


def builtin_multi_agent_skills() -> dict[str, MultiAgentSkill]:
    return {skill.skill_name: skill for skill in _BUILTIN_SKILLS}


def get_multi_agent_skill(skill_name: str) -> MultiAgentSkill:
    skills = builtin_multi_agent_skills()
    try:
        return skills[skill_name]
    except KeyError as exc:
        raise KeyError(f"unknown multi-agent skill: {skill_name}") from exc


def expand_multi_agent_skill(
    skill_name: str,
    *,
    user_goal: str | None = None,
    parent_session_id: str | None = None,
    registry: SubagentRegistry | None = None,
) -> MultiAgentSession:
    return get_multi_agent_skill(skill_name).expand_to_session(
        user_goal=user_goal,
        parent_session_id=parent_session_id,
        registry=registry,
    )


def _task_for_subagent(
    *,
    skill: MultiAgentSkill,
    session_id: str,
    subagent_id: str,
    index: int,
    registry: SubagentRegistry,
) -> SubagentTask:
    profile = registry.require(subagent_id)
    allowed_tools = _tools_for_profile(profile.allowed_tool_categories, skill.required_tools)
    forbidden_tools = _tools_for_profile(profile.denied_tool_categories, skill.required_tools)
    if not allowed_tools:
        raise ValueError(
            f"skill {skill.skill_name} has no scoped tools for subagent {subagent_id}"
        )
    return SubagentTask(
        task_id=f"{skill.skill_name}-task-{index + 1}",
        parent_session_id=session_id,
        assigned_subagent_id=subagent_id,
        task_type=f"{skill.skill_name}:{profile.role}",
        objective=_objective_for_subagent(skill, profile.name),
        input_artifact_ids=skill.expected_artifacts,
        allowed_tool_names=allowed_tools,
        forbidden_tool_names=forbidden_tools,
        expected_output_schema=DEFAULT_OUTPUT_SCHEMA,
        required_outputs=["summary", "findings", "recommended_next_actions"],
        risk_level=skill.risk_level,
        requires_human_approval=skill.requires_approval,
        status="queued",
        created_at=_now(),
        started_at=None,
        completed_at=None,
        metadata={
            "skill_name": skill.skill_name,
            "approval_requirements": skill.approval_requirements,
            "guardrail_checks": skill.guardrail_checks,
            "human_review_points": skill.human_review_points,
        },
    )


def _task_request_message(
    *,
    session_id: str,
    supervisor_id: str,
    task: SubagentTask,
) -> SubagentMessage:
    return SubagentMessage(
        message_id=f"subagent-message-{uuid4().hex[:12]}",
        parent_session_id=session_id,
        from_subagent_id=supervisor_id,
        to_subagent_id=task.assigned_subagent_id,
        message_type="task_request",
        content=task.objective,
        referenced_artifact_ids=task.input_artifact_ids,
        referenced_entity_ids=[],
        referenced_tool_names=task.allowed_tool_names,
        created_at=_now(),
        metadata={"skill_name": task.metadata["skill_name"], "task_id": task.task_id},
    )


def _initial_consensus(
    skill: MultiAgentSkill,
    *,
    session_id: str,
    tasks: list[SubagentTask],
) -> SubagentConsensus:
    return SubagentConsensus(
        consensus_id=f"subagent-consensus-{uuid4().hex[:12]}",
        parent_session_id=session_id,
        task_ids=[task.task_id for task in tasks],
        participating_subagent_ids=skill.required_subagent_ids,
        consensus_status="requires_human_review" if skill.requires_approval else "inconclusive",
        summary="Skill expansion queued; execution and critique pending.",
        agreements=[],
        disagreements=[],
        recommended_next_actions=["Execute skill tasks with scoped subagent runtime."],
        human_review_required=skill.requires_approval,
        metadata={
            "skill_name": skill.skill_name,
            "approval_requirements": skill.approval_requirements,
            "guardrail_checks": skill.guardrail_checks,
        },
    )


def _tools_for_profile(categories: list[str], required_tools: list[str]) -> list[str]:
    category_tools: list[str] = []
    for category in categories:
        category_tools.extend(TOOL_CATALOG.get(category, []))
    return list(dict.fromkeys(tool for tool in required_tools if tool in set(category_tools)))


def _objective_for_subagent(skill: MultiAgentSkill, profile_name: str) -> str:
    return f"{profile_name} executes {skill.skill_name}: {skill.description}"


def _now() -> datetime:
    return datetime.now(UTC)


_BUILTIN_SKILLS: list[MultiAgentSkill] = [
    MultiAgentSkill(
        skill_name="diagnose_project",
        description="Diagnose project readiness, failed jobs, support signals, and summary risk.",
        required_subagent_ids=["platform-operator", "program-manager", "guardrail-sentinel"],
        required_tools=[
            "run_readiness",
            "run_release_check",
            "generate_support_bundle",
            "list_jobs",
            "show_job",
            "summarize_artifacts",
            "draft_report",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=["project-summary", "job-summary", "support-summary"],
        approval_requirements=["support_bundle_logs"],
        guardrail_checks=["operational_reliability", "secret_redaction", "policy_boundary"],
        human_review_points=["before sharing support bundle outside project boundary"],
        risk_level="medium",
    ),
    MultiAgentSkill(
        skill_name="improve_generated_candidates",
        description=(
            "Improve generated candidate hypotheses with design, developability, model, "
            "and structure review."
        ),
        required_subagent_ids=[
            "molecule-designer",
            "developability-safety",
            "predictive-modeler",
            "structure-reviewer",
            "guardrail-sentinel",
        ],
        required_tools=[
            "run_generation",
            "run_design_loop",
            "run_developability",
            "summarize_safety_report",
            "build_model_dataset",
            "run_model_validation",
            "review_structure_artifacts",
            "run_pose_qc",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=["generation-summary", "developability-summary", "model-summary"],
        approval_requirements=["generated_molecule_export", "generated_molecule_assay_advancement"],
        guardrail_checks=["generated_overclaim", "prediction_not_evidence", "docking_not_evidence"],
        human_review_points=["before generated molecule assay advancement"],
        risk_level="high",
    ),
    MultiAgentSkill(
        skill_name="analyze_failed_campaign",
        description=(
            "Analyze failed or stalled campaign blockers and propose human-reviewable "
            "next steps."
        ),
        required_subagent_ids=[
            "campaign-planner",
            "experiment-analyst",
            "graph-reasoner",
            "portfolio-strategist",
            "guardrail-sentinel",
        ],
        required_tools=[
            "plan_campaign",
            "replan_campaign",
            "summarize_assay_results",
            "query_graph",
            "detect_contradictions",
            "optimize_portfolio",
            "run_scenarios",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=["campaign-summary", "assay-summary", "graph-summary"],
        approval_requirements=["campaign_approval", "stage_gate"],
        guardrail_checks=["failed_qc_not_support", "stale_hypotheses", "campaign_gate_policy"],
        human_review_points=["before campaign approval or stage-gate advancement"],
        risk_level="high",
    ),
    MultiAgentSkill(
        skill_name="prepare_review_packet",
        description=(
            "Prepare a source-grounded review packet across evidence, developability, "
            "structure, and hypotheses."
        ),
        required_subagent_ids=[
            "evidence-reviewer",
            "developability-safety",
            "structure-reviewer",
            "hypothesis-planner",
            "guardrail-sentinel",
        ],
        required_tools=[
            "summarize_literature",
            "summarize_evidence_report",
            "run_developability",
            "summarize_safety_report",
            "review_structure_artifacts",
            "generate_hypotheses",
            "rank_hypotheses",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=["evidence-summary", "candidate-summary", "hypothesis-summary"],
        approval_requirements=[],
        guardrail_checks=["evidence_grounding", "artifact_provenance", "hypothesis_not_protocol"],
        human_review_points=["before external review distribution"],
        risk_level="medium",
    ),
    MultiAgentSkill(
        skill_name="evaluate_platform_performance",
        description=(
            "Evaluate platform performance, release readiness, and program management "
            "implications."
        ),
        required_subagent_ids=["evaluation-validator", "platform-operator", "program-manager"],
        required_tools=[
            "run_benchmark",
            "run_reproducibility_check",
            "run_release_check",
            "run_readiness",
            "ops_health",
            "draft_report",
            "summarize_evaluation",
        ],
        expected_artifacts=["evaluation-summary", "ops-summary", "release-summary"],
        approval_requirements=[],
        guardrail_checks=["operational_reliability", "release_reproducibility"],
        human_review_points=["before release readiness signoff"],
        risk_level="medium",
    ),
    MultiAgentSkill(
        skill_name="integration_sync_review",
        description=(
            "Review integration mapping, dry-run sync, connector health, and "
            "write-readiness."
        ),
        required_subagent_ids=[
            "integration-operator",
            "platform-operator",
            "guardrail-sentinel",
        ],
        required_tools=[
            "health_check_integration",
            "dry_run_sync",
            "tool_marketplace_validate",
            "run_readiness",
            "generate_support_bundle",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=["connector-health-summary", "mapping-summary", "sync-summary"],
        approval_requirements=["integration_sync", "external_write"],
        guardrail_checks=["secret_redaction", "external_write_policy", "rbac_boundary"],
        human_review_points=["before any external write or enabled sync"],
        risk_level="high",
    ),
    MultiAgentSkill(
        skill_name="end_to_end_discovery_ops",
        description=(
            "Coordinate end-to-end discovery operations from evidence through campaign "
            "planning."
        ),
        required_subagent_ids=[
            "program-manager",
            "evidence-reviewer",
            "molecule-designer",
            "developability-safety",
            "experiment-analyst",
            "portfolio-strategist",
            "campaign-planner",
            "guardrail-sentinel",
        ],
        required_tools=[
            "summarize_literature",
            "run_generation",
            "run_design_loop",
            "run_developability",
            "summarize_assay_results",
            "optimize_portfolio",
            "plan_campaign",
            "replan_campaign",
            "draft_report",
            "run_guardrail_benchmark",
        ],
        expected_artifacts=[
            "program-summary",
            "evidence-summary",
            "candidate-summary",
            "campaign-summary",
        ],
        approval_requirements=[
            "stage_gate",
            "campaign_approval",
            "generated_molecule_assay_advancement",
        ],
        guardrail_checks=[
            "evidence_grounding",
            "generated_molecule_boundary",
            "failed_qc_not_support",
            "campaign_gate_policy",
        ],
        human_review_points=[
            "before stage gate",
            "before campaign approval",
            "before generated molecule assay advancement",
        ],
        risk_level="high",
    ),
]


__all__ = [
    "DEFAULT_OUTPUT_SCHEMA",
    "MultiAgentSkill",
    "MultiAgentSkillName",
    "builtin_multi_agent_skills",
    "expand_multi_agent_skill",
    "get_multi_agent_skill",
]
