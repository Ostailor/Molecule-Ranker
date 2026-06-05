from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RuntimeRunStatus = Literal[
    "succeeded",
    "failed",
    "blocked",
    "approval_required",
    "guardrail_failed",
]
RuntimeStepStatus = Literal["succeeded", "failed", "blocked", "guardrail_failed"]
ToolStatus = Literal["succeeded", "failed"]
ToolExecutor = Callable[[dict[str, object], "RuntimeContext"], "ToolResult"]


RUNTIME_ACTION_TYPES = {
    "create_project",
    "run_ranking",
    "run_generation",
    "run_developability",
    "run_literature_update",
    "run_model_prediction",
    "run_structure_assessment",
    "build_graph",
    "generate_hypotheses",
    "optimize_portfolio",
    "plan_campaign",
    "run_evaluation",
    "create_review_workspace",
    "export_reports",
    "inspect_failed_jobs",
    "generate_support_bundle",
}
FORBIDDEN_ACTION_WARNINGS = {
    "invent_biomedical_evidence": "Codex runtime cannot invent biomedical evidence.",
    "invent_assay_results": "Codex runtime cannot invent assay results.",
    "invent_citations": "Codex runtime cannot invent citations.",
    "invent_molecules": "Codex runtime cannot invent molecules outside the generation pipeline.",
    "change_scores_directly": "Codex runtime cannot change scores directly.",
    "approve_stage_gate": "Codex runtime cannot approve stage gates.",
    "approve_campaign_advancement": "Codex runtime cannot approve campaign advancement.",
    "bypass_validators": "Codex runtime cannot bypass deterministic validators.",
    "bypass_rbac_policy": "Codex runtime cannot bypass RBAC or policy.",
    "provide_medical_advice": "Codex runtime cannot provide medical advice.",
    "provide_lab_protocol": "Codex runtime cannot provide lab protocols.",
    "provide_synthesis_instructions": "Codex runtime cannot provide synthesis instructions.",
    "provide_dosing_guidance": "Codex runtime cannot provide dosing or treatment guidance.",
}
ACTION_PERMISSIONS = {
    "create_project": "projects:create",
    "run_ranking": "ranking:run",
    "run_generation": "generation:run",
    "run_developability": "developability:run",
    "run_literature_update": "literature:update",
    "run_model_prediction": "models:predict",
    "run_structure_assessment": "structure:assess",
    "build_graph": "graph:build",
    "generate_hypotheses": "hypotheses:generate",
    "optimize_portfolio": "portfolio:optimize",
    "plan_campaign": "campaigns:plan",
    "run_evaluation": "evaluation:run",
    "create_review_workspace": "review:create",
    "export_reports": "reports:export",
    "inspect_failed_jobs": "jobs:inspect",
    "generate_support_bundle": "support:bundle",
}
APPROVAL_REQUIRED_ACTIONS: set[str] = set()


class RecoverableToolError(RuntimeError):
    """Raised by deterministic tools when a retry is reasonable."""


class RuntimeContext(BaseModel):
    actor_id: str
    org_id: str
    project_id: str | None = None
    permissions: set[str] = Field(default_factory=set)
    approved_action_types: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("actor_id", "org_id")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class RuntimeAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"runtime-action-{uuid.uuid4().hex[:12]}")
    action_type: str
    parameters: dict[str, object] = Field(default_factory=dict)
    rationale: str = ""

    @field_validator("action_type")
    @classmethod
    def require_non_empty_action_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("action_type must not be empty")
        return value


class RuntimePlan(BaseModel):
    objective: str
    actions: list[RuntimeAction]
    warnings: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    status: ToolStatus
    summary: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    action_type: str
    permission: str
    description: str
    executor: ToolExecutor | None = None
    requires_approval: bool = False
    retry_count: int = 0


class RuntimeStep(BaseModel):
    action_id: str
    action_type: str
    status: RuntimeStepStatus
    summary: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recovery_attempts: int = 0


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"runtime-audit-{uuid.uuid4().hex[:12]}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    component: str
    status: str
    message: str
    actor_id: str
    org_id: str
    project_id: str | None = None
    action_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeRunResult(BaseModel):
    status: RuntimeRunStatus
    objective: str
    plan: RuntimePlan
    steps: list[RuntimeStep] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    review_outputs: list[str] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)


class ToolRegistry:
    """Controlled registry for approved deterministic molecule-ranker tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.action_type not in RUNTIME_ACTION_TYPES:
            raise ValueError(f"Unsupported runtime action type: {spec.action_type}")
        expected_permission = ACTION_PERMISSIONS[spec.action_type]
        if spec.permission != expected_permission:
            raise ValueError(
                f"Runtime action {spec.action_type} must require {expected_permission}."
            )
        self._tools[spec.action_type] = spec

    def get(self, action_type: str) -> ToolSpec | None:
        return self._tools.get(action_type)

    def approved_action_types(self) -> set[str]:
        return set(self._tools)


class AuditLogger:
    """Append-only runtime audit trail with optional JSONL persistence."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.events: list[AuditEvent] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        component: str,
        status: str,
        message: str,
        context: RuntimeContext,
        action_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            component=component,
            status=status,
            message=message,
            actor_id=context.actor_id,
            org_id=context.org_id,
            project_id=context.project_id,
            action_type=action_type,
            metadata=metadata or {},
        )
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        return event


class ActionPlanner:
    """Build a deterministic safe-action plan from a research objective."""

    _patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\bcreate\b.{0,40}\bproject\b", re.I), "create_project"),
        (re.compile(r"\b(?:rank|ranking)\b", re.I), "run_ranking"),
        (re.compile(r"\b(?:generate|generation)\b.{0,80}\bmolecule", re.I), "run_generation"),
        (re.compile(r"\bdevelopability\b", re.I), "run_developability"),
        (re.compile(r"\bliterature\b.{0,40}\b(?:update|refresh)\b", re.I), "run_literature_update"),
        (re.compile(r"\b(?:model prediction|predict)\b", re.I), "run_model_prediction"),
        (
            re.compile(r"\bstructure\b.{0,80}\b(?:assess|assessment|workflow)\b", re.I),
            "run_structure_assessment",
        ),
        (re.compile(r"\b(?:build graph|knowledge graph|graph)\b", re.I), "build_graph"),
        (re.compile(r"\bhypothes", re.I), "generate_hypotheses"),
        (re.compile(r"\boptimi[sz]e\b.{0,40}\bportfolio\b", re.I), "optimize_portfolio"),
        (re.compile(r"\b(?:plan|campaign)\b.{0,40}\bcampaign\b", re.I), "plan_campaign"),
        (re.compile(r"\bevaluation\b|\bbenchmark\b", re.I), "run_evaluation"),
        (re.compile(r"\breview\b.{0,40}\bworkspace\b", re.I), "create_review_workspace"),
        (re.compile(r"\bexport\b.{0,40}\breport", re.I), "export_reports"),
        (
            re.compile(r"\b(?:failed job|failed jobs|inspect failure)\b", re.I),
            "inspect_failed_jobs",
        ),
        (re.compile(r"\bsupport bundle\b", re.I), "generate_support_bundle"),
    )

    def build_plan(
        self,
        objective: str,
        *,
        requested_actions: list[str] | None = None,
        action_parameters: dict[str, dict[str, object]] | None = None,
    ) -> RuntimePlan:
        action_parameters = action_parameters or {}
        action_types = requested_actions or self._infer_actions(objective)
        actions = [
            RuntimeAction(
                action_type=action_type,
                parameters=action_parameters.get(action_type, {}),
                rationale=f"Requested by objective: {objective}",
            )
            for action_type in action_types
        ]
        warnings = [] if actions else ["No approved molecule-ranker action was inferred."]
        return RuntimePlan(objective=objective, actions=actions, warnings=warnings)

    def _infer_actions(self, objective: str) -> list[str]:
        actions: list[str] = []
        for pattern, action_type in self._patterns:
            if pattern.search(objective) and action_type not in actions:
                actions.append(action_type)
        return actions


class PolicyEngine:
    """Enforce runtime registry membership and RBAC permissions."""

    def authorize(
        self,
        action: RuntimeAction,
        spec: ToolSpec | None,
        context: RuntimeContext,
    ) -> list[str]:
        if spec is None:
            return [
                "Runtime action is not registered in the controlled registry: "
                f"{action.action_type}"
            ]
        if spec.permission not in context.permissions:
            return [
                f"Actor {context.actor_id} lacks required permission {spec.permission} "
                f"for {action.action_type}."
            ]
        return []


class ApprovalGate:
    """Require explicit approval for sensitive runtime actions."""

    def pending_approval(self, action: RuntimeAction, spec: ToolSpec) -> str | None:
        requires_approval = (
            spec.requires_approval or action.action_type in APPROVAL_REQUIRED_ACTIONS
        )
        if not requires_approval:
            return None
        return action.action_type

    def is_approved(self, action_type: str, context: RuntimeContext) -> bool:
        return action_type in context.approved_action_types


class ActionExecutor:
    """Execute a registered deterministic tool with bounded retry recovery."""

    def execute(
        self,
        action: RuntimeAction,
        spec: ToolSpec,
        context: RuntimeContext,
        audit_logger: AuditLogger,
    ) -> tuple[ToolResult, int]:
        if spec.executor is None:
            raise RuntimeError(f"Runtime action {action.action_type} has no executor.")
        attempts = 0
        recoveries = 0
        while True:
            try:
                result = spec.executor(dict(action.parameters), context)
                audit_logger.record(
                    component="ActionExecutor",
                    status=result.status,
                    message=f"Executed {action.action_type}.",
                    context=context,
                    action_type=action.action_type,
                    metadata={"attempt": attempts + 1, "recovery_attempts": recoveries},
                )
                return result, recoveries
            except RecoverableToolError as exc:
                audit_logger.record(
                    component="ActionExecutor",
                    status="recoverable_failure",
                    message=str(exc),
                    context=context,
                    action_type=action.action_type,
                    metadata={"attempt": attempts + 1},
                )
                if attempts >= spec.retry_count:
                    return (
                        ToolResult(status="failed", summary=str(exc), warnings=[str(exc)]),
                        recoveries,
                    )
                attempts += 1
                recoveries += 1
            except Exception as exc:
                audit_logger.record(
                    component="ActionExecutor",
                    status="failed",
                    message=str(exc),
                    context=context,
                    action_type=action.action_type,
                    metadata={"attempt": attempts + 1},
                )
                return (
                    ToolResult(status="failed", summary=str(exc), warnings=[str(exc)]),
                    recoveries,
                )


class ArtifactValidator:
    """Validate that tool outputs are reviewable registered artifact references."""

    _required_artifact_keys = {"artifact_id", "artifact_type", "sha256"}

    def validate(self, result: ToolResult) -> list[str]:
        warnings: list[str] = []
        for index, artifact in enumerate(result.artifacts):
            missing = sorted(self._required_artifact_keys - set(artifact))
            if missing:
                warnings.append(
                    f"Artifact at index {index} is missing required keys: {', '.join(missing)}."
                )
                continue
            for key in self._required_artifact_keys:
                value = artifact.get(key)
                if not isinstance(value, str) or not value.strip():
                    warnings.append(f"Artifact at index {index} has invalid {key}.")
        return warnings


class GuardrailChecker:
    """Block prohibited biomedical, policy, and deterministic-validator bypasses."""

    _objective_patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(
                r"\b(?:invent|fabricate|make up)\b.*"
                r"\b(?:evidence|citation|pmid|doi)\b",
                re.I | re.S,
            ),
            "Codex runtime cannot invent biomedical evidence, citations, or source records.",
        ),
        (
            re.compile(r"\b(?:invent|fabricate|make up)\b.*\bassay\b", re.I | re.S),
            "Codex runtime cannot invent assay results.",
        ),
        (
            re.compile(
                r"\bchange\b.{0,30}\bscores?\b|\bscores?\b.{0,30}\bdirectly\b",
                re.I,
            ),
            "Codex runtime cannot change scores directly.",
        ),
        (
            re.compile(r"\bapprove\b.{0,40}\bstage gate\b", re.I),
            "Codex runtime cannot approve stage gates.",
        ),
        (
            re.compile(r"\bapprove\b.{0,40}\bcampaign\b", re.I),
            "Codex runtime cannot approve campaign advancement.",
        ),
        (
            re.compile(
                r"\bbypass\b.{0,40}\b(?:validator|validation|guardrail|rbac|policy)\b",
                re.I,
            ),
            "Codex runtime cannot bypass deterministic validators, RBAC, policy, or guardrails.",
        ),
        (
            re.compile(r"\bmedical advice\b|\btreatment guidance\b", re.I),
            "Codex runtime cannot provide medical advice or patient treatment guidance.",
        ),
        (
            re.compile(r"\blab protocols?\b", re.I),
            "Codex runtime cannot provide lab protocols.",
        ),
        (
            re.compile(
                r"\bsynthesis (?:route|instruction|protocol)s?\b|\bretrosynthesis\b",
                re.I,
            ),
            "Codex runtime cannot provide synthesis instructions.",
        ),
        (
            re.compile(r"\bdos(?:e|ing)\b|\bmg/kg\b|\bmg/day\b", re.I),
            "Codex runtime cannot provide dosing guidance.",
        ),
    )
    _output_patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(
                r"\b(?:safe|active|effective|efficacious|binding|binds?|synthesizable)\b",
                re.I,
            ),
            "Codex runtime output claims molecules are safe, active, effective, "
            "binding, or synthesizable.",
        ),
        (
            re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I),
            "Codex runtime output appears to invent quantitative assay results.",
        ),
        (
            re.compile(r"\bPMID:?\s*\d{4,9}\b|\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I),
            "Codex runtime output cites literature identifiers that must come from artifacts.",
        ),
        (
            re.compile(r"\b(?:synthesis route|lab protocol|dosing|mg/kg|mg/day)\b", re.I),
            "Codex runtime output contains prohibited protocol, synthesis, or dosing content.",
        ),
    )

    def check_plan(self, objective: str, actions: list[RuntimeAction]) -> list[str]:
        warnings: list[str] = []
        for action in actions:
            warning = FORBIDDEN_ACTION_WARNINGS.get(action.action_type)
            if warning:
                warnings.append(warning)
        for pattern, warning in self._objective_patterns:
            if pattern.search(objective):
                warnings.append(warning)
        return _dedupe(warnings)

    def check_output(self, result: ToolResult) -> list[str]:
        text = " ".join([result.summary, json.dumps(result.data, sort_keys=True, default=str)])
        warnings = [warning for pattern, warning in self._output_patterns if pattern.search(text)]
        return _dedupe(warnings)


class CodexRuntimeAgent:
    """V2.5 runtime backbone for controlled molecule-ranker tool execution."""

    def __init__(
        self,
        *,
        planner: ActionPlanner | None = None,
        registry: ToolRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        approval_gate: ApprovalGate | None = None,
        executor: ActionExecutor | None = None,
        artifact_validator: ArtifactValidator | None = None,
        guardrail_checker: GuardrailChecker | None = None,
        audit_log_path: Path | str | None = None,
    ) -> None:
        self.planner = planner or ActionPlanner()
        self.registry = registry or ToolRegistry()
        self.policy_engine = policy_engine or PolicyEngine()
        self.approval_gate = approval_gate or ApprovalGate()
        self.executor = executor or ActionExecutor()
        self.artifact_validator = artifact_validator or ArtifactValidator()
        self.guardrail_checker = guardrail_checker or GuardrailChecker()
        self.audit_logger = AuditLogger(audit_log_path)

    def run(
        self,
        objective: str,
        context: RuntimeContext,
        *,
        requested_actions: list[str] | None = None,
        action_parameters: dict[str, dict[str, object]] | None = None,
    ) -> RuntimeRunResult:
        start_index = len(self.audit_logger.events)
        self.audit_logger.record(
            component="AuditLogger",
            status="started",
            message="Started V2.5 Codex runtime audit trail.",
            context=context,
        )
        plan = self.planner.build_plan(
            objective,
            requested_actions=requested_actions,
            action_parameters=action_parameters,
        )
        self.audit_logger.record(
            component="ActionPlanner",
            status="planned",
            message=f"Built runtime plan with {len(plan.actions)} action(s).",
            context=context,
            metadata={"actions": [action.action_type for action in plan.actions]},
        )
        plan_warnings = self.guardrail_checker.check_plan(objective, plan.actions)
        if plan_warnings:
            self.audit_logger.record(
                component="GuardrailChecker",
                status="guardrail_failed",
                message="Runtime plan failed guardrails.",
                context=context,
                metadata={"warnings": plan_warnings},
            )
            return self._result(
                status="guardrail_failed",
                objective=objective,
                plan=plan,
                context=context,
                start_index=start_index,
                guardrail_warnings=plan_warnings,
            )
        if not plan.actions:
            return self._result(
                status="blocked",
                objective=objective,
                plan=plan,
                context=context,
                start_index=start_index,
                guardrail_warnings=plan.warnings,
            )

        pending_approvals: list[str] = []
        blocked_warnings: list[str] = []
        resolved_specs: dict[str, ToolSpec] = {}
        for action in plan.actions:
            spec = self.registry.get(action.action_type)
            self.audit_logger.record(
                component="ToolRegistry",
                status="resolved" if spec else "blocked",
                message=(
                    f"Resolved registered tool for {action.action_type}."
                    if spec
                    else f"No registered tool for {action.action_type}."
                ),
                context=context,
                action_type=action.action_type,
            )
            policy_warnings = self.policy_engine.authorize(action, spec, context)
            self.audit_logger.record(
                component="PolicyEngine",
                status="passed" if not policy_warnings else "blocked",
                message=(
                    f"Policy passed for {action.action_type}."
                    if not policy_warnings
                    else "; ".join(policy_warnings)
                ),
                context=context,
                action_type=action.action_type,
                metadata={"warnings": policy_warnings},
            )
            blocked_warnings.extend(policy_warnings)
            if spec is None:
                continue
            approval = self.approval_gate.pending_approval(action, spec)
            if approval and not self.approval_gate.is_approved(approval, context):
                pending_approvals.append(approval)
                self.audit_logger.record(
                    component="ApprovalGate",
                    status="approval_required",
                    message=f"Approval required for {action.action_type}.",
                    context=context,
                    action_type=action.action_type,
                )
            else:
                self.audit_logger.record(
                    component="ApprovalGate",
                    status="passed",
                    message=f"Approval gate passed for {action.action_type}.",
                    context=context,
                    action_type=action.action_type,
                )
            resolved_specs[action.action_id] = spec

        if blocked_warnings:
            return self._result(
                status="blocked",
                objective=objective,
                plan=plan,
                context=context,
                start_index=start_index,
                guardrail_warnings=_dedupe(blocked_warnings),
            )
        if pending_approvals:
            return self._result(
                status="approval_required",
                objective=objective,
                plan=plan,
                context=context,
                start_index=start_index,
                pending_approvals=_dedupe(pending_approvals),
            )

        steps: list[RuntimeStep] = []
        review_outputs: list[str] = []
        guardrail_warnings: list[str] = []
        for action in plan.actions:
            result, recovery_attempts = self.executor.execute(
                action,
                resolved_specs[action.action_id],
                context,
                self.audit_logger,
            )
            artifact_warnings = self.artifact_validator.validate(result)
            self.audit_logger.record(
                component="ArtifactValidator",
                status="passed" if not artifact_warnings else "failed",
                message=(
                    f"Validated artifacts for {action.action_type}."
                    if not artifact_warnings
                    else "; ".join(artifact_warnings)
                ),
                context=context,
                action_type=action.action_type,
                metadata={"warnings": artifact_warnings},
            )
            output_warnings = self.guardrail_checker.check_output(result)
            self.audit_logger.record(
                component="GuardrailChecker",
                status="passed" if not output_warnings else "guardrail_failed",
                message=(
                    f"Output guardrails passed for {action.action_type}."
                    if not output_warnings
                    else "; ".join(output_warnings)
                ),
                context=context,
                action_type=action.action_type,
                metadata={"warnings": output_warnings},
            )
            warnings = _dedupe(result.warnings + artifact_warnings + output_warnings)
            step_status: RuntimeStepStatus = result.status
            if artifact_warnings:
                step_status = "failed"
            if output_warnings:
                step_status = "guardrail_failed"
            step = RuntimeStep(
                action_id=action.action_id,
                action_type=action.action_type,
                status=step_status,
                summary=result.summary,
                artifacts=result.artifacts,
                warnings=warnings,
                recovery_attempts=recovery_attempts,
            )
            steps.append(step)
            if output_warnings:
                guardrail_warnings.extend(output_warnings)
                return self._result(
                    status="guardrail_failed",
                    objective=objective,
                    plan=plan,
                    context=context,
                    start_index=start_index,
                    steps=steps,
                    guardrail_warnings=_dedupe(guardrail_warnings),
                    review_outputs=review_outputs,
                )
            if artifact_warnings or result.status == "failed":
                return self._result(
                    status="failed",
                    objective=objective,
                    plan=plan,
                    context=context,
                    start_index=start_index,
                    steps=steps,
                    guardrail_warnings=warnings,
                    review_outputs=review_outputs,
                )
            artifact_ids = [
                str(artifact["artifact_id"])
                for artifact in result.artifacts
                if isinstance(artifact.get("artifact_id"), str)
            ]
            review_outputs.append(
                f"{action.action_type} completed; artifact IDs: {', '.join(artifact_ids)}"
            )

        return self._result(
            status="succeeded",
            objective=objective,
            plan=plan,
            context=context,
            start_index=start_index,
            steps=steps,
            review_outputs=review_outputs,
        )

    def _result(
        self,
        *,
        status: RuntimeRunStatus,
        objective: str,
        plan: RuntimePlan,
        context: RuntimeContext,
        start_index: int,
        steps: list[RuntimeStep] | None = None,
        pending_approvals: list[str] | None = None,
        guardrail_warnings: list[str] | None = None,
        review_outputs: list[str] | None = None,
    ) -> RuntimeRunResult:
        self.audit_logger.record(
            component="AuditLogger",
            status=status,
            message=f"Completed V2.5 Codex runtime run with status {status}.",
            context=context,
            metadata={
                "pending_approvals": pending_approvals or [],
                "guardrail_warnings": guardrail_warnings or [],
            },
        )
        return RuntimeRunResult(
            status=status,
            objective=objective,
            plan=plan,
            steps=steps or [],
            pending_approvals=pending_approvals or [],
            guardrail_warnings=guardrail_warnings or [],
            review_outputs=review_outputs or [],
            audit_events=self.audit_logger.events[start_index:],
        )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped
