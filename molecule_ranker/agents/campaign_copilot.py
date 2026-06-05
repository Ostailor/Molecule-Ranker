from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.config import CampaignCoPilotAutonomy, CampaignCoPilotStatusUpdateMode
from molecule_ranker.copilot.action_queue import CoPilotActionQueue
from molecule_ranker.copilot.escalation import EscalationManager
from molecule_ranker.copilot.event_detector import EventDetector
from molecule_ranker.copilot.memory import CoPilotMemory
from molecule_ranker.copilot.monitor import CampaignMonitor
from molecule_ranker.copilot.policy import CoPilotPolicyEngine
from molecule_ranker.copilot.reports import CoPilotStatusReporter
from molecule_ranker.copilot.schemas import (
    CampaignCoPilotSession,
    CampaignEvent,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotTrigger,
)
from molecule_ranker.copilot.trigger_router import TriggerRouter

_AUTONOMY_LEVELS = {
    "observe_only",
    "suggest_only",
    "execute_safe_actions",
    "execute_with_approval",
    "supervised_auto",
}
_STATUS_MODES = {
    "manual",
    "daily",
    "weekly",
    "after important trigger",
    "after campaign replan",
}


class CampaignCoPilotAgent(BaseAgent):
    name = "CampaignCoPilotAgent"

    def __init__(
        self,
        *,
        monitor: Any | None = None,
        event_detector: Any | None = None,
        trigger_router: Any | None = None,
        policy_engine: CoPilotPolicyEngine | None = None,
        action_queue: CoPilotActionQueue | None = None,
        executor: Callable[[CoPilotAction], CoPilotActionResult] | None = None,
        escalation_manager: EscalationManager | None = None,
        status_reporter: CoPilotStatusReporter | None = None,
        memory: CoPilotMemory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self.memory = memory or CoPilotMemory(now=now)
        self.monitor = monitor or CampaignMonitor(now=now)
        self.event_detector = event_detector or EventDetector(memory=self.memory, now=now)
        self.trigger_router = trigger_router or TriggerRouter()
        self.policy_engine = policy_engine or CoPilotPolicyEngine()
        self.action_queue = action_queue
        self.executor = executor
        self.escalation_manager = escalation_manager or EscalationManager(now=now)
        self.status_reporter = status_reporter
        self._now = now or (lambda: datetime.now(UTC))
        self._last_cycle: dict[str, Any] = self._empty_cycle(enabled=False)

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_campaign_copilot", False)):
            self._last_cycle = self._empty_cycle(enabled=False)
            context.config["campaign_copilot_last_cycle"] = dict(self._last_cycle)
            return context

        autonomy = self._autonomy(context)
        session = self._start_or_resume_session(context, autonomy=autonomy)
        events = self._detect_events(session)
        guardrail_failures = self._guardrail_failure_count(events)
        triggers = self._route_triggers(events)

        actions: list[CoPilotAction] = []
        if autonomy != "observe_only":
            actions = self._propose_actions(
                triggers,
                autonomy=autonomy,
                require_approval_for_replan=bool(
                    context.config.get("campaign_copilot_require_approval_for_replan", True)
                ),
            )

        queue = self._queue(context)
        approvals_requested = self._queue_actions_and_escalate(actions, triggers, queue=queue)
        results: list[CoPilotActionResult] = []
        if autonomy in {"execute_safe_actions", "execute_with_approval", "supervised_auto"}:
            results = queue.execute_eligible_safe_actions(session=session)

        if self._should_pause_for_guardrail(context, guardrail_failures, results):
            session.status = "paused"
            session.stopped_at = self._now()
            session.metadata["paused_reason"] = "guardrail_failure"

        self._store_memory(triggers=triggers, actions=actions, results=results)
        status_bundle = self._status_report(
            context,
            events=events,
            actions=actions,
            results=results,
        )
        self.memory.mark_seen([event.event_id for event in events])
        session.last_check_at = self._now()
        context.config["campaign_copilot_session"] = session
        context.config["campaign_copilot_artifacts"] = status_bundle.artifacts
        self._last_cycle = {
            "enabled": True,
            "session_id": session.copilot_session_id,
            "session_status": session.status,
            "autonomy": autonomy,
            "events_detected": len(events),
            "triggers_routed": len(triggers),
            "actions_proposed": len(actions),
            "actions_executed": len([result for result in results if result.status == "succeeded"]),
            "approvals_requested": approvals_requested,
            "guardrail_failures": guardrail_failures,
            "status_update_id": status_bundle.update.status_update_id,
            "artifact_names": list(status_bundle.artifacts),
        }
        context.config["campaign_copilot_last_cycle"] = dict(self._last_cycle)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not self._last_cycle.get("enabled", False):
            return "Campaign co-pilot disabled; no monitoring actions taken."
        return (
            "Campaign co-pilot cycle completed: "
            f"{self._last_cycle['events_detected']} events, "
            f"{self._last_cycle['actions_executed']} actions executed, "
            f"{self._last_cycle['approvals_requested']} approvals requested."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return dict(self._last_cycle)

    def _detect_events(self, session: CampaignCoPilotSession) -> list[CampaignEvent]:
        polled = self.monitor.poll_active_campaigns(since=session.last_check_at)
        if hasattr(self.event_detector, "detect"):
            return list(self.event_detector.detect(polled))
        if hasattr(self.event_detector, "detect_new_events"):
            return list(self.event_detector.detect_new_events(polled))
        return list(polled)

    def _route_triggers(self, events: list[CampaignEvent]) -> list[CoPilotTrigger]:
        return list(self.trigger_router.route(events))

    def _propose_actions(
        self,
        triggers: list[CoPilotTrigger],
        *,
        autonomy: CampaignCoPilotAutonomy,
        require_approval_for_replan: bool,
    ) -> list[CoPilotAction]:
        actions: list[CoPilotAction] = []
        for trigger in triggers:
            proposed = self.policy_engine.propose_actions(trigger, autonomy_level=autonomy)
            actions.extend(
                self._apply_replan_approval_requirement(
                    action,
                    require_approval_for_replan=require_approval_for_replan,
                )
                for action in proposed
            )
        return actions

    def _queue_actions_and_escalate(
        self,
        actions: list[CoPilotAction],
        triggers: list[CoPilotTrigger],
        *,
        queue: CoPilotActionQueue,
    ) -> int:
        trigger_by_id = {trigger.trigger_id: trigger for trigger in triggers}
        approvals_requested = 0
        for action in actions:
            if action.status == "skipped":
                continue
            queue.queue_action(action)
            if action.requires_approval:
                approvals_requested += 1
                trigger = trigger_by_id.get(action.trigger_id)
                if trigger is not None:
                    self.escalation_manager.from_trigger(trigger, action=action)
        return approvals_requested

    def _store_memory(
        self,
        *,
        triggers: list[CoPilotTrigger],
        actions: list[CoPilotAction],
        results: list[CoPilotActionResult],
    ) -> None:
        trigger_by_id = {trigger.trigger_id: trigger for trigger in triggers}
        action_by_id = {action.copilot_action_id: action for action in actions}
        for result in results:
            action = action_by_id.get(result.copilot_action_id)
            if action is None:
                continue
            trigger = trigger_by_id.get(action.trigger_id)
            if trigger is None:
                continue
            self.memory.record_action_outcome(trigger=trigger, action=action, result=result)

    def _status_report(
        self,
        context: PipelineContext,
        *,
        events: list[CampaignEvent],
        actions: list[CoPilotAction],
        results: list[CoPilotActionResult],
    ) -> Any:
        reporter = self.status_reporter or CoPilotStatusReporter(
            artifact_dir=self._artifact_dir(context),
            now=self._now,
        )
        return reporter.build_status_update(
            campaign_id=self._campaign_id(context),
            period_start=self._now(),
            period_end=self._now(),
            cadence=self._status_mode(context),
            events=events,
            actions=actions,
            action_results=results,
        )

    def _queue(self, context: PipelineContext) -> CoPilotActionQueue:
        if self.action_queue is None:
            self.action_queue = CoPilotActionQueue(
                executor=self.executor,
                max_actions_per_run=self._max_actions(context),
                now=self._now,
            )
        self.action_queue.max_actions_per_run = self._max_actions(context)
        return self.action_queue

    def _start_or_resume_session(
        self,
        context: PipelineContext,
        *,
        autonomy: CampaignCoPilotAutonomy,
    ) -> CampaignCoPilotSession:
        existing = context.config.get("campaign_copilot_session")
        if isinstance(existing, CampaignCoPilotSession):
            if existing.status in {"paused", "awaiting_approval"}:
                existing.status = "active"
            existing.autonomy_level = autonomy
            return existing
        now = self._now()
        session = CampaignCoPilotSession(
            copilot_session_id=f"copilot-session-{self._campaign_id(context)}",
            campaign_id=self._campaign_id(context),
            project_id=self._optional_str(context.config.get("project_id")),
            program_id=self._optional_str(context.config.get("program_id")),
            status="active",
            autonomy_level=autonomy,
            started_at=now,
            stopped_at=None,
            last_check_at=None,
            metadata={
                "check_interval_seconds": int(
                    context.config.get("campaign_copilot_check_interval_seconds", 3600)
                )
            },
        )
        context.config["campaign_copilot_session"] = session
        return session

    def _apply_replan_approval_requirement(
        self,
        action: CoPilotAction,
        *,
        require_approval_for_replan: bool,
    ) -> CoPilotAction:
        if not require_approval_for_replan or action.action_type != "run_campaign_replan":
            return action
        return action.model_copy(
            update={
                "requires_approval": True,
                "approval_reason": "Human approval required before campaign replan.",
                "status": "proposed",
                "metadata": {
                    **action.metadata,
                    "changes_active_plan": True,
                    "approval_enforced_by_campaign_copilot": True,
                },
            },
            deep=True,
        )

    def _should_pause_for_guardrail(
        self,
        context: PipelineContext,
        guardrail_failures: int,
        results: list[CoPilotActionResult],
    ) -> bool:
        if not bool(context.config.get("campaign_copilot_pause_on_guardrail_failure", True)):
            return False
        return guardrail_failures > 0 or any(
            result.status == "guardrail_failed" for result in results
        )

    def _guardrail_failure_count(self, events: list[CampaignEvent]) -> int:
        return len(
            [
                event
                for event in events
                if event.event_type == "guardrail_failure"
                or event.metadata.get("detector_event_type") == "guardrail_failure"
            ]
        )

    def _artifact_dir(self, context: PipelineContext) -> Path:
        if context.output_dir is not None:
            return context.output_dir / "copilot"
        return Path(str(context.config.get("results_dir", "results"))) / "copilot"

    def _autonomy(self, context: PipelineContext) -> CampaignCoPilotAutonomy:
        value = str(context.config.get("campaign_copilot_autonomy", "observe_only"))
        if value not in _AUTONOMY_LEVELS:
            value = "observe_only"
        return cast(CampaignCoPilotAutonomy, value)

    def _status_mode(self, context: PipelineContext) -> CampaignCoPilotStatusUpdateMode:
        value = str(context.config.get("campaign_copilot_status_update_mode", "manual"))
        if value not in _STATUS_MODES:
            value = "manual"
        return cast(CampaignCoPilotStatusUpdateMode, value)

    def _max_actions(self, context: PipelineContext) -> int:
        value = context.config.get("campaign_copilot_max_actions_per_cycle", 10)
        if isinstance(value, int) and value >= 0:
            return value
        return 10

    def _campaign_id(self, context: PipelineContext) -> str:
        value = context.config.get("campaign_id")
        if isinstance(value, str) and value:
            return value
        return "campaign-default"

    def _optional_str(self, value: Any) -> str | None:
        return value if isinstance(value, str) else None

    def _empty_cycle(self, *, enabled: bool) -> dict[str, Any]:
        return {
            "enabled": enabled,
            "session_id": None,
            "session_status": None,
            "autonomy": None,
            "events_detected": 0,
            "triggers_routed": 0,
            "actions_proposed": 0,
            "actions_executed": 0,
            "approvals_requested": 0,
            "guardrail_failures": 0,
            "status_update_id": None,
            "artifact_names": [],
        }
