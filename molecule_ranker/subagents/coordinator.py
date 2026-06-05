from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from molecule_ranker.agent_governance.capability_grants import CapabilityGrantManager
from molecule_ranker.agent_governance.certification import AgentCertificationManager
from molecule_ranker.agent_governance.schemas import AgentCapabilityScopeType
from molecule_ranker.subagents.registry import (
    HIGH_RISK_TOOL_CATEGORIES,
    SubagentRegistry,
)
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentCritique,
    SubagentMessage,
    SubagentProfile,
    SubagentResult,
    SubagentTask,
)

CoordinationMode = Literal[
    "sequential",
    "parallel_independent",
    "supervisor_delegated",
    "critique_and_revise",
    "consensus_required",
    "human_review_required",
]

HUMAN_REVIEW_TRIGGERS: dict[str, str] = {
    "stage gate": "stage_gate",
    "external write": "external_write",
    "generated molecule assay advancement": "generated_molecule_assay_advancement",
    "campaign approval": "campaign_approval",
    "destructive": "destructive_action",
    "policy override": "policy_override",
}

TOOL_CATALOG: dict[str, list[str]] = {
    "active_learning": ["suggest_active_learning_batch"],
    "admin": ["run_readiness", "run_release_check"],
    "artifacts": ["register_artifacts", "summarize_artifacts"],
    "campaign": ["plan_campaign", "replan_campaign"],
    "contradiction": ["detect_contradictions"],
    "design": ["run_design_loop"],
    "developability": ["run_developability", "assess_developability_artifact"],
    "docking": ["run_structure_validation"],
    "evaluation": ["run_benchmark", "run_guardrail_benchmark", "run_reproducibility_check"],
    "evaluation_summary": ["summarize_evaluation"],
    "evidence_reports": ["summarize_evidence_report"],
    "experiments": ["import_assay_results", "summarize_assay_results"],
    "generation": ["run_generation", "benchmark_generation"],
    "graph": ["build_graph", "query_graph"],
    "graph_query": ["query_graph"],
    "guardrail_benchmark": ["run_guardrail_benchmark"],
    "hypothesis": ["generate_hypotheses", "rank_hypotheses"],
    "integration": ["health_check_integration", "dry_run_sync", "run_sync_write_enabled"],
    "jobs": ["list_jobs", "show_job"],
    "literature": ["run_literature_update", "summarize_literature"],
    "mechanism": ["query_mechanism"],
    "model": ["build_model_dataset", "run_model_validation"],
    "model_prediction_summaries": ["summarize_model_predictions"],
    "ops": ["ops_health", "ops_alerts"],
    "oracle_scoring": ["run_oracle_scoring"],
    "portfolio": ["build_portfolio_candidates", "optimize_portfolio", "run_scenarios"],
    "pose_qc": ["run_pose_qc"],
    "project": ["list_projects", "show_project", "create_project"],
    "ranking": ["run_ranking", "summarize_ranking"],
    "release_checks": ["run_release_check"],
    "reports": ["draft_report", "build_runtime_summary"],
    "result_summaries": ["summarize_assay_results"],
    "safety_reports": ["summarize_safety_report"],
    "structure": ["review_structure_artifacts", "run_structure_validation"],
    "support": ["generate_support_bundle"],
    "tool_marketplace": ["tool_marketplace_list", "tool_marketplace_validate"],
    "validation": ["validate_contracts", "validate_tools"],
}


class SubagentPolicyError(ValueError):
    """Raised when a requested coordination step violates subagent policy."""


class MultiAgentCoordinator:
    def __init__(
        self,
        *,
        registry: SubagentRegistry | None = None,
        tool_catalog: dict[str, list[str]] | None = None,
        certification_manager: AgentCertificationManager | None = None,
        capability_grant_manager: CapabilityGrantManager | None = None,
    ) -> None:
        self.registry = registry or SubagentRegistry()
        self.tool_catalog = tool_catalog or TOOL_CATALOG
        self.certification_manager = certification_manager
        self.capability_grant_manager = capability_grant_manager

    def coordinate(
        self,
        *,
        user_goal: str,
        mode: CoordinationMode = "sequential",
        runtime_session_id: str | None = None,
        visible_artifact_ids: list[str] | None = None,
        scoped_artifact_ids: list[str] | None = None,
        requested_tool_names: list[str] | None = None,
        output_dir: Path | None = None,
        force_disagreement: bool = False,
        governance_scope_type: AgentCapabilityScopeType = "workflow",
        governance_scope_id: str | None = None,
    ) -> MultiAgentSession:
        started_at = _now()
        session_id = f"multi-agent-session-{uuid4().hex[:12]}"
        visible_artifacts = visible_artifact_ids or ["user-goal"]
        scoped_artifacts = scoped_artifact_ids or visible_artifacts[:1]
        _ensure_artifacts_visible(scoped_artifacts, visible_artifacts)

        supervisor = self.choose_supervisor(user_goal)
        if not supervisor.can_delegate:
            raise SubagentPolicyError("supervisor subagent cannot delegate")

        audit_events: list[dict[str, Any]] = []
        self._audit(
            audit_events,
            "multi_agent_session_started",
            supervisor.subagent_id,
            "Accepted user goal for multi-agent coordination.",
        )

        tasks = self.decompose_goal(
            user_goal=user_goal,
            parent_session_id=session_id,
            mode=mode,
            scoped_artifact_ids=scoped_artifacts,
            requested_tool_names=requested_tool_names,
            governance_scope_type=governance_scope_type,
            governance_scope_id=governance_scope_id or session_id,
        )
        self._add_dependencies(tasks, mode)
        messages = [
            self._task_request_message(
                session_id=session_id,
                supervisor_id=supervisor.subagent_id,
                task=task,
            )
            for task in tasks
        ]
        results = [self._run_task(task) for task in tasks]
        critiques = self.request_critiques(
            session_id=session_id,
            results=results,
            tasks=tasks,
            force_disagreement=force_disagreement,
        )
        consensus = [
            self.synthesize_consensus(
                parent_session_id=session_id,
                tasks=tasks,
                critiques=critiques,
                mode=mode,
                user_goal=user_goal,
            )
        ]
        repair_escalations = _repair_escalations(user_goal)
        for escalation in repair_escalations:
            self._audit(
                audit_events,
                "subagent_repair_escalated",
                escalation["diagnostic_subagent_id"],
                escalation["summary"],
                escalation,
            )
        if consensus[0].human_review_required:
            self._audit(
                audit_events,
                "human_review_escalated",
                "guardrail-sentinel",
                "Consensus requires human review.",
                {"consensus_id": consensus[0].consensus_id},
            )

        for task in tasks:
            self._audit(
                audit_events,
                "subagent_task_completed",
                task.assigned_subagent_id,
                f"Task {task.task_id} completed with status {task.status}.",
                {"task_id": task.task_id},
            )

        completed_at = _now()
        session = MultiAgentSession(
            multi_agent_session_id=session_id,
            runtime_session_id=runtime_session_id,
            user_goal=user_goal,
            supervisor_subagent_id=supervisor.subagent_id,
            subagent_ids=sorted(
                {task.assigned_subagent_id for task in tasks} | {supervisor.subagent_id}
            ),
            tasks=tasks,
            messages=messages,
            results=results,
            critiques=critiques,
            consensus=consensus,
            status="awaiting_human_review" if consensus[0].human_review_required else "succeeded",
            started_at=started_at,
            completed_at=completed_at,
            metadata={
                "coordination_mode": mode,
                "audit_events": audit_events,
                "visible_artifact_ids": visible_artifacts,
                "human_review_triggers": _human_review_triggers(user_goal),
                "repair_escalations": repair_escalations,
                "supervisor_policy": {
                    "cannot": supervisor.metadata.get("cannot", []),
                    "high_risk_tool_categories": HIGH_RISK_TOOL_CATEGORIES,
                },
            },
        )
        if output_dir is not None:
            self.write_session(output_dir, session)
        return session

    def choose_supervisor(self, user_goal: str) -> SubagentProfile:
        goal = user_goal.lower()
        if "platform" in goal and "only" in goal:
            return self.registry.require("platform-operator")
        return self.registry.require("program-manager")

    def decompose_goal(
        self,
        *,
        user_goal: str,
        parent_session_id: str,
        mode: CoordinationMode,
        scoped_artifact_ids: list[str],
        requested_tool_names: list[str] | None = None,
        governance_scope_type: AgentCapabilityScopeType = "workflow",
        governance_scope_id: str | None = None,
    ) -> list[SubagentTask]:
        profile_ids = self._select_subagents(user_goal, mode)
        tasks: list[SubagentTask] = []
        for index, profile_id in enumerate(profile_ids):
            profile = self.registry.require(profile_id)
            allowed_tools = self._allowed_tools_for_profile(profile)
            if requested_tool_names is not None:
                unauthorized = sorted(set(requested_tool_names) - set(allowed_tools))
                if unauthorized:
                    raise SubagentPolicyError(
                        "unauthorized tool for assigned subagent: " + ", ".join(unauthorized)
                    )
                allowed_tools = list(dict.fromkeys(requested_tool_names))
            self._check_subagent_governance(
                profile,
                requested_capabilities=requested_tool_names or [],
                scope_type=governance_scope_type,
                scope_id=governance_scope_id or parent_session_id,
            )
            risk_level = _risk_level(user_goal)
            tasks.append(
                SubagentTask(
                    task_id=f"subagent-task-{index + 1}",
                    parent_session_id=parent_session_id,
                    assigned_subagent_id=profile.subagent_id,
                    task_type=str(profile.role),
                    objective=_task_objective(user_goal, profile),
                    input_artifact_ids=scoped_artifact_ids,
                    allowed_tool_names=allowed_tools,
                    forbidden_tool_names=self._forbidden_tools_for_profile(profile),
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
                    requires_human_approval=_requires_human_review(user_goal),
                    status="queued",
                    created_at=_now(),
                    started_at=None,
                    completed_at=None,
                    metadata={
                        "coordination_mode": mode,
                        "profile_role": profile.role,
                        "dependencies": [],
                    },
                )
            )
        return tasks

    def _check_subagent_governance(
        self,
        profile: SubagentProfile,
        *,
        requested_capabilities: list[str],
        scope_type: AgentCapabilityScopeType,
        scope_id: str | None,
    ) -> None:
        if self.certification_manager is not None:
            certifications = self.certification_manager.list_certifications(
                agent_id=profile.subagent_id,
                certification_type="subagent_role",
                include_inactive=False,
            )
            if not certifications:
                raise SubagentPolicyError(
                    f"subagent {profile.subagent_id} lacks active subagent_role certification"
                )
        if self.capability_grant_manager is None:
            return
        for capability in requested_capabilities:
            decision = self.capability_grant_manager.check_capability(
                agent_id=profile.subagent_id,
                capability=capability,
                scope_type=cast(AgentCapabilityScopeType, scope_type),
                scope_id=scope_id,
            )
            if not decision.allowed:
                raise SubagentPolicyError(
                    f"subagent {profile.subagent_id} lacks capability grant: {capability}"
                )

    def request_critiques(
        self,
        *,
        session_id: str,
        results: list[SubagentResult],
        tasks: list[SubagentTask],
        force_disagreement: bool = False,
    ) -> list[SubagentCritique]:
        del session_id
        task_by_id = {task.task_id: task for task in tasks}
        critiques: list[SubagentCritique] = []
        for result in results:
            task = task_by_id[result.task_id]
            if task.risk_level in {"high", "critical"} or task.requires_human_approval:
                critiques.append(
                    self._sentinel_critique(
                        result=result,
                        passed=not force_disagreement and result.status == "succeeded",
                        finding=(
                            "High-risk output requires human review."
                            if task.requires_human_approval
                            else "High-risk output passed guardrail critique."
                        ),
                    )
                )
        if force_disagreement and results and not critiques:
            critiques.append(
                self._sentinel_critique(
                    result=results[0],
                    passed=False,
                    finding="Forced disagreement for review escalation.",
                )
            )
        return critiques

    def synthesize_consensus(
        self,
        *,
        parent_session_id: str,
        tasks: list[SubagentTask],
        critiques: list[SubagentCritique],
        mode: CoordinationMode,
        user_goal: str,
    ) -> SubagentConsensus:
        failed_critiques = [critique for critique in critiques if not critique.passed]
        human_review_triggers = _human_review_triggers(user_goal)
        repair_escalations = _repair_escalations(user_goal)
        human_review_required = bool(
            failed_critiques
            or human_review_triggers
            or any(
                item.get("required_for_unsafe_scientific_output")
                for item in repair_escalations
            )
            or mode in {"human_review_required", "consensus_required"}
            and failed_critiques
        )
        if failed_critiques:
            status = "disagreement"
        elif human_review_required:
            status = "requires_human_review"
        else:
            status = "agreed"
        return SubagentConsensus(
            consensus_id=f"subagent-consensus-{uuid4().hex[:12]}",
            parent_session_id=parent_session_id,
            task_ids=[task.task_id for task in tasks],
            participating_subagent_ids=sorted(
                {task.assigned_subagent_id for task in tasks}
                | {critique.critic_subagent_id for critique in critiques}
            ),
            consensus_status=status,  # type: ignore[arg-type]
            summary=(
                "Human review required."
                if human_review_required
                else "Subagents completed with no blocking disagreement."
            ),
            agreements=[
                "All subagent outputs are scoped to assigned artifacts and tools."
            ]
            if not failed_critiques
            else [],
            disagreements=[
                finding
                for critique in failed_critiques
                for finding in critique.findings
            ],
            recommended_next_actions=(
                ["Escalate to human reviewer."]
                if human_review_required
                else ["Proceed with human-readable review of outputs."]
            ),
            human_review_required=human_review_required,
            metadata={
                "coordination_mode": mode,
                "human_review_triggers": human_review_triggers,
                "repair_escalations": repair_escalations,
            },
        )

    def write_session(self, output_dir: Path, session: MultiAgentSession) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "multi_agent_session.json"
        path.write_text(
            json.dumps(session.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _allowed_tools_for_profile(self, profile: SubagentProfile) -> list[str]:
        tools: list[str] = []
        for category in profile.allowed_tool_categories:
            tools.extend(self.tool_catalog.get(category, []))
        denied_categories = set(profile.denied_tool_categories)
        for category in denied_categories:
            denied_tools = set(self.tool_catalog.get(category, []))
            tools = [tool for tool in tools if tool not in denied_tools]
        if not tools:
            raise SubagentPolicyError(f"no scoped tools for subagent: {profile.subagent_id}")
        return list(dict.fromkeys(tools))

    def _forbidden_tools_for_profile(self, profile: SubagentProfile) -> list[str]:
        tools: list[str] = []
        for category in profile.denied_tool_categories:
            tools.extend(self.tool_catalog.get(category, [category]))
        return list(dict.fromkeys(tools))

    def _select_subagents(self, user_goal: str, mode: CoordinationMode) -> list[str]:
        goal = user_goal.lower()
        selected: list[str] = []
        for keyword, profile_id in _KEYWORD_SUBAGENTS:
            if keyword in goal:
                selected.append(profile_id)
        if "generated molecule assay advancement" in goal:
            selected.extend(["molecule-designer", "experiment-analyst"])
        if mode == "parallel_independent" and len(selected) < 2:
            selected.extend(["evidence-reviewer", "developability-safety"])
        if mode == "critique_and_revise":
            selected.append("guardrail-sentinel")
        if not selected:
            selected.append("program-manager")
        return list(dict.fromkeys(selected))

    def _run_task(self, task: SubagentTask) -> SubagentResult:
        started_at = _now()
        completed_at = _now()
        task.started_at = started_at
        task.completed_at = completed_at
        task.status = "awaiting_approval" if task.requires_human_approval else "succeeded"
        result_status = "partial" if task.requires_human_approval else "succeeded"
        return SubagentResult(
            result_id=f"subagent-result-{uuid4().hex[:12]}",
            task_id=task.task_id,
            subagent_id=task.assigned_subagent_id,
            status=result_status,  # type: ignore[arg-type]
            output_json={
                "summary": f"{task.assigned_subagent_id} completed scoped task.",
                "findings": [],
                "recommended_next_actions": (
                    ["Human review required before execution."]
                    if task.requires_human_approval
                    else ["Review result artifact."]
                ),
            },
            output_text=f"{task.assigned_subagent_id} completed scoped task.",
            artifact_ids=[f"artifact-{task.task_id}"],
            tool_usage_ids=[f"tool-usage-{tool}" for tool in task.allowed_tool_names],
            confidence=0.65 if task.requires_human_approval else 0.85,
            warnings=(
                ["Human approval required for high-risk action."]
                if task.requires_human_approval
                else []
            ),
            guardrail_findings=[],
            created_at=completed_at,
            metadata={
                "risk_level": task.risk_level,
                "scoped_tool_names": task.allowed_tool_names,
                "input_artifact_ids": task.input_artifact_ids,
            },
        )

    def _sentinel_critique(
        self,
        *,
        result: SubagentResult,
        passed: bool,
        finding: str,
    ) -> SubagentCritique:
        return SubagentCritique(
            critique_id=f"subagent-critique-{uuid4().hex[:12]}",
            critic_subagent_id="guardrail-sentinel",
            target_result_id=result.result_id,
            critique_type="scientific_guardrail",
            passed=passed,
            findings=[finding],
            required_fixes=[] if passed else ["Human review must resolve disagreement."],
            confidence=0.9,
            metadata={"required_for_high_risk": True},
        )

    def _task_request_message(
        self,
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
            metadata={"task_id": task.task_id},
        )

    def _add_dependencies(self, tasks: list[SubagentTask], mode: CoordinationMode) -> None:
        if mode == "parallel_independent":
            for task in tasks:
                task.metadata["dependencies"] = []
            return
        for index, task in enumerate(tasks):
            task.metadata["dependencies"] = [tasks[index - 1].task_id] if index else []

    def _audit(
        self,
        audit_events: list[dict[str, Any]],
        event_type: str,
        actor: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_events.append(
            {
                "event_id": f"subagent-audit-{uuid4().hex[:12]}",
                "event_type": event_type,
                "actor": actor,
                "summary": summary,
                "timestamp": _now().isoformat(),
                "metadata": metadata or {},
            }
        )


_KEYWORD_SUBAGENTS: tuple[tuple[str, str], ...] = (
    ("evidence", "evidence-reviewer"),
    ("literature", "evidence-reviewer"),
    ("rank", "evidence-reviewer"),
    ("molecule", "molecule-designer"),
    ("generation", "molecule-designer"),
    ("design", "molecule-designer"),
    ("developability", "developability-safety"),
    ("safety", "developability-safety"),
    ("assay", "experiment-analyst"),
    ("experiment", "experiment-analyst"),
    ("model", "predictive-modeler"),
    ("prediction", "predictive-modeler"),
    ("structure", "structure-reviewer"),
    ("docking", "structure-reviewer"),
    ("graph", "graph-reasoner"),
    ("hypothesis", "hypothesis-planner"),
    ("portfolio", "portfolio-strategist"),
    ("campaign", "campaign-planner"),
    ("integration", "integration-operator"),
    ("external write", "integration-operator"),
    ("evaluation", "evaluation-validator"),
    ("validation", "evaluation-validator"),
    ("guardrail", "guardrail-sentinel"),
    ("platform", "platform-operator"),
    ("support", "platform-operator"),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_artifacts_visible(
    scoped_artifact_ids: list[str],
    visible_artifact_ids: list[str],
) -> None:
    unauthorized = sorted(set(scoped_artifact_ids) - set(visible_artifact_ids))
    if unauthorized:
        raise SubagentPolicyError(
            "subagents cannot see unauthorized artifacts: " + ", ".join(unauthorized)
        )


def _human_review_triggers(user_goal: str) -> list[str]:
    goal = user_goal.lower()
    return [
        approval_type
        for phrase, approval_type in HUMAN_REVIEW_TRIGGERS.items()
        if phrase in goal
    ]


def _repair_escalations(user_goal: str) -> list[dict[str, Any]]:
    goal = user_goal.lower()
    if not any(term in goal for term in ("repair", "failure", "failed", "diagnose")):
        return []
    escalations: list[dict[str, Any]] = [
        {
            "diagnostic_subagent_id": "platform-operator",
            "summary": "PlatformOperator requested to diagnose operational repair failure.",
            "repair_role": "FailureDiagnosisAgent",
        }
    ]
    if any(term in goal for term in ("unsafe", "guardrail", "scientific output")):
        escalations.append(
            {
                "diagnostic_subagent_id": "guardrail-sentinel",
                "summary": (
                    "GuardrailSentinel review required for unsafe scientific repair output."
                ),
                "repair_role": "GuardrailSentinel",
                "required_for_unsafe_scientific_output": True,
            }
        )
    return escalations


def _requires_human_review(user_goal: str) -> bool:
    return bool(
        _human_review_triggers(user_goal)
        or any(
            item.get("required_for_unsafe_scientific_output")
            for item in _repair_escalations(user_goal)
        )
    )


def _risk_level(user_goal: str) -> Literal["low", "medium", "high", "critical"]:
    triggers = set(_human_review_triggers(user_goal))
    if {"destructive_action", "policy_override"}.intersection(triggers):
        return "critical"
    if triggers:
        return "high"
    if any(word in user_goal.lower() for word in ("safety", "docking", "generated")):
        return "medium"
    return "low"


def _task_objective(user_goal: str, profile: SubagentProfile) -> str:
    return f"{profile.name}: {user_goal}"


__all__ = [
    "CoordinationMode",
    "HUMAN_REVIEW_TRIGGERS",
    "MultiAgentCoordinator",
    "SubagentPolicyError",
    "TOOL_CATALOG",
]
