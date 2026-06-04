from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.executor import (
    ExecutionMode,
    RuntimeActionExecutor,
    RuntimeExecutionResult,
    ToolHandler,
)
from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

SpecialistAgentKind = Literal[
    "program_management",
    "evidence_review",
    "molecule_design",
    "developability_safety",
    "experimental_feedback",
    "predictive_modeling",
    "structure_workflow_review",
    "knowledge_graph_reasoning",
    "hypothesis_generation",
    "portfolio_campaign_planning",
    "integration_operations",
    "evaluation_validation",
    "guardrail_safety_review",
    "platform_operations",
]
DelegationStatus = Literal[
    "created",
    "planned",
    "executed",
    "awaiting_human_review",
    "blocked",
    "failed",
]
CritiqueVerdict = Literal["accepted", "needs_revision", "escalate_human"]

HUMAN_ONLY_APPROVALS = {
    "stage_gate",
    "campaign_advance",
    "external_write",
    "generated_molecule_export",
    "destructive_action",
}
FORBIDDEN_SUBAGENT_OUTPUTS = [
    "Subagents are operational specialists, not scientific truth sources.",
    "Subagents cannot invent evidence, assay results, citations, molecules, graph facts, "
    "model metrics, docking scores, campaign outcomes, or benchmark results.",
    "Subagents cannot approve stage gates, campaign advancement, external writes, "
    "generated-molecule assay advancement, or destructive actions.",
    "Subagents cannot bypass deterministic validators, RBAC, policy, approvals, "
    "approved tools, or sandbox boundaries.",
    "Subagents cannot provide medical advice, lab protocols, synthesis instructions, dosing, "
    "or patient treatment guidance.",
]


class SpecialistAgentSpec(BaseModel):
    agent_id: str
    kind: SpecialistAgentKind
    display_name: str
    description: str
    allowed_tool_categories: list[str]
    allowed_permissions: list[str]
    sandbox_profile: str
    output_schema: dict[str, Any]
    policy_constraints: list[str] = Field(default_factory=list)
    human_only_approval_types: list[str] = Field(
        default_factory=lambda: sorted(HUMAN_ONLY_APPROVALS)
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_tool_categories", "allowed_permissions")
    @classmethod
    def require_non_empty_strings(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("specialist agent allowlists must not be empty")
        return value

    @model_validator(mode="after")
    def require_structured_output_schema(self) -> SpecialistAgentSpec:
        if self.output_schema.get("type") != "object":
            raise ValueError("specialist output_schema must be a JSON object schema")
        if "summary" not in self.output_schema.get("properties", {}):
            raise ValueError("specialist output_schema must include summary")
        return self


class SpecialistDelegationTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"multi-agent-task-{uuid4().hex[:12]}")
    session_id: str
    specialist_id: str
    objective: str
    delegated_by: str
    project_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    scoped_artifact_ids: list[str] = Field(default_factory=list)
    allowed_tool_names: list[str] = Field(default_factory=list)
    status: DelegationStatus = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "completed_at")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class SpecialistAgentOutput(BaseModel):
    output_id: str = Field(default_factory=lambda: f"multi-agent-output-{uuid4().hex[:12]}")
    task_id: str
    specialist_id: str
    summary: str
    grounded_artifact_ids: list[str] = Field(default_factory=list)
    tool_result_ids: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    escalation_reason: str | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_escalation_reason(self) -> SpecialistAgentOutput:
        if self.escalation_required and not self.escalation_reason:
            raise ValueError("escalation_reason is required when escalation_required is true")
        return self


class SpecialistCritique(BaseModel):
    critique_id: str = Field(default_factory=lambda: f"multi-agent-critique-{uuid4().hex[:12]}")
    reviewed_output_id: str
    reviewer_specialist_id: str
    verdict: CritiqueVerdict
    issues: list[str] = Field(default_factory=list)
    required_human_review: bool = False
    guardrail_warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanReviewEscalation(BaseModel):
    escalation_id: str = Field(default_factory=lambda: f"multi-agent-escalation-{uuid4().hex[:12]}")
    task_id: str
    specialist_id: str
    reason: str
    requested_by: str
    approval_types: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["pending", "resolved"] = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpecialistDelegationResult(BaseModel):
    task: SpecialistDelegationTask
    plan: RuntimeActionPlan
    execution: RuntimeExecutionResult | None = None
    output: SpecialistAgentOutput | None = None
    critiques: list[SpecialistCritique] = Field(default_factory=list)
    escalations: list[HumanReviewEscalation] = Field(default_factory=list)
    audit_events: list[RuntimeAgentAuditEvent] = Field(default_factory=list)


class SpecialistAgentRegistry:
    def __init__(self, specs: list[SpecialistAgentSpec] | None = None) -> None:
        self._specs = {spec.agent_id: spec for spec in specs or default_specialist_agents()}

    def require(self, specialist_id: str) -> SpecialistAgentSpec:
        try:
            return self._specs[specialist_id]
        except KeyError as exc:
            raise KeyError(f"unknown specialist agent: {specialist_id}") from exc

    def list_agents(self) -> list[SpecialistAgentSpec]:
        return sorted(self._specs.values(), key=lambda spec: spec.agent_id)

    def by_kind(self, kind: SpecialistAgentKind) -> SpecialistAgentSpec:
        for spec in self._specs.values():
            if spec.kind == kind:
                return spec
        raise KeyError(f"unknown specialist kind: {kind}")


class MultiAgentScientificOrchestrator:
    """Coordinate V2.4 specialist Codex subagents through runtime-agent controls."""

    def __init__(
        self,
        *,
        agent_registry: SpecialistAgentRegistry | None = None,
        tool_registry: RuntimeToolRegistry | None = None,
        tool_handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        self.agent_registry = agent_registry or SpecialistAgentRegistry()
        self.tool_registry = tool_registry or RuntimeToolRegistry.default()
        self.tool_handlers = tool_handlers or {}
        self.guardrails = RuntimeGuardrailChecker(registry=self.tool_registry)

    def list_specialists(self) -> list[SpecialistAgentSpec]:
        return self.agent_registry.list_agents()

    def allowed_tools_for_specialist(
        self,
        specialist_id: str,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | list[str] | None = None,
    ) -> list[str]:
        spec = self.agent_registry.require(specialist_id)
        permissions = set(user_permissions or [])
        allowed_permissions = set(spec.allowed_permissions)
        tools = self.tool_registry.discover_approved_tools(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            user_permissions=permissions,
            categories=set(spec.allowed_tool_categories),
        )
        return [
            tool.tool_name
            for tool in tools
            if set(tool.required_permissions).issubset(permissions)
            and set(tool.required_permissions).issubset(allowed_permissions)
            and _tool_stays_inside_specialist_boundary(tool.policy_tags)
        ]

    def delegate_task(
        self,
        *,
        specialist_id: str,
        objective: str,
        session_id: str,
        delegated_by: str,
        current_artifacts: list[dict[str, Any]] | None = None,
        scoped_artifact_ids: list[str] | None = None,
        requested_tool_names: list[str] | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | list[str] | None = None,
    ) -> SpecialistDelegationResult:
        specialist = self.agent_registry.require(specialist_id)
        permissions = set(user_permissions or [])
        artifacts = current_artifacts or []
        artifact_ids = _artifact_id_set(artifacts)
        scoped_ids = list(dict.fromkeys(scoped_artifact_ids or sorted(artifact_ids)))
        unknown_refs = sorted(set(scoped_ids) - artifact_ids)
        if unknown_refs:
            raise ValueError("unknown scoped artifacts: " + ", ".join(unknown_refs))

        allowed_tool_names = self.allowed_tools_for_specialist(
            specialist_id,
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            user_permissions=permissions,
        )
        if requested_tool_names is not None:
            disallowed = sorted(set(requested_tool_names) - set(allowed_tool_names))
            if disallowed:
                raise ValueError(
                    "specialist cannot use requested tools: " + ", ".join(disallowed)
                )
            allowed_tool_names = list(dict.fromkeys(requested_tool_names))
        selected_tools = _select_tools_for_objective(
            objective,
            allowed_tool_names,
            self.tool_registry,
        )
        if not selected_tools:
            selected_tools = _read_only_tools(allowed_tool_names, self.tool_registry)[:1]
        if not selected_tools:
            raise ValueError(f"no approved tools available for specialist: {specialist_id}")

        task = SpecialistDelegationTask(
            session_id=session_id,
            specialist_id=specialist_id,
            objective=objective,
            delegated_by=delegated_by,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            scoped_artifact_ids=scoped_ids,
            allowed_tool_names=allowed_tool_names,
            status="planned",
            metadata={
                "specialist_kind": specialist.kind,
                "sandbox_profile": specialist.sandbox_profile,
                "policy_constraints": specialist.policy_constraints + FORBIDDEN_SUBAGENT_OUTPUTS,
            },
        )
        plan = self._build_plan(
            task=task,
            specialist=specialist,
            selected_tools=selected_tools,
            user_permissions=permissions,
            artifacts=artifacts,
        )
        audit_events = [
            _audit(
                session_id=session_id,
                event_type="specialist_task_delegated",
                actor=delegated_by,
                summary=f"Delegated task to {specialist.display_name}.",
                object_type="SpecialistDelegationTask",
                object_id=task.task_id,
                metadata={
                    "specialist_id": specialist.agent_id,
                    "specialist_kind": specialist.kind,
                    "allowed_tool_names": allowed_tool_names,
                    "scoped_artifact_ids": scoped_ids,
                    "sandbox_profile": specialist.sandbox_profile,
                },
            )
        ]
        return SpecialistDelegationResult(task=task, plan=plan, audit_events=audit_events)

    def execute_delegation(
        self,
        delegation: SpecialistDelegationResult,
        *,
        mode: ExecutionMode,
        actor: str,
        approvals: set[str] | list[str] | None = None,
    ) -> SpecialistDelegationResult:
        execution = RuntimeActionExecutor(
            registry=self.tool_registry,
            tool_handlers=self.tool_handlers,
        ).execute(delegation.plan, mode=mode, actor=actor, approvals=approvals)
        output = self._structured_output(delegation, execution)
        escalations = list(delegation.escalations)
        if output.escalation_required:
            escalations.append(
                self.escalate_to_human_review(
                    task=delegation.task,
                    reason=output.escalation_reason or "Specialist output requires human review.",
                    requested_by=delegation.task.specialist_id,
                    artifact_ids=output.grounded_artifact_ids,
                )
            )
        task_status: DelegationStatus = "executed"
        if (
            execution.status in {"approval_required", "policy_blocked"}
            or output.escalation_required
        ):
            task_status = "awaiting_human_review"
        elif execution.status == "failed":
            task_status = "failed"
        task = delegation.task.model_copy(
            update={"status": task_status, "completed_at": datetime.now(UTC)}
        )
        audit_events = [
            *delegation.audit_events,
            *execution.audit_events,
            _audit(
                session_id=task.session_id,
                event_type="specialist_task_completed",
                actor=actor,
                summary=f"Specialist task completed with {execution.status}.",
                object_type="SpecialistAgentOutput",
                object_id=output.output_id,
                metadata={
                    "specialist_id": task.specialist_id,
                    "execution_status": execution.status,
                    "escalation_required": output.escalation_required,
                },
            ),
        ]
        return delegation.model_copy(
            update={
                "task": task,
                "execution": execution,
                "output": output,
                "escalations": escalations,
                "audit_events": audit_events,
            }
        )

    def critique_output(
        self,
        *,
        reviewer_specialist_id: str,
        output: SpecialistAgentOutput,
        scoped_artifact_ids: list[str],
    ) -> SpecialistCritique:
        reviewer = self.agent_registry.require(reviewer_specialist_id)
        issues: list[str] = []
        guardrail = self.guardrails.check_output(output.model_dump(mode="json"))
        issues.extend(violation.message for violation in guardrail.violations)
        unknown_artifacts = sorted(set(output.grounded_artifact_ids) - set(scoped_artifact_ids))
        if unknown_artifacts:
            issues.append(
                "Output references artifacts outside scope: " + ", ".join(unknown_artifacts)
            )
        if output.escalation_required and output.escalation_reason:
            issues.append(output.escalation_reason)
        verdict: CritiqueVerdict = "accepted"
        if issues:
            verdict = (
                "escalate_human"
                if reviewer.kind == "guardrail_safety_review"
                or output.escalation_required
                or guardrail.violations
                else "needs_revision"
            )
        return SpecialistCritique(
            reviewed_output_id=output.output_id,
            reviewer_specialist_id=reviewer_specialist_id,
            verdict=verdict,
            issues=list(dict.fromkeys(issues)),
            required_human_review=verdict == "escalate_human",
            guardrail_warnings=[violation.message for violation in guardrail.violations],
            metadata={"reviewer_kind": reviewer.kind},
        )

    def escalate_to_human_review(
        self,
        *,
        task: SpecialistDelegationTask,
        reason: str,
        requested_by: str,
        approval_types: list[str] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> HumanReviewEscalation:
        return HumanReviewEscalation(
            task_id=task.task_id,
            specialist_id=task.specialist_id,
            reason=reason,
            requested_by=requested_by,
            approval_types=sorted(set(approval_types or [])),
            artifact_ids=artifact_ids or [],
            metadata={
                "human_only_approval_types": sorted(HUMAN_ONLY_APPROVALS),
                "subagent_cannot_self_approve": True,
            },
        )

    def _build_plan(
        self,
        *,
        task: SpecialistDelegationTask,
        specialist: SpecialistAgentSpec,
        selected_tools: list[str],
        user_permissions: set[str],
        artifacts: list[dict[str, Any]],
    ) -> RuntimeActionPlan:
        plan_id = f"multi-agent-plan-{uuid4().hex[:12]}"
        steps: list[RuntimeActionStep] = []
        required_approvals: list[str] = []
        risk_level = "low"
        tool_specs: dict[str, dict[str, Any]] = {}
        artifact_ref = task.scoped_artifact_ids[0] if task.scoped_artifact_ids else None
        for index, tool_name in enumerate(selected_tools):
            tool = self.tool_registry.require(tool_name)
            if tool.side_effect_level == "external_write":
                required_approvals.append("external_write")
                risk_level = "high"
            tool_args: dict[str, Any] = {"goal": task.objective}
            if artifact_ref is not None:
                tool_args["artifact_id"] = artifact_ref
            step = RuntimeActionStep(
                step_id=f"multi-agent-step-{uuid4().hex[:12]}",
                plan_id=plan_id,
                step_index=index,
                action_type=tool.tool_name,
                tool_name=tool.tool_name,
                tool_args=tool_args,
                requires_approval=tool.requires_approval_by_default
                or tool.side_effect_level == "external_write",
                approval_reason=(
                    "Human approval required for specialist tool execution."
                    if (
                        tool.requires_approval_by_default
                        or tool.side_effect_level == "external_write"
                    )
                    else None
                ),
                expected_outputs=["structured_specialist_output"],
                status="pending",
                metadata={
                    "specialist_id": specialist.agent_id,
                    "specialist_kind": specialist.kind,
                    "scoped_artifact_ids": task.scoped_artifact_ids,
                    "sandbox_profile": specialist.sandbox_profile,
                },
            )
            steps.append(step)
            tool_specs[tool.tool_name] = {
                "required_permissions": tool.required_permissions,
                "side_effect_level": tool.side_effect_level,
                "policy_tags": tool.policy_tags,
                "tool_package": tool.metadata.get("tool_package"),
                "tool_policy": tool.metadata.get("tool_policy"),
            }
        return RuntimeActionPlan(
            plan_id=plan_id,
            session_id=task.session_id,
            user_goal=task.objective,
            plan_summary=f"{specialist.display_name} delegated runtime plan.",
            steps=steps,
            required_approvals=sorted(set(required_approvals)),
            expected_artifacts=["structured_specialist_output"],
            risk_level=risk_level,  # type: ignore[arg-type]
            guardrail_warnings=[],
            created_by="deterministic_template",
            validated=True,
            validation_errors=[],
            metadata={
                "tool_specs": tool_specs,
                "runtime_context": {
                    "project_id": task.project_id,
                    "org_id": task.org_id,
                    "user_id": task.user_id,
                    "user_permissions": sorted(user_permissions),
                },
                "planner": "multi_agent_specialist_orchestrator",
                "specialist_agent": specialist.model_dump(mode="json"),
                "scoped_artifacts": [
                    artifact
                    for artifact in artifacts
                    if artifact.get("artifact_id") in set(task.scoped_artifact_ids)
                ],
                "sandbox_profile": specialist.sandbox_profile,
                "policy_constraints": specialist.policy_constraints + FORBIDDEN_SUBAGENT_OUTPUTS,
            },
        )

    def _structured_output(
        self,
        delegation: SpecialistDelegationResult,
        execution: RuntimeExecutionResult,
    ) -> SpecialistAgentOutput:
        warnings = list(execution.warnings)
        if execution.status in {"approval_required", "policy_blocked"}:
            warnings.append(
                "Specialist execution stopped before completion by policy or approval."
            )
        if execution.status == "failed":
            warnings.append("Specialist execution failed before producing a final artifact.")
        output = SpecialistAgentOutput(
            task_id=delegation.task.task_id,
            specialist_id=delegation.task.specialist_id,
            summary=(
                f"{delegation.task.specialist_id} completed delegated runtime execution "
                f"with status {execution.status}."
            ),
            grounded_artifact_ids=list(dict.fromkeys(execution.artifact_ids)),
            tool_result_ids=[result.result_id for result in execution.results],
            findings=[
                {
                    "tool_name": result.tool_name,
                    "status": result.status,
                    "artifact_ids": result.artifact_ids,
                    "error_summary": result.error_summary,
                }
                for result in execution.results
            ],
            recommendations=[
                {
                    "type": "human_review" if execution.status == "approval_required" else "review",
                    "text": "Review grounded specialist output before acting on it.",
                }
            ],
            limitations=FORBIDDEN_SUBAGENT_OUTPUTS,
            escalation_required=execution.status in {"approval_required", "policy_blocked"},
            escalation_reason=(
                f"Runtime execution requires human review because status is {execution.status}."
                if execution.status in {"approval_required", "policy_blocked"}
                else None
            ),
            guardrail_warnings=warnings,
            metadata={
                "execution_id": execution.execution_id,
                "runtime_status": execution.status,
                "schema_validated": True,
                "artifact_grounded": True,
            },
        )
        guardrail = self.guardrails.check_output(output.model_dump(mode="json"))
        if guardrail.violations:
            messages = [violation.message for violation in guardrail.violations]
            return output.model_copy(
                update={
                    "escalation_required": True,
                    "escalation_reason": "; ".join(messages),
                    "guardrail_warnings": list(
                        dict.fromkeys([*output.guardrail_warnings, *messages])
                    ),
                }
            )
        return output


def default_specialist_agents() -> list[SpecialistAgentSpec]:
    return [
        _agent(
            "program-manager",
            "program_management",
            "Program Management Agent",
            "Coordinates project plans, run summaries, review handoffs, "
            "and supportable next steps.",
            ["project", "review", "admin", "codex"],
            ["project:read", "project:create", "review:write", "admin:readiness", "codex:run"],
            "read_only_runtime",
        ),
        _agent(
            "evidence-reviewer",
            "evidence_review",
            "Evidence Review Agent",
            "Reviews source-backed ranking and literature artifacts without creating evidence.",
            ["ranking", "literature", "review", "codex"],
            ["run:read", "literature:read", "literature:update", "review:write", "codex:run"],
            "read_only_runtime",
        ),
        _agent(
            "molecule-designer",
            "molecule_design",
            "Molecule Design Agent",
            "Runs opt-in generated-molecule and design workflows as computational hypotheses.",
            ["generation", "developability", "review"],
            ["generation:run", "developability:read", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "developability-safety-triage",
            "developability_safety",
            "Developability And Safety Triage Agent",
            "Assesses computational developability and safety-risk triage artifacts.",
            ["developability", "structure", "review"],
            ["developability:run", "developability:read", "structure:read", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "experimental-feedback",
            "experimental_feedback",
            "Experimental Feedback Agent",
            "Imports, links, and summarizes user-supplied assay result artifacts.",
            ["experiments", "review"],
            ["experiment:write", "experiment:read", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "predictive-modeling",
            "predictive_modeling",
            "Predictive Modeling Agent",
            "Coordinates model dataset, training, validation, and prediction artifacts.",
            ["experiments", "model", "evaluation", "review"],
            ["experiment:read", "model:run", "evaluation:run", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "structure-workflow-reviewer",
            "structure_workflow_review",
            "Structure Workflow Review Agent",
            "Reviews optional structure and docking workflow artifacts without "
            "treating scores as evidence.",
            ["structure", "developability", "review"],
            ["structure:read", "structure:run", "developability:read", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "knowledge-graph-reasoner",
            "knowledge_graph_reasoning",
            "Knowledge Graph Reasoning Agent",
            "Builds and queries provenance-aware graph artifacts without creating graph truth.",
            ["graph", "review", "codex"],
            ["graph:build", "graph:read", "review:write", "codex:run"],
            "artifact_write_runtime",
        ),
        _agent(
            "hypothesis-generator",
            "hypothesis_generation",
            "Hypothesis Generation Agent",
            "Generates graph-backed hypotheses and research questions as planning artifacts.",
            ["hypotheses", "graph", "review"],
            [
                "hypotheses:generate",
                "hypotheses:rank",
                "hypotheses:write",
                "graph:read",
                "review:write",
            ],
            "artifact_write_runtime",
        ),
        _agent(
            "portfolio-campaign-planner",
            "portfolio_campaign_planning",
            "Portfolio And Campaign Planning Agent",
            "Plans portfolios and campaigns while leaving advancement approvals to humans.",
            ["portfolio", "campaign", "review"],
            ["portfolio:run", "campaign:plan", "campaign:write", "review:write"],
            "artifact_write_runtime",
        ),
        _agent(
            "integration-operator",
            "integration_operations",
            "Integration Operations Agent",
            "Runs integration health checks, dry runs, and approved sync planning.",
            ["integration"],
            ["integration:read", "integration:write"],
            "integration_runtime",
        ),
        _agent(
            "evaluation-validator",
            "evaluation_validation",
            "Evaluation And Validation Agent",
            "Runs evaluation, release, reproducibility, and validation workflows.",
            ["evaluation", "admin"],
            ["evaluation:run", "admin:release_check", "admin:readiness"],
            "artifact_write_runtime",
        ),
        _agent(
            "guardrail-safety-reviewer",
            "guardrail_safety_review",
            "Guardrail And Safety Review Agent",
            "Reviews plans, outputs, and state for scientific and platform safety violations.",
            ["evaluation", "review", "admin"],
            ["evaluation:run", "review:write", "admin:readiness"],
            "read_only_runtime",
        ),
        _agent(
            "platform-operator",
            "platform_operations",
            "Platform Operations Agent",
            "Runs platform readiness, support, release, and operational diagnostics.",
            ["admin", "project", "integration"],
            [
                "admin:readiness",
                "admin:release_check",
                "support:bundle",
                "project:read",
                "integration:read",
            ],
            "platform_runtime",
        ),
    ]


def _agent(
    agent_id: str,
    kind: SpecialistAgentKind,
    display_name: str,
    description: str,
    categories: list[str],
    permissions: list[str],
    sandbox_profile: str,
) -> SpecialistAgentSpec:
    return SpecialistAgentSpec(
        agent_id=agent_id,
        kind=kind,
        display_name=display_name,
        description=description,
        allowed_tool_categories=categories,
        allowed_permissions=permissions,
        sandbox_profile=sandbox_profile,
        output_schema={
            "type": "object",
            "required": ["summary", "findings", "recommendations", "limitations"],
            "properties": {
                "summary": {"type": "string"},
                "findings": {"type": "array"},
                "recommendations": {"type": "array"},
                "limitations": {"type": "array"},
                "grounded_artifact_ids": {"type": "array"},
                "escalation_required": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        policy_constraints=FORBIDDEN_SUBAGENT_OUTPUTS,
        metadata={"v2_3_specialist": True},
    )


def _tool_stays_inside_specialist_boundary(policy_tags: list[str]) -> bool:
    return not {"stage_gate", "campaign_advance", "destructive_action"}.intersection(policy_tags)


def _artifact_id_set(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if isinstance(artifact.get("artifact_id"), str)
    }


def _select_tools_for_objective(
    objective: str,
    allowed_tool_names: list[str],
    registry: RuntimeToolRegistry,
) -> list[str]:
    objective_lower = objective.lower()
    matched: list[str] = []
    for tool_name in allowed_tool_names:
        tool = registry.require(tool_name)
        haystack = f"{tool.tool_name} {tool.category} {tool.description}".lower()
        if any(token in haystack for token in _objective_tokens(objective_lower)):
            matched.append(tool_name)
    if not matched:
        for pattern, category in _CATEGORY_PATTERNS:
            if pattern.search(objective_lower):
                matched.extend(
                    name
                    for name in allowed_tool_names
                    if registry.require(name).category == category
                )
    return list(dict.fromkeys(matched))[:3]


def _objective_tokens(objective_lower: str) -> list[str]:
    blocked = {
        "and",
        "for",
        "from",
        "the",
        "with",
        "using",
        "review",
        "agent",
        "artifact",
        "artifacts",
    }
    return [
        token
        for token in re.split(r"[^a-z0-9_]+", objective_lower)
        if len(token) > 3 and token not in blocked
    ]


def _read_only_tools(allowed_tool_names: list[str], registry: RuntimeToolRegistry) -> list[str]:
    return [
        name
        for name in allowed_tool_names
        if registry.require(name).side_effect_level in {"none", "external_read"}
    ]


_CATEGORY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brank|candidate", re.I), "ranking"),
    (re.compile(r"\bliterature|evidence", re.I), "literature"),
    (re.compile(r"\bgenerat|design", re.I), "generation"),
    (re.compile(r"\bdevelopability|safety|admet", re.I), "developability"),
    (re.compile(r"\bassay|experiment", re.I), "experiments"),
    (re.compile(r"\bmodel|predict", re.I), "evaluation"),
    (re.compile(r"\bstructure|dock", re.I), "structure"),
    (re.compile(r"\bgraph", re.I), "graph"),
    (re.compile(r"\bhypothes", re.I), "hypotheses"),
    (re.compile(r"\bportfolio", re.I), "portfolio"),
    (re.compile(r"\bcampaign", re.I), "campaign"),
    (re.compile(r"\bintegration|sync", re.I), "integration"),
    (re.compile(r"\bevaluation|validation|benchmark", re.I), "evaluation"),
    (re.compile(r"\bguardrail|safety", re.I), "evaluation"),
    (re.compile(r"\bplatform|readiness|support|release", re.I), "admin"),
)


def _audit(
    *,
    session_id: str,
    event_type: str,
    actor: str,
    summary: str,
    object_type: str,
    object_id: str,
    metadata: dict[str, Any] | None = None,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"multi-agent-audit-{uuid4().hex[:12]}",
        session_id=session_id,
        event_type=event_type,
        actor=actor,
        timestamp=datetime.now(UTC),
        summary=summary,
        object_type=object_type,
        object_id=object_id,
        before=None,
        after=None,
        metadata=metadata or {},
    )


def specialist_roster_summary() -> list[dict[str, Any]]:
    return [
        {
            "agent_id": spec.agent_id,
            "kind": spec.kind,
            "display_name": spec.display_name,
            "allowed_tool_categories": spec.allowed_tool_categories,
            "sandbox_profile": spec.sandbox_profile,
        }
        for spec in SpecialistAgentRegistry().list_agents()
    ]


def validate_multi_agent_output_schema(output: SpecialistAgentOutput) -> dict[str, Any]:
    payload = output.model_dump(mode="json")
    json.dumps(payload, sort_keys=True)
    return payload


__all__ = [
    "CritiqueVerdict",
    "DelegationStatus",
    "FORBIDDEN_SUBAGENT_OUTPUTS",
    "HUMAN_ONLY_APPROVALS",
    "HumanReviewEscalation",
    "MultiAgentScientificOrchestrator",
    "SpecialistAgentKind",
    "SpecialistAgentOutput",
    "SpecialistAgentRegistry",
    "SpecialistAgentSpec",
    "SpecialistCritique",
    "SpecialistDelegationResult",
    "SpecialistDelegationTask",
    "default_specialist_agents",
    "specialist_roster_summary",
    "validate_multi_agent_output_schema",
]
