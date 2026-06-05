from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from molecule_ranker.copilot.event_detector import EventDetector
from molecule_ranker.copilot.memory import CoPilotMemory
from molecule_ranker.copilot.policy import CoPilotPolicyEngine
from molecule_ranker.copilot.replanning import CampaignReplanDraftWorkflow
from molecule_ranker.copilot.reports import CoPilotStatusReporter
from molecule_ranker.copilot.schemas import (
    CampaignEvent,
    CampaignEventType,
    CoPilotMemoryRecord,
    Severity,
    TriggerType,
)
from molecule_ranker.copilot.trigger_router import TriggerRouter

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CoPilotEvalCase:
    name: str
    campaign_state: dict[str, Any]
    expected_detector_types: set[str]
    expected_trigger_types: set[TriggerType]
    seed_events: list[CampaignEvent] = field(default_factory=list)
    red_team: bool = False
    expected_approval: bool = False
    expected_escalation: bool = False
    policy_action_type: str | None = None
    policy_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CoPilotEvalCaseResult:
    name: str
    detected: bool
    routed: bool
    safe_action_precision: float
    unsafe_auto_actions: int
    approval_required: bool
    replan_quality: float
    escalation_recalled: bool
    status_summary_grounded: bool
    guardrail_passed: bool
    failed_qc_no_false_conclusion: bool


@dataclass(frozen=True)
class CoPilotEvalReport:
    suite: str
    metrics: dict[str, float]
    case_results: list[CoPilotEvalCaseResult]

    def case_by_name(self, name: str) -> CoPilotEvalCaseResult:
        for result in self.case_results:
            if result.name == name:
                return result
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "metrics": self.metrics,
            "case_results": [
                {
                    "name": result.name,
                    "detected": result.detected,
                    "routed": result.routed,
                    "safe_action_precision": result.safe_action_precision,
                    "unsafe_auto_actions": result.unsafe_auto_actions,
                    "approval_required": result.approval_required,
                    "replan_quality": result.replan_quality,
                    "escalation_recalled": result.escalation_recalled,
                    "status_summary_grounded": result.status_summary_grounded,
                    "guardrail_passed": result.guardrail_passed,
                    "failed_qc_no_false_conclusion": result.failed_qc_no_false_conclusion,
                }
                for result in self.case_results
            ],
        }


class CoPilotEvalSuite:
    def __init__(
        self,
        *,
        detector: EventDetector | None = None,
        router: TriggerRouter | None = None,
        policy_engine: CoPilotPolicyEngine | None = None,
    ) -> None:
        self.detector = detector or EventDetector(memory=CoPilotMemory(), now=lambda: NOW)
        self.router = router or TriggerRouter()
        self.policy_engine = policy_engine or CoPilotPolicyEngine()

    def improvement_rate(self, records: list[CoPilotMemoryRecord]) -> float:
        if not records:
            return 0.0
        return sum(record.success_rate for record in records) / len(records)

    def run(self, *, suite: str = "default") -> CoPilotEvalReport:
        if suite != "default":
            raise ValueError(f"Unknown co-pilot eval suite: {suite}")
        case_results = [self._run_case(case) for case in self._default_cases()]
        return CoPilotEvalReport(
            suite=suite,
            metrics=self._metrics(case_results),
            case_results=case_results,
        )

    def _run_case(self, case: CoPilotEvalCase) -> CoPilotEvalCaseResult:
        detected_events = [
            *self.detector.detect_campaign_events(case.campaign_state),
            *case.seed_events,
        ]
        detector_types = {
            str(event.metadata.get("detector_event_type", event.event_type))
            for event in detected_events
        }
        triggers = self.router.route(detected_events)
        trigger_types: set[TriggerType] = {trigger.trigger_type for trigger in triggers}
        actions = [
            action
            for trigger in triggers
            for action in self.policy_engine.propose_actions(
                trigger,
                autonomy_level="execute_safe_actions",
            )
        ]
        policy_approval = False
        if case.policy_action_type is not None:
            decision = self.policy_engine.evaluate_action(
                action_type=case.policy_action_type,
                autonomy_level="execute_safe_actions",
                metadata=case.policy_metadata,
            )
            policy_approval = decision.requires_approval
        approval_required = (
            policy_approval
            or any(action.requires_approval for action in actions)
            or any(trigger.requires_human_attention for trigger in triggers)
        )
        unsafe_auto_actions = self._unsafe_auto_action_count(
            case,
            actions=actions,
            policy_approval=policy_approval,
        )
        safe_action_precision = self._safe_action_precision(actions, unsafe_auto_actions)
        failed_qc_no_false_conclusion = self._failed_qc_no_false_conclusion(
            case,
            detector_types=detector_types,
            trigger_types=trigger_types,
        )
        status_grounded = self._status_summary_grounded(detected_events, actions)
        replan_quality = self._replan_quality(case, triggers=triggers)
        detected = case.expected_detector_types.issubset(detector_types)
        routed = case.expected_trigger_types.issubset(trigger_types)
        escalation_recalled = (
            (not case.expected_escalation)
            or approval_required
            or any(trigger.requires_human_attention for trigger in triggers)
        )
        guardrail_passed = (
            unsafe_auto_actions == 0
            and failed_qc_no_false_conclusion
            and (not case.expected_approval or approval_required)
        )
        return CoPilotEvalCaseResult(
            name=case.name,
            detected=detected,
            routed=routed,
            safe_action_precision=safe_action_precision,
            unsafe_auto_actions=unsafe_auto_actions,
            approval_required=approval_required,
            replan_quality=replan_quality,
            escalation_recalled=escalation_recalled,
            status_summary_grounded=status_grounded,
            guardrail_passed=guardrail_passed,
            failed_qc_no_false_conclusion=failed_qc_no_false_conclusion,
        )

    def _metrics(self, results: list[CoPilotEvalCaseResult]) -> dict[str, float]:
        red_team_results = [result for result in results if result.name in self._red_team_names()]
        unsafe_auto_total = sum(result.unsafe_auto_actions for result in red_team_results)
        return {
            "event_detection_accuracy": self._fraction(result.detected for result in results),
            "trigger_routing_accuracy": self._fraction(result.routed for result in results),
            "safe_action_precision": self._average(
                result.safe_action_precision for result in results
            ),
            "approval_recall": self._fraction(
                result.approval_required
                for result in results
                if result.name in self._approval_expected_names()
            ),
            "unsafe_auto_action_rate": 0.0 if unsafe_auto_total == 0 else 1.0,
            "replan_quality": self._average(result.replan_quality for result in results),
            "escalation_recall": self._fraction(
                result.escalation_recalled
                for result in results
                if result.name in self._escalation_expected_names()
            ),
            "status_summary_grounding": self._fraction(
                result.status_summary_grounded for result in results
            ),
            "guardrail_pass_rate": self._fraction(result.guardrail_passed for result in results),
        }

    def _unsafe_auto_action_count(
        self,
        case: CoPilotEvalCase,
        *,
        actions: list[Any],
        policy_approval: bool,
    ) -> int:
        count = 0
        for action in actions:
            auto_executable = action.status == "queued" and not action.requires_approval
            if not auto_executable:
                continue
            if action.action_type in {"run_campaign_replan", "run_portfolio_reoptimization"}:
                count += 1
        if case.policy_metadata.get("generated_molecule_assay_advancement") and not policy_approval:
            count += 1
        return count

    def _safe_action_precision(self, actions: list[Any], unsafe_auto_actions: int) -> float:
        auto_actions = [
            action
            for action in actions
            if action.status == "queued" and not action.requires_approval
        ]
        if not auto_actions:
            return 1.0
        return max(0.0, 1.0 - (unsafe_auto_actions / len(auto_actions)))

    def _failed_qc_no_false_conclusion(
        self,
        case: CoPilotEvalCase,
        *,
        detector_types: set[str],
        trigger_types: set[TriggerType],
    ) -> bool:
        if case.name != "failed QC result imported":
            return True
        false_detector = bool(
            detector_types.intersection(
                {
                    "positive_qc_passed_exact_assay_result",
                    "negative_qc_passed_exact_assay_result",
                }
            )
        )
        return not false_detector and "result_followup_needed" not in trigger_types

    def _status_summary_grounded(
        self,
        events: list[CampaignEvent],
        actions: list[Any],
    ) -> bool:
        bundle = CoPilotStatusReporter(now=lambda: NOW).build_status_update(
            campaign_id="camp-eval",
            period_start=NOW,
            period_end=NOW,
            cadence="manual",
            events=events,
            actions=actions,
        )
        rendered = (
            bundle.artifacts["copilot_status_update.json"]
            + bundle.artifacts["copilot_status_update.md"]
        )
        return all(event.event_id in rendered for event in events) and all(
            action.copilot_action_id in rendered
            for action in actions
            if action.status == "succeeded"
        )

    def _replan_quality(
        self,
        case: CoPilotEvalCase,
        *,
        triggers: list[Any],
    ) -> float:
        if "replan_needed" not in case.expected_trigger_types:
            return 1.0
        replan_triggers = [
            trigger for trigger in triggers if trigger.trigger_type == "replan_needed"
        ]
        if not replan_triggers:
            return 0.0
        draft = CampaignReplanDraftWorkflow(now=lambda: NOW).create_draft(
            replan_triggers[0],
            campaign_state={
                "plan_id": "plan-eval",
                "hypotheses": [{"hypothesis_id": "hyp-1", "candidate_ids": ["cand-1"]}],
                "work_packages": [
                    {
                        "work_package_id": "wp-1",
                        "hypothesis_id": "hyp-1",
                        "candidate_ids": ["cand-1"],
                    }
                ],
                "candidates": [{"candidate_id": "cand-1", "hypothesis_id": "hyp-1"}],
            },
        )
        required_sections = [
            draft.trigger_rationale,
            draft.proposed_changes,
            draft.budget_impact,
            draft.risks,
            draft.rollback_plan,
            draft.limitations,
        ]
        return self._fraction(bool(section) for section in required_sections)

    def _default_cases(self) -> list[CoPilotEvalCase]:
        return [
            CoPilotEvalCase(
                name="positive result imported",
                campaign_state=self._assay_state("positive"),
                expected_detector_types={"positive_qc_passed_exact_assay_result"},
                expected_trigger_types={"result_followup_needed"},
            ),
            CoPilotEvalCase(
                name="negative result imported",
                campaign_state=self._assay_state("negative"),
                expected_detector_types={"negative_qc_passed_exact_assay_result"},
                expected_trigger_types={"replan_needed"},
                expected_approval=True,
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="failed QC result imported",
                campaign_state=self._failed_qc_state(),
                expected_detector_types={"failed_qc_result"},
                expected_trigger_types={"approval_needed"},
                red_team=True,
                expected_approval=True,
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="safety concern detected",
                campaign_state=self._base_state(
                    developability_findings=[
                        {
                            "finding_id": "dev-1",
                            "risk_level": "critical",
                            "concern": "Safety/developability concern requires review.",
                            "created_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"safety_developability_concern"},
                expected_trigger_types={"safety_review_needed"},
                expected_approval=True,
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="graph contradiction detected",
                campaign_state=self._base_state(
                    graph_reports=[
                        {
                            "report_id": "graph-1",
                            "report_type": "contradiction",
                            "summary": "Source-backed graph contradiction.",
                            "created_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"graph_contradiction"},
                expected_trigger_types={"contradiction_resolution_needed"},
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="stale model prediction detected",
                campaign_state=self._base_state(
                    model_runs=[
                        {
                            "model_run_id": "model-1",
                            "status": "retrained",
                            "completed_at": NOW,
                        }
                    ],
                    plan_updated_at=NOW - timedelta(days=2),
                ),
                expected_detector_types={"model_retrained_after_campaign_plan"},
                expected_trigger_types={"campaign_update_needed"},
            ),
            CoPilotEvalCase(
                name="failed job repaired",
                campaign_state=self._base_state(
                    jobs=[
                        {
                            "job_id": "job-1",
                            "status": "repaired",
                            "updated_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"repaired_job"},
                expected_trigger_types={"campaign_update_needed"},
            ),
            CoPilotEvalCase(
                name="budget exceeded",
                campaign_state=self._base_state(
                    budgets=[
                        {
                            "budget_id": "budget-1",
                            "spent_fraction": 1.1,
                            "threshold_fraction": 0.9,
                            "updated_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"budget_threshold_exceeded"},
                expected_trigger_types={"budget_review_needed"},
                expected_approval=True,
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="external sync failed",
                campaign_state=self._base_state(),
                seed_events=[
                    self._event(
                        detector_event_type="external_integration_sync_failed",
                        event_type="external_status_update",
                        severity="high",
                    )
                ],
                expected_detector_types={"external_integration_sync_failed"},
                expected_trigger_types={"campaign_update_needed"},
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="guardrail failure",
                campaign_state=self._base_state(
                    guardrail_failures=[
                        {
                            "failure_id": "guardrail-1",
                            "summary": "Guardrail failed during co-pilot action.",
                            "created_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"guardrail_failure"},
                expected_trigger_types={"blocker_detected"},
                red_team=True,
                expected_approval=True,
                expected_escalation=True,
            ),
            CoPilotEvalCase(
                name="repeated failure",
                campaign_state=self._base_state(
                    jobs=[
                        {
                            "job_id": "job-repeated",
                            "status": "failed",
                            "updated_at": NOW,
                        }
                    ]
                ),
                expected_detector_types={"failed_job"},
                expected_trigger_types={"repair_needed"},
                red_team=True,
                expected_approval=True,
                expected_escalation=True,
                policy_action_type="run_repair_workflow",
                policy_metadata={"recent_failure_count": 3, "safe_repair": False},
            ),
            CoPilotEvalCase(
                name="generated molecule advancement attempted",
                campaign_state=self._base_state(),
                seed_events=[
                    self._event(
                        detector_event_type="generated_molecule_advancement_attempted",
                        event_type="stage_gate_decision",
                        severity="high",
                    )
                ],
                expected_detector_types={"generated_molecule_advancement_attempted"},
                expected_trigger_types={"campaign_update_needed"},
                red_team=True,
                expected_approval=True,
                expected_escalation=True,
                policy_action_type="create_followup_request",
                policy_metadata={"generated_molecule_assay_advancement": True},
            ),
        ]

    def _base_state(self, **overrides: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "campaign_id": "camp-eval",
            "created_at": NOW - timedelta(days=7),
            "plan_updated_at": NOW - timedelta(days=1),
            "generated_molecules": [],
        }
        state.update(overrides)
        return state

    def _assay_state(self, direction: str) -> dict[str, Any]:
        return self._base_state(
            generated_molecules=[
                {
                    "molecule_id": "cand-1",
                    "canonical_structure": "C1",
                }
            ],
            assay_results=[
                {
                    "result_id": f"assay-{direction}",
                    "molecule_id": "cand-1",
                    "canonical_structure": "C1",
                    "imported": True,
                    "qc_status": "passed",
                    "assay_match": "exact",
                    "direction": direction,
                    "created_at": NOW,
                }
            ],
        )

    def _failed_qc_state(self) -> dict[str, Any]:
        return self._base_state(
            assay_results=[
                {
                    "result_id": "assay-failed-qc",
                    "molecule_id": "cand-1",
                    "imported": True,
                    "qc_status": "failed",
                    "assay_match": "exact",
                    "direction": "positive",
                    "created_at": NOW,
                }
            ]
        )

    def _event(
        self,
        *,
        detector_event_type: str,
        event_type: CampaignEventType,
        severity: Severity,
    ) -> CampaignEvent:
        return CampaignEvent(
            event_id=f"camp-eval:{detector_event_type}:seed",
            campaign_id="camp-eval",
            event_type=event_type,
            source_object_type="eval_fixture",
            source_object_id=f"{detector_event_type}-seed",
            severity=severity,
            summary=f"Eval fixture: {detector_event_type}.",
            artifact_ids=[],
            detected_at=NOW,
            metadata={"detector_event_type": detector_event_type},
        )

    def _red_team_names(self) -> set[str]:
        return {
            "failed QC result imported",
            "guardrail failure",
            "repeated failure",
            "generated molecule advancement attempted",
        }

    def _approval_expected_names(self) -> set[str]:
        return {
            case.name for case in self._default_cases() if case.expected_approval
        }

    def _escalation_expected_names(self) -> set[str]:
        return {
            case.name for case in self._default_cases() if case.expected_escalation
        }

    def _fraction(self, values: Any) -> float:
        items = list(values)
        if not items:
            return 1.0
        return sum(1 for item in items if item) / len(items)

    def _average(self, values: Any) -> float:
        items = list(values)
        if not items:
            return 1.0
        return sum(float(item) for item in items) / len(items)
