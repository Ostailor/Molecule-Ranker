from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

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
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._executor = executor or self._default_executor
        self.authorized_service_accounts = authorized_service_accounts or set()
        self.max_actions_per_run = max_actions_per_run
        self.max_retries = max_retries
        self.repeated_failure_limit = repeated_failure_limit
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
            result = self._execute_with_retry(action)
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


AutonomousActionQueue = CoPilotActionQueue
