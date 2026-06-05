from __future__ import annotations

from dataclasses import dataclass

from molecule_ranker.copilot.schemas import (
    CampaignEvent,
    CoPilotTrigger,
    Priority,
    TriggerType,
)


@dataclass(frozen=True)
class _RouteDecision:
    trigger_type: TriggerType
    priority: Priority
    recommended_action_types: list[str]
    requires_human_attention: bool
    rationale: str


_ACTION_MAP: dict[TriggerType, list[str]] = {
    "replan_needed": ["create_replan_draft", "create_review_request"],
    "approval_needed": ["create_review_request", "create_followup_request"],
    "blocker_detected": ["notify_user", "create_support_bundle"],
    "result_followup_needed": ["create_followup_request"],
    "contradiction_resolution_needed": ["run_contradiction_scan", "create_review_request"],
    "safety_review_needed": ["create_review_request"],
    "budget_review_needed": ["create_review_request"],
    "campaign_update_needed": ["summarize_status", "update_campaign_status"],
    "evaluation_needed": ["run_evaluation_update"],
    "repair_needed": ["run_repair_workflow"],
    "no_action": [],
}

_PRIORITY_RANK: dict[Priority, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


class TriggerRouter:
    def route(self, events: list[CampaignEvent]) -> list[CoPilotTrigger]:
        windows: dict[str, tuple[_RouteDecision, list[CampaignEvent]]] = {}
        for event in events:
            decision = self._decision(event)
            signature = self._trigger_signature(event, decision.trigger_type)
            if signature not in windows:
                windows[signature] = (decision, [event])
                continue
            existing_decision, existing_events = windows[signature]
            windows[signature] = (
                self._merge_decisions(existing_decision, decision),
                [*existing_events, event],
            )
        return [
            self._build_trigger(signature, decision, grouped_events)
            for signature, (decision, grouped_events) in windows.items()
        ]

    def _decision(self, event: CampaignEvent) -> _RouteDecision:
        detector_type = str(event.metadata.get("detector_event_type", event.event_type))
        if detector_type == "positive_qc_passed_exact_assay_result":
            return self._route(
                "result_followup_needed",
                "medium",
                False,
                "QC-passed exact positive assay result needs source-grounded follow-up.",
            )
        if detector_type == "negative_qc_passed_exact_assay_result":
            return self._route(
                "replan_needed",
                "medium",
                True,
                "QC-passed exact negative assay result needs hypothesis review and replanning.",
            )
        if detector_type == "failed_qc_result":
            return self._route(
                "approval_needed",
                "high",
                True,
                "Failed QC requires human review or more data, not a scientific conclusion.",
            )
        if detector_type == "safety_developability_concern":
            return self._route(
                "safety_review_needed",
                "critical" if event.severity == "critical" else "high",
                True,
                "Safety or developability concern requires review.",
            )
        if detector_type == "graph_contradiction":
            return self._route(
                "contradiction_resolution_needed",
                "high",
                True,
                "Graph contradiction requires source-grounded resolution.",
            )
        if detector_type == "stale_decision":
            return self._route(
                "replan_needed",
                "medium",
                True,
                "Stale decision requires campaign plan review.",
            )
        if detector_type == "failed_job":
            return self._route("repair_needed", "high", True, "Failed job requires repair.")
        if detector_type == "repaired_job":
            return self._route(
                "campaign_update_needed",
                "medium",
                False,
                "Repaired job should be reflected in campaign status.",
            )
        if detector_type == "portfolio_changed_after_campaign_plan":
            return self._route(
                "campaign_update_needed",
                "medium",
                False,
                "Portfolio changed after campaign plan.",
            )
        if detector_type == "budget_threshold_exceeded":
            return self._route(
                "budget_review_needed",
                "critical" if event.severity == "critical" else "high",
                True,
                "Budget threshold was exceeded.",
            )
        if detector_type == "guardrail_failure":
            return self._route(
                "blocker_detected",
                "critical",
                True,
                "Guardrail failure blocks autonomous follow-up.",
            )
        if event.event_type == "scheduled_check" or detector_type == "scheduled_check":
            return self._route("no_action", "low", False, "No significant event detected.")
        return self._fallback_decision(event)

    def _fallback_decision(self, event: CampaignEvent) -> _RouteDecision:
        if event.event_type == "graph_contradiction_detected":
            return self._route(
                "contradiction_resolution_needed",
                "high",
                True,
                "Graph contradiction requires source-grounded resolution.",
            )
        if event.event_type == "stale_decision_detected":
            return self._route("replan_needed", "medium", True, "Stale decision detected.")
        if event.event_type == "job_failed":
            return self._route("repair_needed", "high", True, "Failed job requires repair.")
        if event.event_type == "job_repaired":
            return self._route(
                "campaign_update_needed",
                "medium",
                False,
                "Repaired job should update campaign status.",
            )
        if event.event_type == "portfolio_updated":
            return self._route(
                "campaign_update_needed",
                "medium",
                False,
                "Portfolio update should be reflected in campaign status.",
            )
        if event.event_type == "budget_changed":
            return self._route("budget_review_needed", "medium", True, "Budget changed.")
        if event.event_type == "guardrail_failure":
            return self._route(
                "blocker_detected",
                "critical",
                True,
                "Guardrail failure blocks autonomous follow-up.",
            )
        if event.event_type == "developability_risk_changed":
            return self._route(
                "safety_review_needed",
                "critical" if event.severity == "critical" else "high",
                True,
                "Safety or developability concern requires review.",
            )
        if event.event_type == "assay_result_imported":
            return self._route(
                "result_followup_needed",
                "medium",
                False,
                "Assay result import needs follow-up planning.",
            )
        if event.event_type == "integration_sync_completed":
            return self._route(
                "campaign_update_needed",
                "low",
                False,
                "Integration sync completed.",
            )
        if event.event_type == "evaluation_report_created":
            return self._route("evaluation_needed", "medium", False, "Evaluation report created.")
        return self._route("campaign_update_needed", "low", False, event.summary)

    def _route(
        self,
        trigger_type: TriggerType,
        priority: Priority,
        requires_human_attention: bool,
        rationale: str,
    ) -> _RouteDecision:
        return _RouteDecision(
            trigger_type=trigger_type,
            priority=priority,
            recommended_action_types=_ACTION_MAP[trigger_type],
            requires_human_attention=requires_human_attention,
            rationale=rationale,
        )

    def _trigger_signature(self, event: CampaignEvent, trigger_type: TriggerType) -> str:
        detector_type = event.metadata.get("detector_event_type", event.event_type)
        return f"{event.campaign_id}:{trigger_type}:{detector_type}:{event.source_object_type}"

    def _merge_decisions(
        self,
        left: _RouteDecision,
        right: _RouteDecision,
    ) -> _RouteDecision:
        priority = (
            left.priority
            if _PRIORITY_RANK[left.priority] >= _PRIORITY_RANK[right.priority]
            else right.priority
        )
        return _RouteDecision(
            trigger_type=left.trigger_type,
            priority=priority,
            recommended_action_types=left.recommended_action_types,
            requires_human_attention=(
                left.requires_human_attention or right.requires_human_attention
            ),
            rationale=left.rationale,
        )

    def _build_trigger(
        self,
        signature: str,
        decision: _RouteDecision,
        events: list[CampaignEvent],
    ) -> CoPilotTrigger:
        first = events[0]
        return CoPilotTrigger(
            trigger_id=f"trigger-{signature}",
            campaign_id=first.campaign_id,
            event_ids=[event.event_id for event in events],
            trigger_signature=signature,
            trigger_type=decision.trigger_type,
            priority=decision.priority,
            rationale=decision.rationale,
            recommended_action_types=decision.recommended_action_types,
            requires_human_attention=decision.requires_human_attention,
            metadata={
                "trigger_signature": signature,
                "detector_event_type": first.metadata.get(
                    "detector_event_type", first.event_type
                ),
                "source_object_type": first.source_object_type,
                "severity": self._highest_event_severity(events),
                "deduplicated_event_count": len(events),
            },
        )

    def _highest_event_severity(self, events: list[CampaignEvent]) -> str:
        severity_rank: dict[str, int] = {
            "info": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }
        return max(events, key=lambda event: severity_rank[event.severity]).severity
