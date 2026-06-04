from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import (
    RegressionCheck,
    RepairAction,
    RepairExecution,
    RepairPlan,
)

if TYPE_CHECKING:
    from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

RepairExecutionMode = Literal[
    "dry_run",
    "suggest_only",
    "execute_safe_repairs",
    "execute_with_approval",
    "supervised_auto",
]
ToolHandler = Callable[..., Mapping[str, Any] | dict[str, Any] | None]
ApprovalChecker = Callable[[RepairAction], bool]
ApprovalRequester = Callable[[RepairAction, str], str]
AuditWriter = Callable[[dict[str, Any]], None]
RegressionRunner = Callable[..., RegressionCheck | list[RegressionCheck] | None]

APPROVAL_SIDE_EFFECTS = {"external_write", "destructive"}
HIGH_RISK_LEVELS = {"high", "critical"}
BLOCKED_TOOL_NAMES = {"import_assay_results", "link_assay_results"}
FORBIDDEN_EDIT_KEYS = {
    "assay_result",
    "assay_results",
    "direct_score_edit",
    "raw_evidence",
    "raw_evidence_edit",
    "raw_source_artifact_edit",
    "scientific_score",
}
INTERNAL_ACTIONS = {
    "adjust_safe_config",
    "revalidate_artifact",
    "request_missing_input",
    "request_human_approval",
    "mark_skipped",
    "quarantine_artifact",
    "rollback_artifact",
    "rollback_job",
    "rebuild_index",
    "clear_derived_cache",
    "retry_external_read",
    "retry_codex_with_schema",
    "run_regression_check",
    "create_issue_report",
}


class RepairExecutor:
    """Execute validated repair plans through registered tools and policy gates."""

    def __init__(
        self,
        *,
        tool_registry: RuntimeToolRegistry | None = None,
        tool_handlers: Mapping[str, ToolHandler] | None = None,
        policy_engine: Any | None = None,
        approval_checker: ApprovalChecker | None = None,
        approval_requester: ApprovalRequester | None = None,
        audit_writer: AuditWriter | None = None,
        regression_runner: RegressionRunner | None = None,
        repair_memory: Any | None = None,
    ) -> None:
        if tool_registry is None:
            from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

            tool_registry = RuntimeToolRegistry.default()
        self.tool_registry = tool_registry
        self.tool_handlers = dict(tool_handlers or {})
        self.policy_engine = policy_engine
        self.approval_checker = approval_checker
        self.approval_requester = approval_requester
        self.audit_writer = audit_writer
        self.regression_runner = regression_runner
        self.repair_memory = repair_memory
        self.audit_events: list[dict[str, Any]] = []

    def execute(
        self,
        repair_plan: RepairPlan | Mapping[str, Any],
        *,
        mode: RepairExecutionMode | str = "suggest_only",
        approvals: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> RepairExecution:
        plan = _parse_plan(repair_plan)
        approval_set = {str(item) for item in approvals or []}
        execution_id = f"repair-execution-{uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        executed_actions: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts_created: list[str] = []
        artifacts_modified: list[str] = []
        jobs_created: list[str] = []
        approvals_requested: list[str] = []

        self._audit(
            "repair_execution_started",
            plan,
            execution_id=execution_id,
            metadata={"mode": mode},
        )

        validation_errors = self._validate_plan(plan)
        if validation_errors:
            warnings.extend(validation_errors)
            execution = self._execution(
                execution_id=execution_id,
                plan=plan,
                status="failed",
                executed_actions=executed_actions,
                artifacts_created=artifacts_created,
                artifacts_modified=artifacts_modified,
                jobs_created=jobs_created,
                approvals_requested=approvals_requested,
                regression_check_ids=[],
                warnings=warnings,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                mode=str(mode),
            )
            self._record_memory(plan, execution)
            self._audit("repair_execution_failed_validation", plan, execution_id=execution_id)
            return execution

        if str(mode) in {"dry_run", "suggest_only"}:
            approvals_requested.extend(
                self._request_approvals(plan, approval_set=approval_set, dry_run=True)
            )
            status = "approval_required" if approvals_requested else "queued"
            execution = self._execution(
                execution_id=execution_id,
                plan=plan,
                status=status,
                executed_actions=[],
                artifacts_created=[],
                artifacts_modified=[],
                jobs_created=[],
                approvals_requested=approvals_requested,
                regression_check_ids=[],
                warnings=warnings,
                started_at=started_at,
                completed_at=None,
                mode=str(mode),
            )
            self._audit("repair_execution_not_executed", plan, execution_id=execution_id)
            return execution

        if _guardrail_blocked(plan, approval_set):
            approvals_requested.extend(self._request_approvals(plan, approval_set=approval_set))
            execution = self._execution(
                execution_id=execution_id,
                plan=plan,
                status="guardrail_blocked",
                executed_actions=[],
                artifacts_created=[],
                artifacts_modified=[],
                jobs_created=[],
                approvals_requested=approvals_requested,
                regression_check_ids=[],
                warnings=["Guardrail failure requires review before repair execution."],
                started_at=started_at,
                completed_at=datetime.now(UTC),
                mode=str(mode),
            )
            self._record_memory(plan, execution)
            self._audit("repair_execution_guardrail_blocked", plan, execution_id=execution_id)
            return execution

        blocked_actions = [
            action
            for action in plan.actions
            if self._requires_approval(action) and not self._is_approved(action, approval_set)
        ]
        if blocked_actions:
            approvals_requested.extend(
                self._request_approvals(
                    plan,
                    approval_set=approval_set,
                    actions=blocked_actions,
                )
            )
            execution = self._execution(
                execution_id=execution_id,
                plan=plan,
                status="approval_required",
                executed_actions=[],
                artifacts_created=[],
                artifacts_modified=[],
                jobs_created=[],
                approvals_requested=approvals_requested,
                regression_check_ids=[],
                warnings=["Repair execution requires approval for one or more actions."],
                started_at=started_at,
                completed_at=datetime.now(UTC),
                mode=str(mode),
            )
            self._record_memory(plan, execution)
            self._audit("repair_execution_approval_required", plan, execution_id=execution_id)
            return execution

        try:
            for action in plan.actions:
                result = self._execute_action(action)
                executed_actions.append(result)
                artifacts_created.extend(_string_list(result.get("artifacts_created")))
                artifacts_modified.extend(_string_list(result.get("artifacts_modified")))
                jobs_created.extend(_string_list(result.get("jobs_created")))
                self._audit(
                    "repair_action_executed",
                    plan,
                    execution_id=execution_id,
                    action=action,
                    metadata={"status": result.get("status", "succeeded")},
                )
                if result.get("status") == "failed":
                    raise RepairExecutionError(str(result.get("error") or "repair action failed"))
        except Exception as exc:
            warnings.append(str(exc))
            rollback_records = self._record_or_run_rollback(
                plan,
                approval_set=approval_set,
                execution_id=execution_id,
            )
            executed_actions.extend(rollback_records)
            execution = self._execution(
                execution_id=execution_id,
                plan=plan,
                status="failed",
                executed_actions=executed_actions,
                artifacts_created=artifacts_created,
                artifacts_modified=artifacts_modified,
                jobs_created=jobs_created,
                approvals_requested=approvals_requested,
                regression_check_ids=[],
                warnings=warnings,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                mode=str(mode),
                extra_metadata={"rollback_plan_recorded": bool(plan.rollback_plan)},
            )
            self._record_memory(plan, execution)
            self._audit("repair_execution_failed", plan, execution_id=execution_id)
            return execution

        regression_checks = self._run_regression_checks(plan, execution_id)
        regression_check_ids = [check.regression_check_id for check in regression_checks]
        if not all(check.passed for check in regression_checks):
            warnings.append("Regression checks failed after repair execution.")
            rollback_records = self._record_or_run_rollback(
                plan,
                approval_set=approval_set,
                execution_id=execution_id,
            )
            executed_actions.extend(rollback_records)
            status = "failed"
        else:
            status = "succeeded"

        execution = self._execution(
            execution_id=execution_id,
            plan=plan,
            status=status,
            executed_actions=executed_actions,
            artifacts_created=artifacts_created,
            artifacts_modified=artifacts_modified,
            jobs_created=jobs_created,
            approvals_requested=approvals_requested,
            regression_check_ids=regression_check_ids,
            warnings=warnings,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            mode=str(mode),
            extra_metadata={"rollback_plan_recorded": bool(plan.rollback_plan)},
        )
        self._record_memory(plan, execution)
        self._audit("repair_execution_completed", plan, execution_id=execution_id)
        return execution

    def _execute_action(self, action: RepairAction) -> dict[str, Any]:
        if action.action_type == "request_human_approval":
            return _action_result(action, status="skipped", approval_required=True)
        if action.tool_name is None:
            if action.action_type not in INTERNAL_ACTIONS:
                raise RepairExecutionError(
                    f"Repair action {action.repair_action_id} has no registered tool."
                )
            return _action_result(action, status="succeeded")

        spec = self.tool_registry.get(action.tool_name)
        if spec is None:
            raise RepairExecutionError(f"Unregistered repair tool: {action.tool_name}")
        handler = self.tool_handlers.get(action.tool_name)
        if handler is None:
            raise RepairExecutionError(f"No repair handler registered for tool: {action.tool_name}")
        result = _call_handler(handler, action, spec)
        return {
            **_action_result(action, status=str(result.get("status", "succeeded"))),
            "tool_output": dict(result),
            "artifacts_created": _string_list(result.get("artifact_ids"))
            + _string_list(result.get("artifacts_created")),
            "artifacts_modified": _string_list(result.get("artifacts_modified")),
            "jobs_created": _string_list(result.get("job_ids"))
            + _string_list(result.get("jobs_created")),
            "error": result.get("error") or result.get("error_summary"),
        }

    def _validate_plan(self, plan: RepairPlan) -> list[str]:
        errors = list(plan.validation_errors)
        if not plan.validated:
            errors.append("Repair plan is not validated.")
        for action in [*plan.actions, *plan.rollback_plan]:
            if action.tool_name is not None:
                if self.tool_registry.get(action.tool_name) is None:
                    errors.append(f"Unregistered repair tool: {action.tool_name}")
                if action.tool_name in BLOCKED_TOOL_NAMES:
                    errors.append(
                        f"Repair tool cannot modify source measurements: {action.tool_name}"
                    )
            if action.side_effect_level in APPROVAL_SIDE_EFFECTS and not action.requires_approval:
                errors.append(f"{action.repair_action_id} has risky side effects without approval.")
            if action.risk_level in HIGH_RISK_LEVELS and not action.requires_approval:
                errors.append(f"{action.repair_action_id} is high risk without approval.")
            if _contains_forbidden_edit(action.tool_args) or _contains_forbidden_edit(
                action.metadata
            ):
                errors.append(f"{action.repair_action_id} attempts forbidden scientific edits.")
            if _would_approve_stage_gate_or_campaign(action):
                errors.append(
                    f"{action.repair_action_id} attempts stage gate or campaign approval."
                )
        return errors

    def _requires_approval(self, action: RepairAction) -> bool:
        if action.requires_approval:
            return True
        if action.side_effect_level in APPROVAL_SIDE_EFFECTS:
            return True
        if action.risk_level in HIGH_RISK_LEVELS:
            return True
        return _policy_requires_approval(self.policy_engine, action)

    def _is_approved(self, action: RepairAction, approval_set: set[str]) -> bool:
        approval_keys = {
            action.repair_action_id,
            action.action_type,
            action.target_object_id,
            action.tool_name or "",
            "all",
        }
        if approval_keys.intersection(approval_set):
            return True
        if self.approval_checker is None:
            return False
        return bool(self.approval_checker(action))

    def _request_approvals(
        self,
        plan: RepairPlan,
        *,
        approval_set: set[str],
        dry_run: bool = False,
        actions: list[RepairAction] | None = None,
    ) -> list[str]:
        approval_ids: list[str] = []
        for action in actions or plan.actions:
            if not self._requires_approval(action) or self._is_approved(action, approval_set):
                continue
            if dry_run:
                approval_ids.append(f"dry-run-approval-{action.repair_action_id}")
                continue
            reason = action.approval_reason or "Repair action requires approval."
            if self.approval_requester is not None:
                approval_ids.append(str(self.approval_requester(action, reason)))
            else:
                approval_ids.append(f"approval-request-{action.repair_action_id}")
        return approval_ids

    def _run_regression_checks(
        self,
        plan: RepairPlan,
        execution_id: str,
    ) -> list[RegressionCheck]:
        if self.regression_runner is None:
            return [
                RegressionCheck(
                    regression_check_id=f"regression-check-{uuid4().hex[:12]}",
                    repair_execution_id=execution_id,
                    check_type="workflow_smoke",
                    passed=True,
                    findings=[],
                    artifacts_checked=list(plan.expected_artifacts),
                    created_at=datetime.now(UTC),
                    metadata={"default_executor_check": True},
                )
            ]
        result = _call_regression_runner(self.regression_runner, plan, execution_id)
        if result is None:
            return []
        if isinstance(result, RegressionCheck):
            return [result]
        return list(result)

    def _record_or_run_rollback(
        self,
        plan: RepairPlan,
        *,
        approval_set: set[str],
        execution_id: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for action in plan.rollback_plan:
            if self._requires_approval(action) and not self._is_approved(action, approval_set):
                records.append(
                    _action_result(
                        action,
                        status="approval_required",
                        rollback_recorded=True,
                    )
                )
                continue
            try:
                result = self._execute_action(action)
            except Exception as exc:
                result = _action_result(action, status="failed", error=str(exc))
            result["rollback_recorded"] = True
            records.append(result)
            self._audit(
                "repair_rollback_recorded",
                plan,
                execution_id=execution_id,
                action=action,
                metadata={"status": result.get("status")},
            )
        return records

    def _execution(
        self,
        *,
        execution_id: str,
        plan: RepairPlan,
        status: str,
        executed_actions: list[dict[str, Any]],
        artifacts_created: list[str],
        artifacts_modified: list[str],
        jobs_created: list[str],
        approvals_requested: list[str],
        regression_check_ids: list[str],
        warnings: list[str],
        started_at: datetime,
        completed_at: datetime | None,
        mode: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> RepairExecution:
        return RepairExecution(
            repair_execution_id=execution_id,
            repair_plan_id=plan.repair_plan_id,
            status=status,  # type: ignore[arg-type]
            executed_actions=executed_actions,
            artifacts_created=list(dict.fromkeys(artifacts_created)),
            artifacts_modified=list(dict.fromkeys(artifacts_modified)),
            jobs_created=list(dict.fromkeys(jobs_created)),
            approvals_requested=list(dict.fromkeys(approvals_requested)),
            regression_check_ids=regression_check_ids,
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
            metadata={
                "execution_mode": mode,
                "audit_events": list(self.audit_events),
                "rollback_plan": [
                    action.model_dump(mode="json") for action in plan.rollback_plan
                ],
                **(extra_metadata or {}),
            },
        )

    def _audit(
        self,
        event_type: str,
        plan: RepairPlan,
        *,
        execution_id: str,
        action: RepairAction | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "event_id": f"repair-audit-{uuid4().hex[:12]}",
            "event_type": event_type,
            "repair_plan_id": plan.repair_plan_id,
            "repair_execution_id": execution_id,
            "repair_action_id": action.repair_action_id if action else None,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": metadata or {},
        }
        self.audit_events.append(event)
        if self.audit_writer is not None:
            self.audit_writer(event)

    def _record_memory(self, plan: RepairPlan, execution: RepairExecution) -> None:
        if self.repair_memory is None:
            return
        payload = {
            "repair_plan_id": plan.repair_plan_id,
            "repair_execution_id": execution.repair_execution_id,
            "status": execution.status,
            "succeeded": execution.status == "succeeded",
            "failure_category": plan.metadata.get("failure_category"),
        }
        for method_name in ("record_execution", "record_repair", "update"):
            method = getattr(self.repair_memory, method_name, None)
            if callable(method):
                method(payload)
                return
        if isinstance(self.repair_memory, list):
            self.repair_memory.append(payload)


class RepairExecutionError(RuntimeError):
    """Raised when a repair action cannot be executed safely."""


def execute_repair(
    repair_plan: RepairPlan | Mapping[str, Any],
    **kwargs: Any,
) -> RepairExecution:
    return RepairExecutor().execute(repair_plan, **kwargs)


def _parse_plan(repair_plan: RepairPlan | Mapping[str, Any]) -> RepairPlan:
    if isinstance(repair_plan, RepairPlan):
        return repair_plan
    return RepairPlan.model_validate(repair_plan)


def _guardrail_blocked(plan: RepairPlan, approval_set: set[str]) -> bool:
    if "guardrail_review" in approval_set or "all" in approval_set:
        return False
    if plan.metadata.get("failure_category") == "guardrail_failed":
        return True
    return any(
        action.action_type == "quarantine_artifact"
        and action.requires_approval
        and action.risk_level in HIGH_RISK_LEVELS
        for action in plan.actions
    )


def _call_handler(handler: ToolHandler, action: RepairAction, spec: Any) -> dict[str, Any]:
    try:
        result = handler(action, spec)
    except TypeError:
        result = handler(action)
    if result is None:
        return {}
    return dict(result)


def _call_regression_runner(
    runner: RegressionRunner,
    plan: RepairPlan,
    execution_id: str,
) -> RegressionCheck | list[RegressionCheck] | None:
    try:
        return runner(plan, execution_id)
    except TypeError:
        try:
            return runner(plan)
        except TypeError:
            return runner()


def _action_result(action: RepairAction, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "repair_action_id": action.repair_action_id,
        "action_type": action.action_type,
        "target_object_type": action.target_object_type,
        "target_object_id": action.target_object_id,
        "tool_name": action.tool_name,
        "status": status,
        **extra,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _contains_forbidden_edit(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_EDIT_KEYS:
                return True
            if _contains_forbidden_edit(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_edit(item) for item in value)
    return False


def _would_approve_stage_gate_or_campaign(action: RepairAction) -> bool:
    payload = " ".join(
        [
            action.action_type,
            action.target_object_type,
            action.target_object_id,
            action.tool_name or "",
            action.expected_effect,
        ]
    ).lower()
    return "approve" in payload and ("stage_gate" in payload or "campaign" in payload)


def _policy_requires_approval(policy_engine: Any | None, action: RepairAction) -> bool:
    if policy_engine is None:
        return False
    method = getattr(policy_engine, "requires_approval", None)
    if callable(method):
        return bool(method(action))
    if isinstance(policy_engine, Mapping):
        policy = policy_engine.get(action.action_type)
        if isinstance(policy, Mapping):
            return bool(policy.get("requires_approval"))
    return False


__all__ = [
    "RepairExecution",
    "RepairExecutionError",
    "RepairExecutionMode",
    "RepairExecutor",
    "execute_repair",
]
