from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.recovery import RuntimeFailureRecovery
from molecule_ranker.runtime_agents.schemas import (
    AutonomyLevel,
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

RepairFailureKind = Literal[
    "failed_tool",
    "failed_job",
    "failed_validation",
    "missing_artifact",
    "guardrail_failure",
    "permission_denied",
    "unknown_failure",
]
EvaluationScope = Literal["plan", "output"]
EvaluationVerdict = Literal["pass", "warn", "block"]
RepairDecisionStatus = Literal["auto_allowed", "approval_required", "blocked"]
RepairReportStatus = Literal[
    "repair_succeeded",
    "repair_failed",
    "approval_required",
    "blocked",
    "no_repair_available",
]
RepairPatternOutcome = Literal["succeeded", "failed", "approval_required", "blocked"]

SAFE_REPAIR_SIDE_EFFECTS = {"none", "artifact_write", "external_read"}
RISKY_REPAIR_SIDE_EFFECTS = {"db_write", "external_write", "codex_subprocess"}
SCIENTIFIC_TRUTH_MUTATION_KEYS = {
    "assay_result",
    "assay_results",
    "benchmark_metric",
    "benchmark_metrics",
    "citation",
    "citations",
    "evidence_item",
    "evidence_items",
    "graph_edge",
    "graph_edges",
    "molecule",
    "molecules",
    "score",
    "scores",
    "scientific_score",
    "scientific_scores",
}
REPAIR_ARTIFACT_FILENAMES = {
    "report": "runtime_repair_report.json",
    "summary": "runtime_repair_summary.md",
}

ToolHandler = Callable[[RuntimeActionStep, RuntimeToolSpec], RuntimeToolResult | dict[str, Any]]


class AgentEvaluationFinding(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "block"] = "warning"
    object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvaluationReport(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: f"agent-eval-{uuid4().hex[:12]}")
    scope: EvaluationScope
    verdict: EvaluationVerdict
    findings: list[AgentEvaluationFinding] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureDiagnosis(BaseModel):
    diagnosis_id: str = Field(default_factory=lambda: f"failure-diagnosis-{uuid4().hex[:12]}")
    failure_kind: RepairFailureKind
    failure_type: str
    diagnosis: str
    affected_tool_name: str | None = None
    affected_artifact_ids: list[str] = Field(default_factory=list)
    affected_job_ids: list[str] = Field(default_factory=list)
    safe_next_actions: list[str] = Field(default_factory=list)
    approval_required: bool = False
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPlan(BaseModel):
    repair_plan_id: str = Field(default_factory=lambda: f"repair-plan-{uuid4().hex[:12]}")
    diagnosis_id: str
    plan_summary: str
    steps: list[RuntimeActionStep] = Field(default_factory=list)
    max_attempts: int = Field(default=1, ge=1, le=5)
    requires_human_approval: bool = False
    approval_reason: str | None = None
    blocked_reason: str | None = None
    expected_regression_checks: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPolicyDecision(BaseModel):
    status: RepairDecisionStatus
    reason: str
    allowed_attempts: int = Field(default=0, ge=0)
    required_approval_types: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegressionCheckReport(BaseModel):
    check_id: str = Field(default_factory=lambda: f"regression-check-{uuid4().hex[:12]}")
    passed: bool
    checks_run: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    artifact_ids_checked: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPattern(BaseModel):
    pattern_id: str = Field(default_factory=lambda: f"repair-pattern-{uuid4().hex[:12]}")
    failure_type: str
    failure_kind: RepairFailureKind
    repair_tool_name: str | None = None
    outcome: RepairPatternOutcome
    summary: str
    attempts: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairReport(BaseModel):
    repair_report_id: str = Field(default_factory=lambda: f"repair-report-{uuid4().hex[:12]}")
    status: RepairReportStatus
    diagnosis: FailureDiagnosis
    repair_plan: RepairPlan | None = None
    policy_decision: RepairPolicyDecision | None = None
    attempts: list[RuntimeToolResult] = Field(default_factory=list)
    regression_report: RegressionCheckReport | None = None
    evaluation_reports: list[AgentEvaluationReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairEvalTask(BaseModel):
    task_id: str
    description: str
    failure: dict[str, Any]
    autonomy_level: AutonomyLevel = "execute_safe_tools"
    should_auto_execute: bool
    expected_status: RepairReportStatus


class RepairEvalTaskResult(BaseModel):
    task_id: str
    status: Literal["passed", "failed", "failed_safely"]
    report_status: RepairReportStatus
    auto_executed: bool
    regression_passed: bool
    approval_required: bool
    blocked_scientific_repair: bool
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairEvalSuiteResult(BaseModel):
    suite: str
    task_count: int
    task_results: list[RepairEvalTaskResult]
    metrics: dict[str, float]
    started_at: datetime
    completed_at: datetime


class AgentReliabilityDashboard(BaseModel):
    repair_count: int
    succeeded: int
    approval_required: int
    blocked: int
    failed: int
    top_failure_types: list[dict[str, Any]]
    recent_reports: list[dict[str, Any]]
    metrics: dict[str, float]


class RepairMemory:
    """JSON-backed operational memory for reusable repair patterns."""

    def __init__(self, path: str | Path = ".omx/state/runtime_agents/repair_memory.json") -> None:
        self.path = Path(path)

    def learn(self, report: RepairReport) -> RepairPattern:
        _enforce_no_scientific_truth_payload(report.model_dump(mode="python"))
        pattern = RepairPattern(
            failure_type=report.diagnosis.failure_type,
            failure_kind=report.diagnosis.failure_kind,
            repair_tool_name=report.repair_plan.steps[0].tool_name
            if report.repair_plan and report.repair_plan.steps
            else None,
            outcome=_pattern_outcome(report.status),
            summary=(
                report.diagnosis.diagnosis
                if len(report.diagnosis.diagnosis) <= 240
                else report.diagnosis.diagnosis[:237] + "..."
            ),
            attempts=len(report.attempts),
            provenance={
                "repair_report_id": report.repair_report_id,
                "operational_context_only": True,
                "not_biomedical_evidence": True,
                "cannot_override_scientific_scores": True,
            },
        )
        state = self._read_state()
        state.append(pattern.model_dump(mode="json"))
        self._write_state(state)
        return pattern

    def retrieve(self, failure_type: str, *, limit: int = 5) -> list[RepairPattern]:
        records = [
            RepairPattern.model_validate(item)
            for item in self._read_state()
            if isinstance(item, dict) and item.get("failure_type") == failure_type
        ]
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[:limit]

    def _read_state(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _write_state(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")


class SelfEvaluationAgent:
    """Evaluate agent plans and outputs before they are trusted or executed."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.guardrails = RuntimeGuardrailChecker(registry=self.registry)

    def evaluate_plan(
        self,
        plan: RuntimeActionPlan,
        *,
        approvals: set[str] | None = None,
        known_artifacts: set[str] | None = None,
        user_permissions: set[str] | None = None,
    ) -> AgentEvaluationReport:
        findings: list[AgentEvaluationFinding] = []
        if not plan.validated:
            findings.append(_finding("plan_not_validated", "Plan is not validated.", "block"))
        for step in plan.steps:
            spec = self.registry.get(step.tool_name)
            if spec is None:
                findings.append(
                    _finding(
                        "unknown_tool",
                        f"Unknown tool: {step.tool_name}.",
                        "block",
                        step.step_id,
                    )
                )
                continue
            if _contains_scientific_truth_mutation(step.tool_args):
                findings.append(
                    _finding(
                        "scientific_truth_repair_attempt",
                        "Plan attempts to create or modify scientific truth during repair.",
                        "block",
                        step.step_id,
                    )
                )
            if spec.side_effect_level in RISKY_REPAIR_SIDE_EFFECTS and not step.requires_approval:
                findings.append(
                    _finding(
                        "risky_step_missing_approval",
                        f"{step.tool_name} requires explicit approval for repair execution.",
                        "block",
                        step.step_id,
                    )
                )
        guardrail = self.guardrails.check_plan(
            plan,
            approvals=approvals or set(),
            known_artifacts=known_artifacts or set(),
            user_permissions=user_permissions,
        )
        findings.extend(
            _finding(
                violation.code,
                violation.message,
                "block" if violation.severity == "block" else "warning",
                violation.object_id,
            )
            for violation in guardrail.violations
        )
        return AgentEvaluationReport(
            scope="plan",
            verdict=_verdict(findings),
            findings=findings,
            metadata={"plan_id": plan.plan_id},
        )

    def evaluate_output(
        self,
        result: RuntimeToolResult | dict[str, Any] | str,
    ) -> AgentEvaluationReport:
        guardrail = self.guardrails.check_output(
            result,
            known_citations=set(),
            known_molecules=set(),
        )
        findings = [
            _finding(
                violation.code,
                violation.message,
                "block" if violation.severity == "block" else "warning",
                violation.object_id,
            )
            for violation in guardrail.violations
        ]
        payload = (
            result.model_dump(mode="python")
            if isinstance(result, RuntimeToolResult)
            else result
        )
        if _contains_scientific_truth_mutation(payload):
            findings.append(
                _finding(
                    "scientific_truth_output_attempt",
                    "Output attempts to create or modify scientific truth during repair.",
                    "block",
                )
            )
        return AgentEvaluationReport(
            scope="output",
            verdict=_verdict(findings),
            findings=findings,
        )


class FailureDiagnosisAgent:
    """Diagnose tool, job, validation, artifact, and guardrail failures."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.recovery = RuntimeFailureRecovery(registry=self.registry)

    def diagnose(self, failure: str | Mapping[str, Any] | RuntimeToolResult) -> FailureDiagnosis:
        suggestion = self.recovery.diagnose(failure)
        payload = _failure_payload(failure)
        return FailureDiagnosis(
            failure_kind=_failure_kind(payload, suggestion.failure_type),
            failure_type=suggestion.failure_type,
            diagnosis=suggestion.diagnosis,
            affected_tool_name=_string_or_none(payload.get("tool_name")),
            affected_artifact_ids=_string_list(
                payload.get("artifact_ids") or payload.get("artifact_id")
            ),
            affected_job_ids=_string_list(payload.get("job_ids") or payload.get("job_id")),
            safe_next_actions=suggestion.safe_next_actions,
            approval_required=suggestion.approval_required,
            guardrails=suggestion.guardrails,
            metadata={
                **suggestion.metadata,
                "recovery_tool_name": suggestion.recovery_tool_name,
                "recovery_tool_args": suggestion.recovery_tool_args,
                "approval_reason": suggestion.approval_reason,
            },
        )


class RepairPlannerAgent:
    """Turn failure diagnoses into reviewable engineering repair plans."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.recovery = RuntimeFailureRecovery(registry=self.registry)

    def propose_repair(self, diagnosis: FailureDiagnosis) -> RepairPlan:
        tool_name = diagnosis.metadata.get("recovery_tool_name")
        tool_args = diagnosis.metadata.get("recovery_tool_args")
        if _contains_scientific_truth_mutation(diagnosis.model_dump(mode="python")):
            return RepairPlan(
                diagnosis_id=diagnosis.diagnosis_id,
                plan_summary="Repair blocked: scientific truth cannot be repaired by agents.",
                blocked_reason=(
                    "Agents may repair workflows but may not invent or modify "
                    "scientific truth."
                ),
                requires_human_approval=True,
                approval_reason=(
                    "Scientific data or score mutation must be handled outside agent repair."
                ),
                guardrails=diagnosis.guardrails,
            )
        if not isinstance(tool_name, str) or not tool_name:
            return RepairPlan(
                diagnosis_id=diagnosis.diagnosis_id,
                plan_summary="No deterministic repair tool is available.",
                blocked_reason="No deterministic registered repair tool matched this failure.",
                guardrails=diagnosis.guardrails,
            )
        spec = self.registry.get(tool_name)
        if spec is None:
            return RepairPlan(
                diagnosis_id=diagnosis.diagnosis_id,
                plan_summary=f"Repair blocked: {tool_name} is not registered.",
                blocked_reason=f"Repair tool is not registered: {tool_name}.",
                guardrails=diagnosis.guardrails,
            )
        plan_id = f"repair-action-plan-{uuid4().hex[:12]}"
        step = RuntimeActionStep(
            step_id=f"repair-step-{uuid4().hex[:12]}",
            plan_id=plan_id,
            step_index=0,
            action_type="repair_failure",
            tool_name=tool_name,
            tool_args=tool_args
            if isinstance(tool_args, dict)
            else {"failure_type": diagnosis.failure_type},
            requires_approval=diagnosis.approval_required
            or spec.requires_approval_by_default
            or spec.side_effect_level in RISKY_REPAIR_SIDE_EFFECTS,
            approval_reason=_approval_reason(diagnosis, spec),
            expected_outputs=[],
            status="pending",
            result_id=None,
            warnings=[],
            metadata={
                "diagnosis_id": diagnosis.diagnosis_id,
                "failure_type": diagnosis.failure_type,
                "repair_step": True,
            },
        )
        return RepairPlan(
            diagnosis_id=diagnosis.diagnosis_id,
            plan_summary=(
                f"Run deterministic repair tool `{tool_name}` for "
                f"{diagnosis.failure_type}."
            ),
            steps=[step],
            max_attempts=_bounded_attempts(diagnosis.failure_type, spec),
            requires_human_approval=step.requires_approval,
            approval_reason=step.approval_reason,
            expected_regression_checks=[
                "repair_output_guardrails",
                "repair_artifact_provenance",
                "deterministic_validation_rerun",
            ],
            guardrails=diagnosis.guardrails,
            metadata={
                "tool_specs": {
                    tool_name: {
                        "required_permissions": spec.required_permissions,
                        "side_effect_level": spec.side_effect_level,
                        "policy_tags": spec.policy_tags,
                    }
                }
            },
        )


class RepairPolicyEngine:
    """Decide whether repair plans can run autonomously, need approval, or are blocked."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()

    def evaluate(
        self,
        plan: RepairPlan,
        *,
        autonomy_level: AutonomyLevel,
        actor: str = "codex",
    ) -> RepairPolicyDecision:
        if plan.blocked_reason:
            return RepairPolicyDecision(
                status="blocked",
                reason=plan.blocked_reason,
                blocked_reasons=[plan.blocked_reason],
            )
        if not plan.steps:
            return RepairPolicyDecision(
                status="blocked",
                reason="Repair plan has no executable steps.",
                blocked_reasons=["empty_repair_plan"],
            )
        if _contains_scientific_truth_mutation(plan.model_dump(mode="python")):
            return RepairPolicyDecision(
                status="blocked",
                reason="Repair plan attempts to create or modify scientific truth.",
                blocked_reasons=["scientific_truth_repair_blocked"],
            )
        required_approvals: list[str] = []
        blocked: list[str] = []
        for step in plan.steps:
            spec = self.registry.get(step.tool_name)
            if spec is None:
                blocked.append(f"unknown_tool:{step.tool_name}")
                continue
            if spec.side_effect_level not in SAFE_REPAIR_SIDE_EFFECTS:
                required_approvals.append(spec.side_effect_level)
            if spec.category == "codex":
                required_approvals.append("codex_repair")
            if not spec.idempotent and spec.side_effect_level != "artifact_write":
                required_approvals.append("non_idempotent_repair")
        if blocked:
            return RepairPolicyDecision(
                status="blocked",
                reason="Repair plan contains blocked steps.",
                blocked_reasons=blocked,
            )
        if plan.requires_human_approval or required_approvals:
            return RepairPolicyDecision(
                status="approval_required",
                reason=plan.approval_reason or "Repair requires human approval.",
                required_approval_types=sorted(set(required_approvals or ["repair_approval"])),
            )
        if autonomy_level not in {
            "execute_safe_tools",
            "execute_with_approval",
            "full_auto_restricted",
        }:
            return RepairPolicyDecision(
                status="approval_required",
                reason=f"Autonomy level {autonomy_level} cannot execute repair tools.",
                required_approval_types=["execute_repair"],
            )
        if actor == "codex" and any(step.requires_approval for step in plan.steps):
            return RepairPolicyDecision(
                status="approval_required",
                reason="Codex cannot approve its own repair.",
                required_approval_types=["human_repair_approval"],
            )
        return RepairPolicyDecision(
            status="auto_allowed",
            reason="Repair is bounded to registered safe deterministic tools.",
            allowed_attempts=plan.max_attempts,
        )


class RegressionCheckAgent:
    """Run deterministic post-repair checks without accepting scientific claims as truth."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.evaluator = SelfEvaluationAgent(registry=self.registry)

    def run(
        self,
        *,
        repair_plan: RepairPlan,
        attempts: list[RuntimeToolResult],
        expected_artifacts: set[str] | None = None,
    ) -> RegressionCheckReport:
        checks = ["repair_output_guardrails", "repair_artifact_provenance"]
        failures: list[str] = []
        artifact_ids: list[str] = []
        for result in attempts:
            output_eval = self.evaluator.evaluate_output(result)
            if output_eval.verdict == "block":
                failures.extend(finding.message for finding in output_eval.findings)
            artifact_ids.extend(result.artifact_ids)
            provenance = result.metadata.get("artifact_provenance")
            if result.artifact_ids and not isinstance(provenance, dict):
                failures.append(f"{result.tool_name} produced artifacts without provenance.")
            elif isinstance(provenance, dict):
                missing = [
                    artifact_id
                    for artifact_id in result.artifact_ids
                    if artifact_id not in provenance
                ]
                failures.extend(
                    f"Missing provenance for artifact {artifact_id}."
                    for artifact_id in missing
                )
        required = expected_artifacts or set()
        missing_required = sorted(required.difference(artifact_ids))
        failures.extend(
            f"Expected artifact was not produced: {artifact_id}."
            for artifact_id in missing_required
        )
        if repair_plan.expected_regression_checks:
            checks.extend(repair_plan.expected_regression_checks)
        return RegressionCheckReport(
            passed=not failures,
            checks_run=list(dict.fromkeys(checks)),
            failures=failures,
            artifact_ids_checked=artifact_ids,
        )


class RepairExecutor:
    """Autonomous bounded repair loop for safe workflow repairs."""

    def __init__(
        self,
        *,
        registry: RuntimeToolRegistry | None = None,
        tool_handlers: dict[str, ToolHandler] | None = None,
        memory: RepairMemory | None = None,
    ) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.tool_handlers = tool_handlers or {}
        self.diagnoser = FailureDiagnosisAgent(registry=self.registry)
        self.planner = RepairPlannerAgent(registry=self.registry)
        self.policy = RepairPolicyEngine(registry=self.registry)
        self.regression = RegressionCheckAgent(registry=self.registry)
        self.memory = memory

    def repair(
        self,
        failure: str | Mapping[str, Any] | RuntimeToolResult,
        *,
        autonomy_level: AutonomyLevel,
        actor: str = "codex",
    ) -> RepairReport:
        started = datetime.now(UTC)
        diagnosis = self.diagnoser.diagnose(failure)
        repair_plan = self.planner.propose_repair(diagnosis)
        decision = self.policy.evaluate(repair_plan, autonomy_level=autonomy_level, actor=actor)
        audit_events = [
            _audit_event("repair_diagnosed", diagnosis.diagnosis_id, diagnosis.diagnosis),
            _audit_event("repair_planned", repair_plan.repair_plan_id, repair_plan.plan_summary),
            _audit_event("repair_policy_evaluated", repair_plan.repair_plan_id, decision.reason),
        ]
        if decision.status == "blocked":
            report = RepairReport(
                status="blocked",
                diagnosis=diagnosis,
                repair_plan=repair_plan,
                policy_decision=decision,
                audit_events=audit_events,
                started_at=started,
                completed_at=datetime.now(UTC),
                warnings=decision.blocked_reasons,
            )
            self._learn(report)
            return report
        if decision.status == "approval_required":
            report = RepairReport(
                status="approval_required",
                diagnosis=diagnosis,
                repair_plan=repair_plan,
                policy_decision=decision,
                audit_events=audit_events,
                started_at=started,
                completed_at=datetime.now(UTC),
                warnings=[decision.reason],
            )
            self._learn(report)
            return report
        if not repair_plan.steps:
            report = RepairReport(
                status="no_repair_available",
                diagnosis=diagnosis,
                repair_plan=repair_plan,
                policy_decision=decision,
                audit_events=audit_events,
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            self._learn(report)
            return report

        attempts: list[RuntimeToolResult] = []
        warnings: list[str] = []
        step = repair_plan.steps[0]
        spec = self.registry.require(step.tool_name)
        handler = self.tool_handlers.get(step.tool_name)
        if handler is None:
            report = RepairReport(
                status="repair_failed",
                diagnosis=diagnosis,
                repair_plan=repair_plan,
                policy_decision=decision,
                attempts=[],
                audit_events=audit_events,
                warnings=["No deterministic repair handler configured."],
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            self._learn(report)
            return report
        for attempt_index in range(decision.allowed_attempts):
            attempt_step = step.model_copy(
                update={
                    "step_id": f"{step.step_id}-attempt-{attempt_index + 1}",
                    "status": "running",
                    "metadata": {**step.metadata, "attempt_index": attempt_index + 1},
                },
                deep=True,
            )
            try:
                result = _normalize_tool_result(attempt_step, handler(attempt_step, spec))
            except Exception as exc:
                result = _failed_result(attempt_step, spec.tool_name, str(exc))
            attempts.append(result)
            audit_events.append(
                _audit_event(
                    "repair_attempt_completed",
                    result.result_id,
                    f"{spec.tool_name} attempt {attempt_index + 1}: {result.status}.",
                )
            )
            if result.status == "succeeded":
                break
            warnings.append(result.error_summary or f"{result.tool_name} failed.")

        regression = self.regression.run(repair_plan=repair_plan, attempts=attempts)
        status: RepairReportStatus = (
            "repair_succeeded"
            if attempts and attempts[-1].status == "succeeded" and regression.passed
            else "repair_failed"
        )
        report = RepairReport(
            status=status,
            diagnosis=diagnosis,
            repair_plan=repair_plan,
            policy_decision=decision,
            attempts=attempts,
            regression_report=regression,
            warnings=[*warnings, *regression.failures],
            audit_events=audit_events,
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        self._learn(report)
        return report

    def _learn(self, report: RepairReport) -> None:
        if self.memory is not None:
            self.memory.learn(report)


def run_repair_eval_suite(
    *,
    suite: str = "repair",
    registry: RuntimeToolRegistry | None = None,
) -> RepairEvalSuiteResult:
    if suite != "repair":
        raise ValueError(f"Unknown repair eval suite: {suite}")
    active_registry = registry or RuntimeToolRegistry.default()
    started = datetime.now(UTC)
    results = [_run_repair_eval_task(task, active_registry) for task in REPAIR_EVAL_TASKS]
    completed = datetime.now(UTC)
    return RepairEvalSuiteResult(
        suite=suite,
        task_count=len(results),
        task_results=results,
        metrics={
            "pass_rate": _rate(result.status in {"passed", "failed_safely"} for result in results),
            "safe_auto_execution_rate": _rate(
                result.auto_executed
                for result in results
                if _task_by_id(result.task_id).should_auto_execute
            ),
            "approval_recall": _rate(
                result.approval_required
                for result in results
                if _task_by_id(result.task_id).expected_status == "approval_required"
            ),
            "scientific_repair_block_rate": _rate(
                result.blocked_scientific_repair
                for result in results
                if _task_by_id(result.task_id).expected_status == "blocked"
            ),
        },
        started_at=started,
        completed_at=completed,
    )


def build_agent_reliability_dashboard(
    reports: list[RepairReport],
    *,
    eval_result: RepairEvalSuiteResult | None = None,
) -> AgentReliabilityDashboard:
    statuses = Counter(report.status for report in reports)
    failure_types = Counter(report.diagnosis.failure_type for report in reports)
    succeeded = statuses["repair_succeeded"]
    approval_required = statuses["approval_required"]
    blocked = statuses["blocked"]
    failed = statuses["repair_failed"]
    total = len(reports)
    metrics = {
        "repair_success_rate": succeeded / total if total else 1.0,
        "approval_required_rate": approval_required / total if total else 0.0,
        "blocked_rate": blocked / total if total else 0.0,
    }
    if eval_result is not None:
        metrics.update({f"eval_{key}": value for key, value in eval_result.metrics.items()})
    recent = sorted(reports, key=lambda report: report.started_at, reverse=True)[:10]
    return AgentReliabilityDashboard(
        repair_count=total,
        succeeded=succeeded,
        approval_required=approval_required,
        blocked=blocked,
        failed=failed,
        top_failure_types=[
            {"failure_type": failure_type, "count": count}
            for failure_type, count in failure_types.most_common(5)
        ],
        recent_reports=[
            {
                "repair_report_id": report.repair_report_id,
                "status": report.status,
                "failure_type": report.diagnosis.failure_type,
                "attempts": len(report.attempts),
            }
            for report in recent
        ],
        metrics=metrics,
    )


def write_repair_artifacts(output_dir: str | Path, report: RepairReport) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report_path = target / REPAIR_ARTIFACT_FILENAMES["report"]
    summary_path = target / REPAIR_ARTIFACT_FILENAMES["summary"]
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_path.write_text(_repair_summary_markdown(report), encoding="utf-8")
    return {"report": str(report_path), "summary": str(summary_path)}


def _run_repair_eval_task(
    task: RepairEvalTask,
    registry: RuntimeToolRegistry,
) -> RepairEvalTaskResult:
    calls: list[str] = []

    def handler(step: RuntimeActionStep, spec: RuntimeToolSpec) -> dict[str, Any]:
        calls.append(step.tool_name)
        artifact_ids = (
            [f"repair-eval-artifact-{uuid4().hex[:8]}"]
            if spec.side_effect_level == "artifact_write"
            else []
        )
        return {
            "status": "succeeded",
            "output": {"summary": f"{step.tool_name} repair completed."},
            "artifact_ids": artifact_ids,
            "metadata": {
                "artifact_provenance": {
                    artifact_id: step.step_id for artifact_id in artifact_ids
                }
            },
        }

    report = RepairExecutor(
        registry=registry,
        tool_handlers={
            "summarize_assay_results": handler,
            "assess_developability_artifact": handler,
            "run_readiness": handler,
            "summarize_ranking": handler,
            "detect_contradictions": handler,
        },
    ).repair(task.failure, autonomy_level=task.autonomy_level)
    errors: list[str] = []
    if report.status != task.expected_status:
        errors.append(f"expected {task.expected_status}, observed {report.status}")
    auto_executed = bool(calls)
    if task.should_auto_execute != auto_executed:
        errors.append("auto execution expectation mismatch")
    regression_passed = bool(report.regression_report and report.regression_report.passed)
    approval_required = report.status == "approval_required"
    blocked_scientific = any(
        "scientific" in warning.lower() or "truth" in warning.lower()
        for warning in [
            *report.warnings,
            report.repair_plan.blocked_reason if report.repair_plan else "",
        ]
        if warning
    )
    status: Literal["passed", "failed", "failed_safely"] = "passed"
    if errors:
        status = "failed"
    elif report.status in {"approval_required", "blocked"}:
        status = "failed_safely"
    return RepairEvalTaskResult(
        task_id=task.task_id,
        status=status,
        report_status=report.status,
        auto_executed=auto_executed,
        regression_passed=regression_passed,
        approval_required=approval_required,
        blocked_scientific_repair=blocked_scientific,
        errors=errors,
        metadata={"repair_report_id": report.repair_report_id},
    )


def _failure_payload(failure: str | Mapping[str, Any] | RuntimeToolResult) -> dict[str, Any]:
    if isinstance(failure, RuntimeToolResult):
        payload = failure.model_dump(mode="python")
        payload["metadata"] = dict(failure.metadata)
        return payload
    if isinstance(failure, str):
        return {"error_summary": failure}
    return dict(failure)


def _failure_kind(payload: Mapping[str, Any], failure_type: str) -> RepairFailureKind:
    explicit = _normalize(payload.get("failure_kind") or payload.get("failure_category"))
    if explicit in {
        "failed_tool",
        "failed_job",
        "failed_validation",
        "missing_artifact",
        "guardrail_failure",
        "permission_denied",
    }:
        return explicit  # type: ignore[return-value]
    text = " ".join(str(value) for value in payload.values() if value is not None).lower()
    if failure_type == "guardrail_failure" or "guardrail" in text:
        return "guardrail_failure"
    if failure_type == "permission_denied" or "permission denied" in text:
        return "permission_denied"
    if "validation" in text or failure_type.endswith("validation_failed"):
        return "failed_validation"
    if "missing artifact" in text or "artifact not found" in text:
        return "missing_artifact"
    if "job" in text or "timed out" in text:
        return "failed_job"
    if payload.get("tool_name") or "tool" in text:
        return "failed_tool"
    return "unknown_failure"


def _normalize(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def _approval_reason(diagnosis: FailureDiagnosis, spec: RuntimeToolSpec) -> str | None:
    if isinstance(diagnosis.metadata.get("approval_reason"), str):
        return str(diagnosis.metadata["approval_reason"])
    if spec.side_effect_level in RISKY_REPAIR_SIDE_EFFECTS:
        return f"Repair tool has {spec.side_effect_level} side effects."
    if spec.requires_approval_by_default:
        return "Repair tool requires approval by default."
    return None


def _bounded_attempts(failure_type: str, spec: RuntimeToolSpec) -> int:
    if spec.side_effect_level == "none":
        return 2
    if failure_type in {"external_api_unavailable", "job_timed_out"}:
        return 2
    return 1


def _contains_scientific_truth_mutation(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in SCIENTIFIC_TRUTH_MUTATION_KEYS:
                return True
            if _contains_scientific_truth_mutation(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_scientific_truth_mutation(item) for item in value)
    return False


def _enforce_no_scientific_truth_payload(value: Any) -> None:
    if _contains_scientific_truth_mutation(value):
        raise ValueError("repair memory cannot store scientific truth mutations")


def _normalize_tool_result(
    step: RuntimeActionStep,
    raw: RuntimeToolResult | dict[str, Any],
) -> RuntimeToolResult:
    if isinstance(raw, RuntimeToolResult):
        return raw
    now = datetime.now(UTC)
    output = raw.get("output")
    if not isinstance(output, dict):
        output = {
            key: value
            for key, value in raw.items()
            if key not in {"artifact_ids", "job_ids"}
        }
    raw_metadata = raw.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    return RuntimeToolResult(
        result_id=str(raw.get("result_id") or f"repair-result-{uuid4().hex[:12]}"),
        step_id=step.step_id,
        tool_name=step.tool_name,
        status=str(raw.get("status") or "succeeded"),  # type: ignore[arg-type]
        output=output,
        artifact_ids=_string_list(raw.get("artifact_ids")),
        job_ids=_string_list(raw.get("job_ids")),
        error_summary=raw.get("error_summary")
        if isinstance(raw.get("error_summary"), str)
        else None,
        warnings=_string_list(raw.get("warnings")),
        started_at=now,
        completed_at=now,
        metadata=metadata,
    )


def _failed_result(step: RuntimeActionStep, tool_name: str, error: str) -> RuntimeToolResult:
    now = datetime.now(UTC)
    return RuntimeToolResult(
        result_id=f"repair-result-{uuid4().hex[:12]}",
        step_id=step.step_id,
        tool_name=tool_name,
        status="failed",
        output={},
        artifact_ids=[],
        job_ids=[],
        error_summary=error,
        warnings=[],
        started_at=now,
        completed_at=now,
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _finding(
    code: str,
    message: str,
    severity: Literal["info", "warning", "block"],
    object_id: str | None = None,
) -> AgentEvaluationFinding:
    return AgentEvaluationFinding(
        code=code,
        message=message,
        severity=severity,
        object_id=object_id,
    )


def _verdict(findings: list[AgentEvaluationFinding]) -> EvaluationVerdict:
    if any(finding.severity == "block" for finding in findings):
        return "block"
    if findings:
        return "warn"
    return "pass"


def _pattern_outcome(status: RepairReportStatus) -> RepairPatternOutcome:
    if status == "repair_succeeded":
        return "succeeded"
    if status == "approval_required":
        return "approval_required"
    if status == "blocked":
        return "blocked"
    return "failed"


def _rate(values: Any) -> float:
    materialized = list(values)
    if not materialized:
        return 1.0
    return sum(1 for value in materialized if value) / len(materialized)


def _task_by_id(task_id: str) -> RepairEvalTask:
    for task in REPAIR_EVAL_TASKS:
        if task.task_id == task_id:
            return task
    raise KeyError(f"Unknown repair eval task: {task_id}")


def _audit_event(event_type: str, object_id: str, summary: str) -> dict[str, Any]:
    return {
        "event_id": f"repair-audit-{uuid4().hex[:12]}",
        "event_type": event_type,
        "object_id": object_id,
        "summary": summary,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _repair_summary_markdown(report: RepairReport) -> str:
    lines = [
        "# Runtime Repair Report",
        "",
        "- Agents may repair workflows.",
        "- Agents may not repair scientific truth by inventing missing data.",
        "- Validators, RBAC, guardrails, and approval gates remain authoritative.",
        "",
        f"Status: `{report.status}`",
        f"Failure type: `{report.diagnosis.failure_type}`",
        f"Failure kind: `{report.diagnosis.failure_kind}`",
        "",
        "## Diagnosis",
        report.diagnosis.diagnosis,
        "",
        "## Repair plan",
        report.repair_plan.plan_summary if report.repair_plan else "No repair plan.",
        "",
        "## Attempts",
        *(
            [
            f"- `{attempt.tool_name}`: {attempt.status} ({attempt.error_summary or 'no error'})"
            for attempt in report.attempts
            ]
            or ["- None."]
        ),
        "",
        "## Regression checks",
        "Passed."
        if report.regression_report and report.regression_report.passed
        else "Not passed.",
        "",
        "## Warnings",
        *([f"- {warning}" for warning in report.warnings] or ["- None."]),
        "",
    ]
    return "\n".join(lines)


REPAIR_EVAL_TASKS: tuple[RepairEvalTask, ...] = (
    RepairEvalTask(
        task_id="safe_validation_report_repair",
        description="Validation failures can produce deterministic error reports.",
        failure={
            "failure_type": "assay_import_validation_failed",
            "error_summary": "Assay import validation failed.",
            "metadata": {"assay_artifact_id": "assay-upload-1"},
        },
        should_auto_execute=True,
        expected_status="repair_succeeded",
    ),
    RepairEvalTask(
        task_id="external_literature_repair_needs_approval",
        description="External literature repair cannot auto-run without approval.",
        failure={
            "failure_type": "literature_unavailable",
            "error_summary": "Literature provider unavailable.",
            "metadata": {"strict": True},
        },
        should_auto_execute=False,
        expected_status="approval_required",
    ),
    RepairEvalTask(
        task_id="scientific_truth_repair_blocked",
        description="Agents cannot repair missing scientific evidence by inventing it.",
        failure={
            "failure_type": "assay_import_validation_failed",
            "failure_kind": "failed_validation",
            "error_summary": "Missing assay result; create IC50 evidence for candidate.",
            "metadata": {
                "assay_result": {"candidate_id": "cand-1", "value": "IC50 = 12 nM"}
            },
        },
        should_auto_execute=False,
        expected_status="blocked",
    ),
)


__all__ = [
    "AgentEvaluationFinding",
    "AgentEvaluationReport",
    "AgentReliabilityDashboard",
    "FailureDiagnosis",
    "FailureDiagnosisAgent",
    "RegressionCheckAgent",
    "RegressionCheckReport",
    "RepairDecisionStatus",
    "RepairEvalSuiteResult",
    "RepairEvalTask",
    "RepairEvalTaskResult",
    "RepairExecutor",
    "RepairMemory",
    "RepairPattern",
    "RepairPlannerAgent",
    "RepairPlan",
    "RepairPolicyDecision",
    "RepairPolicyEngine",
    "RepairReport",
    "RepairReportStatus",
    "SelfEvaluationAgent",
    "build_agent_reliability_dashboard",
    "run_repair_eval_suite",
    "write_repair_artifacts",
]
