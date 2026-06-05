from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agent_governance.budgets import (
    AgentAutonomyBudgetManager,
    AgentBudgetReservation,
    BudgetImpact,
)
from molecule_ranker.agent_governance.integration import governance_autonomy_level
from molecule_ranker.agent_governance.run_control import (
    AgentRunControlManager,
    RunControlRequest,
)
from molecule_ranker.agent_governance.schemas import AgentAutonomyBudget
from molecule_ranker.copilot.schemas import (
    ActionResultStatus,
    ActionStatus,
    CampaignCoPilotSession,
    CoPilotAction,
    CoPilotActionResult,
)

ActionExecutor = Callable[[CoPilotAction], CoPilotActionResult]


class CoPilotActionQueue:
    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        executor: ActionExecutor | None = None,
        authorized_service_accounts: set[str] | None = None,
        max_actions_per_run: int = 10,
        max_retries: int = 2,
        repeated_failure_limit: int = 3,
        autonomy_budget_manager: AgentAutonomyBudgetManager | None = None,
        autonomy_budget_id: str | None = None,
        run_control_manager: AgentRunControlManager | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._executor = executor or self._default_executor
        self.authorized_service_accounts = authorized_service_accounts or set()
        self.max_actions_per_run = max_actions_per_run
        self.max_retries = max_retries
        self.repeated_failure_limit = repeated_failure_limit
        self.autonomy_budget_manager = autonomy_budget_manager
        self.autonomy_budget_id = autonomy_budget_id
        self.run_control_manager = run_control_manager
        self.actions: dict[str, CoPilotAction] = {}
        self.results: dict[str, CoPilotActionResult] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.notifications: list[dict[str, str]] = []
        self._dedupe_index: dict[str, str] = {}
        self._failure_counts: dict[str, int] = {}

    def enqueue(self, actions: list[CoPilotAction]) -> CoPilotActionQueue:
        for action in actions:
            self.queue_action(action)
        return self

    def queue_action(self, action: CoPilotAction) -> CoPilotAction:
        dedupe_key = self._dedupe_key(action)
        existing_action_id = self._dedupe_index.get(dedupe_key)
        if existing_action_id is not None:
            existing = self.actions[existing_action_id]
            self._audit(existing, "deduplicated", actor_id="copilot")
            return existing
        queued = self._with_status(action, "queued")
        self.actions[queued.copilot_action_id] = queued
        self._dedupe_index[dedupe_key] = queued.copilot_action_id
        self._audit(queued, "queued", actor_id="copilot")
        return queued

    def get_action(self, action_id: str) -> CoPilotAction:
        return self.actions[action_id]

    def approve_action(
        self,
        action_id: str,
        *,
        approver_id: str,
        approver_type: str = "human",
    ) -> CoPilotAction | CoPilotActionResult:
        action = self.actions[action_id]
        if not self._approval_actor_allowed(approver_id, approver_type):
            result = self._result(
                action,
                status="approval_required",
                summary="Approval must come from a human or authorized service account.",
            )
            self.results[action_id] = result
            self._audit(action, "approval_denied", actor_id=approver_id)
            return result
        approved = self._with_status(action, "approved")
        approved.metadata["approved_by"] = approver_id
        approved.metadata["approver_type"] = approver_type
        self.actions[action_id] = approved
        self._audit(approved, "approved", actor_id=approver_id)
        return approved

    def reject_action(
        self,
        action_id: str,
        *,
        approver_id: str,
        approver_type: str = "human",
        reason: str,
    ) -> CoPilotAction:
        action = self.actions[action_id]
        if not self._approval_actor_allowed(approver_id, approver_type):
            self._audit(action, "rejection_denied", actor_id=approver_id)
            return action
        rejected = self._with_status(action, "rejected")
        rejected.metadata["rejected_by"] = approver_id
        rejected.metadata["rejection_reason"] = reason
        self.actions[action_id] = rejected
        self.results[action_id] = self._result(
            rejected,
            status="skipped",
            summary=f"Action rejected: {reason}",
        )
        self._audit(rejected, "rejected", actor_id=approver_id)
        return rejected

    def execute_eligible_safe_actions(
        self,
        *,
        session: CampaignCoPilotSession | None = None,
    ) -> list[CoPilotActionResult]:
        results: list[CoPilotActionResult] = []
        executed_count = 0
        for action in list(self.actions.values()):
            if executed_count >= self.max_actions_per_run:
                break
            if not self._eligible_for_execution(action):
                continue
            governance_result, reservation = self._governance_preflight(
                action,
                session=session,
            )
            if governance_result is not None:
                results.append(governance_result)
                self.results[action.copilot_action_id] = governance_result
                failed = self._with_status(action, "failed")
                self.actions[action.copilot_action_id] = failed
                self._audit(failed, str(governance_result.status), actor_id="governance")
                executed_count += 1
                continue
            result = self._execute_with_retry(action)
            if reservation is not None:
                if result.status == "succeeded":
                    self.autonomy_budget_manager.commit_budget(reservation.reservation_id)  # type: ignore[union-attr]
                else:
                    self.autonomy_budget_manager.release_budget(  # type: ignore[union-attr]
                        reservation.reservation_id,
                        reason=f"Co-pilot action ended with {result.status}.",
                    )
            results.append(result)
            self.results[action.copilot_action_id] = result
            final_status: ActionStatus = "succeeded" if result.status == "succeeded" else "failed"
            persisted = self._with_status(
                self.actions[action.copilot_action_id],
                final_status,
                completed=result.status == "succeeded",
            )
            self.actions[action.copilot_action_id] = persisted
            self._audit(persisted, final_status, actor_id="copilot")
            if result.status == "failed":
                self._record_failure(
                    persisted,
                    session=session,
                    failure_increment=self._failure_increment(result),
                )
            executed_count += 1
        return results

    def process(self) -> list[CoPilotActionResult]:
        return self.execute_eligible_safe_actions()

    def _execute_with_retry(self, action: CoPilotAction) -> CoPilotActionResult:
        attempts = 0
        last_result: CoPilotActionResult | None = None
        while attempts < self.max_retries:
            attempts += 1
            running = self._with_status(self.actions[action.copilot_action_id], "running")
            self.actions[action.copilot_action_id] = running
            self._audit(running, "running", actor_id="copilot")
            result = self._executor(running)
            result.metadata["attempt"] = attempts
            last_result = result
            if result.status != "failed" or not result.metadata.get("transient"):
                return result
            self._audit(running, "retry_scheduled", actor_id="copilot")
        if last_result is None:
            return self._result(action, status="failed", summary="Action did not execute.")
        return last_result

    def _eligible_for_execution(self, action: CoPilotAction) -> bool:
        if action.status not in {"queued", "approved"}:
            return False
        if action.requires_approval and action.status != "approved":
            return False
        if action.side_effect_level in {"external_write", "destructive"}:
            return False
        return action.risk_level in {"low", "medium"}

    def _governance_preflight(
        self,
        action: CoPilotAction,
        *,
        session: CampaignCoPilotSession | None,
    ) -> tuple[CoPilotActionResult | None, AgentBudgetReservation | None]:
        if self.run_control_manager is not None:
            decision = self.run_control_manager.evaluate(
                RunControlRequest(
                    agent_id=_copilot_agent_id(action),
                    agent_type="campaign_copilot",
                    campaign_id=action.campaign_id,
                    action=action.action_type,
                    autonomy_level=governance_autonomy_level(
                        session.autonomy_level
                        if session is not None
                        else action.metadata.get("autonomy_level")
                    ),
                    side_effect_level=action.side_effect_level,
                ),
                now=self._now(),
            )
            if not decision.allowed:
                status: ActionResultStatus = (
                    "approval_required" if decision.requires_approval else "blocked_by_policy"
                )
                return (
                    self._result(
                        action,
                        status=status,
                        summary="; ".join(decision.reasons),
                    ),
                    None,
                )
        if self.autonomy_budget_manager is None:
            return None, None
        budget = self._budget_for_action(action)
        if budget is None:
            return None, None
        reservation_result = self.autonomy_budget_manager.reserve_budget(
            budget,
            _budget_impact_for_action(action),
            approvals=_approval_set(action),
            now=self._now(),
            metadata={"copilot_action_id": action.copilot_action_id},
        )
        if reservation_result.decision.allowed:
            return None, reservation_result.reservation
        status = (
            "approval_required"
            if reservation_result.decision.requires_approval
            else "blocked_by_policy"
        )
        return (
            self._result(
                action,
                status=status,
                summary="; ".join(reservation_result.decision.reasons),
            ),
            None,
        )

    def _budget_for_action(self, action: CoPilotAction) -> AgentAutonomyBudget | str | None:
        explicit = action.metadata.get("budget_id") or self.autonomy_budget_id
        if isinstance(explicit, str) and explicit:
            return explicit
        if self.autonomy_budget_manager is None:
            return None
        agent_id = _copilot_agent_id(action)
        for budget in self.autonomy_budget_manager.budgets:
            if (
                budget.campaign_id in {None, action.campaign_id}
                and budget.agent_id in {None, agent_id}
            ):
                return budget
        return None

    def _record_failure(
        self,
        action: CoPilotAction,
        *,
        session: CampaignCoPilotSession | None,
        failure_increment: int,
    ) -> None:
        count = self._failure_counts.get(action.copilot_action_id, 0) + failure_increment
        self._failure_counts[action.copilot_action_id] = count
        if count < self.repeated_failure_limit:
            return
        if session is not None:
            session.status = "paused"
            session.stopped_at = self._now()
            session.metadata["paused_reason"] = "repeated_action_failure"
            self._audit(action, "session_paused", actor_id="copilot")
        priority = str(action.metadata.get("priority", ""))
        assigned_role = action.metadata.get("assigned_role")
        if priority in {"high", "critical"} and isinstance(assigned_role, str):
            self.notifications.append(
                {
                    "assigned_role": assigned_role,
                    "action_id": action.copilot_action_id,
                    "reason": "high-priority action failed repeatedly",
                }
            )
            self._audit(action, "assigned_role_notified", actor_id="copilot")

    def _failure_increment(self, result: CoPilotActionResult) -> int:
        attempt = result.metadata.get("attempt", 1)
        return attempt if isinstance(attempt, int) and attempt > 0 else 1

    def _default_executor(self, action: CoPilotAction) -> CoPilotActionResult:
        return self._result(
            action,
            status="succeeded",
            summary="Safe planning action executed.",
        )

    def _result(
        self,
        action: CoPilotAction,
        *,
        status: ActionResultStatus,
        summary: str,
    ) -> CoPilotActionResult:
        return CoPilotActionResult(
            result_id=f"result-{action.copilot_action_id}-{len(self.results) + 1}",
            copilot_action_id=action.copilot_action_id,
            status=status,
            artifact_ids=[],
            job_ids=[],
            summary=summary,
            warnings=[],
            created_at=self._now(),
            metadata={},
        )

    def _with_status(
        self,
        action: CoPilotAction,
        status: ActionStatus,
        *,
        completed: bool = False,
    ) -> CoPilotAction:
        return action.model_copy(
            update={
                "status": status,
                "completed_at": self._now() if completed else action.completed_at,
                "metadata": dict(action.metadata),
            },
            deep=True,
        )

    def _approval_actor_allowed(self, approver_id: str, approver_type: str) -> bool:
        if approver_id.lower() in {"codex", "copilot"}:
            return False
        if approver_type == "human":
            return True
        return (
            approver_type == "service_account"
            and approver_id in self.authorized_service_accounts
        )

    def _dedupe_key(self, action: CoPilotAction) -> str:
        explicit = action.metadata.get("dedupe_key")
        if explicit is not None:
            return str(explicit)
        return f"{action.campaign_id}:{action.trigger_id}:{action.action_type}:{action.tool_args}"

    def _audit(self, action: CoPilotAction, transition: str, *, actor_id: str) -> None:
        self.audit_log.append(
            {
                "action_id": action.copilot_action_id,
                "campaign_id": action.campaign_id,
                "transition": transition,
                "actor_id": actor_id,
                "status": action.status,
                "created_at": self._now(),
            }
        )


def _copilot_agent_id(action: CoPilotAction) -> str:
    value = action.metadata.get("agent_id")
    if isinstance(value, str) and value:
        return value
    return f"campaign-copilot:{action.campaign_id}"


def _approval_set(action: CoPilotAction) -> set[str]:
    approvals = set()
    if action.status == "approved":
        approvals.add("budget_override")
        approvals.add("high_cost_job")
    raw = action.metadata.get("governance_approvals")
    if isinstance(raw, list):
        approvals.update(str(item) for item in raw if isinstance(item, str))
    return approvals


def _budget_impact_for_action(action: CoPilotAction) -> BudgetImpact:
    raw = action.metadata.get("budget_impact")
    if isinstance(raw, dict):
        return BudgetImpact.model_validate(raw)
    impact: dict[str, Any] = {"action_type": action.action_type}
    if action.tool_name:
        impact["tool_calls"] = 1
    if action.side_effect_level == "artifact_write":
        impact["artifact_writes"] = 1
    elif action.side_effect_level == "db_write":
        impact["db_writes"] = 1
    elif action.side_effect_level == "external_read":
        impact["external_reads"] = 1
    elif action.side_effect_level == "external_write":
        impact["external_writes"] = 1
    elif action.side_effect_level == "destructive":
        impact["destructive"] = True
    if action.action_type == "run_campaign_replan":
        impact["campaign_replans"] = 1
    return BudgetImpact.model_validate(impact)


AutonomousActionQueue = CoPilotActionQueue
