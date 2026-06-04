from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.subagents.coordinator import HUMAN_REVIEW_TRIGGERS, TOOL_CATALOG
from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import SubagentProfile, SubagentTask, TaskRiskLevel

DelegationRequester = Literal["user", "system"]

TASK_TYPE_ROLE_MAP: dict[str, str] = {
    "program_management": "program_manager",
    "evidence_review": "evidence_reviewer",
    "molecule_design": "molecule_designer",
    "developability_safety": "developability_safety",
    "experiment_analysis": "experiment_analyst",
    "predictive_modeling": "predictive_modeler",
    "structure_review": "structure_reviewer",
    "graph_reasoning": "graph_reasoner",
    "hypothesis_planning": "hypothesis_planner",
    "portfolio_strategy": "portfolio_strategist",
    "campaign_planning": "campaign_planner",
    "integration_operation": "integration_operator",
    "evaluation_validation": "evaluation_validator",
    "guardrail_review": "guardrail_sentinel",
    "platform_operations": "platform_operator",
    "failed_jobs_readiness": "platform_operator",
    "campaign_status_blockers": "campaign_planner",
    "experiment_qc_review": "experiment_analyst",
    "graph_contradictions_stale_hypotheses": "graph_reasoner",
    "final_summary": "program_manager",
}

COMPLEX_CAMPAIGN_STALL_TASKS: tuple[tuple[str, str, list[str]], ...] = (
    (
        "failed_jobs_readiness",
        "Check failed jobs, workers, health, and readiness for campaign blockers.",
        ["run_readiness", "ops_health"],
    ),
    (
        "campaign_status_blockers",
        "Check campaign status, blockers, dependencies, and replan triggers.",
        ["plan_campaign", "replan_campaign"],
    ),
    (
        "experiment_qc_review",
        "Check new assay results, failed QC, missing links, and contradictions.",
        ["summarize_assay_results"],
    ),
    (
        "graph_contradictions_stale_hypotheses",
        "Check graph contradictions and stale hypotheses related to the stalled campaign.",
        ["query_graph", "detect_contradictions"],
    ),
    (
        "guardrail_review",
        "Critique final recommendations for scientific and operational guardrails.",
        ["run_guardrail_benchmark"],
    ),
    (
        "final_summary",
        "Produce final status summary and human-reviewable next steps.",
        ["draft_report"],
    ),
)


class DelegationPolicy(BaseModel):
    parent_session_id: str
    visible_artifact_ids: list[str]
    allowed_tool_names: list[str] | None = None
    forbidden_tool_names: list[str] = Field(default_factory=list)
    default_risk_level: TaskRiskLevel = "low"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationRequest(BaseModel):
    requester_subagent_id: str | DelegationRequester
    task_type: str
    objective: str
    required_artifact_ids: list[str]
    required_tool_names: list[str]
    risk_level: TaskRiskLevel | None = None
    parent_task_id: str | None = None
    delegation_chain: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationDecision(BaseModel):
    task: SubagentTask
    assigned_profile: SubagentProfile
    requires_guardrail_critique: bool
    requires_human_approval: bool
    inherited_policy: dict[str, Any]


class DelegationPolicyError(ValueError):
    """Raised when a delegation request violates subagent policy."""


class DelegationEngine:
    def __init__(
        self,
        *,
        registry: SubagentRegistry | None = None,
        tool_catalog: dict[str, list[str]] | None = None,
    ) -> None:
        self.registry = registry or SubagentRegistry()
        self.tool_catalog = tool_catalog or TOOL_CATALOG

    def delegate(
        self,
        request: DelegationRequest,
        *,
        policy: DelegationPolicy,
    ) -> DelegationDecision:
        self._validate_requester(request)
        role = self.role_for_task_type(request.task_type)
        profile = self.registry.by_role(role)
        self._validate_no_cycle(profile, request)
        self._validate_artifacts(request, policy)
        self._validate_tools(profile, request, policy)
        risk_level = request.risk_level or policy.default_risk_level
        requires_human_approval = _requires_human_approval(request.objective, risk_level)
        task = SubagentTask(
            task_id=f"delegated-task-{uuid4().hex[:12]}",
            parent_session_id=policy.parent_session_id,
            assigned_subagent_id=profile.subagent_id,
            task_type=request.task_type,
            objective=request.objective,
            input_artifact_ids=request.required_artifact_ids,
            allowed_tool_names=request.required_tool_names,
            forbidden_tool_names=list(
                dict.fromkeys([*policy.forbidden_tool_names, *self._forbidden_tools(profile)])
            ),
            expected_output_schema={
                "type": "object",
                "required": ["summary", "findings", "recommended_next_actions"],
                "properties": {
                    "summary": {"type": "string"},
                    "findings": {"type": "array"},
                    "recommended_next_actions": {"type": "array"},
                },
            },
            required_outputs=["summary", "findings", "recommended_next_actions"],
            risk_level=risk_level,
            requires_human_approval=requires_human_approval,
            status="queued",
            created_at=_now(),
            started_at=None,
            completed_at=None,
            metadata={
                "parent_task_id": request.parent_task_id,
                "delegation_chain": [*request.delegation_chain, profile.subagent_id],
                "inherited_policy": policy.model_dump(mode="json"),
                "requires_guardrail_critique": risk_level in {"high", "critical"},
                **request.metadata,
            },
        )
        return DelegationDecision(
            task=task,
            assigned_profile=profile,
            requires_guardrail_critique=risk_level in {"high", "critical"},
            requires_human_approval=requires_human_approval,
            inherited_policy=policy.model_dump(mode="json"),
        )

    def decompose_complex_task(
        self,
        *,
        user_goal: str,
        requester_subagent_id: str | DelegationRequester,
        policy: DelegationPolicy,
        root_artifact_ids: list[str],
    ) -> list[DelegationDecision]:
        if not _is_complex_campaign_stall_goal(user_goal):
            return [
                self.delegate(
                    DelegationRequest(
                        requester_subagent_id=requester_subagent_id,
                        task_type="program_management",
                        objective=user_goal,
                        required_artifact_ids=root_artifact_ids,
                        required_tool_names=["draft_report"],
                    ),
                    policy=policy,
                )
            ]
        decisions: list[DelegationDecision] = []
        for task_type, objective, tools in COMPLEX_CAMPAIGN_STALL_TASKS:
            decisions.append(
                self.delegate(
                    DelegationRequest(
                        requester_subagent_id=requester_subagent_id,
                        task_type=task_type,
                        objective=objective,
                        required_artifact_ids=root_artifact_ids,
                        required_tool_names=tools,
                        risk_level="medium" if task_type != "guardrail_review" else "high",
                        delegation_chain=[],
                        metadata={"complex_goal": user_goal},
                    ),
                    policy=policy,
                )
            )
        return decisions

    def role_for_task_type(self, task_type: str) -> str:
        try:
            return TASK_TYPE_ROLE_MAP[task_type]
        except KeyError as exc:
            raise DelegationPolicyError(f"unknown task type: {task_type}") from exc

    def _validate_requester(self, request: DelegationRequest) -> None:
        if request.requester_subagent_id in {"user", "system"}:
            return
        requester = self.registry.require(str(request.requester_subagent_id))
        if not requester.can_delegate:
            raise DelegationPolicyError(
                f"subagent cannot request delegation: {request.requester_subagent_id}"
            )

    def _validate_no_cycle(
        self,
        profile: SubagentProfile,
        request: DelegationRequest,
    ) -> None:
        if profile.subagent_id in set(request.delegation_chain):
            raise DelegationPolicyError(
                "cyclic delegation blocked for subagent: " + profile.subagent_id
            )

    def _validate_artifacts(
        self,
        request: DelegationRequest,
        policy: DelegationPolicy,
    ) -> None:
        unauthorized = sorted(set(request.required_artifact_ids) - set(policy.visible_artifact_ids))
        if unauthorized:
            raise DelegationPolicyError(
                "required artifacts are not authorized: " + ", ".join(unauthorized)
            )

    def _validate_tools(
        self,
        profile: SubagentProfile,
        request: DelegationRequest,
        policy: DelegationPolicy,
    ) -> None:
        profile_tools = set(self._allowed_tools(profile))
        if policy.allowed_tool_names is not None:
            profile_tools = profile_tools.intersection(policy.allowed_tool_names)
        profile_tools.difference_update(policy.forbidden_tool_names)
        unauthorized = sorted(set(request.required_tool_names) - profile_tools)
        if unauthorized:
            raise DelegationPolicyError(
                "required tools are not allowed for subagent profile: "
                + ", ".join(unauthorized)
            )

    def _allowed_tools(self, profile: SubagentProfile) -> list[str]:
        tools: list[str] = []
        for category in profile.allowed_tool_categories:
            tools.extend(self.tool_catalog.get(category, []))
        forbidden = set(self._forbidden_tools(profile))
        return [tool for tool in dict.fromkeys(tools) if tool not in forbidden]

    def _forbidden_tools(self, profile: SubagentProfile) -> list[str]:
        tools: list[str] = []
        for category in profile.denied_tool_categories:
            tools.extend(self.tool_catalog.get(category, [category]))
        return list(dict.fromkeys(tools))


def _requires_human_approval(objective: str, risk_level: TaskRiskLevel) -> bool:
    if risk_level in {"high", "critical"}:
        return True
    objective_lower = objective.lower()
    return any(phrase in objective_lower for phrase in HUMAN_REVIEW_TRIGGERS)


def _is_complex_campaign_stall_goal(user_goal: str) -> bool:
    goal = user_goal.lower()
    return "campaign" in goal and ("stalled" in goal or "stall" in goal)


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "COMPLEX_CAMPAIGN_STALL_TASKS",
    "DelegationDecision",
    "DelegationEngine",
    "DelegationPolicy",
    "DelegationPolicyError",
    "DelegationRequest",
    "TASK_TYPE_ROLE_MAP",
]
