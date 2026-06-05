from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from molecule_ranker.copilot.memory import CoPilotMemory
from molecule_ranker.copilot.schemas import CampaignEvent, CampaignEventType, Severity

_PREDICTION_KINDS = {"model_prediction", "prediction", "predicted", "in_silico_prediction"}


class EventDetector:
    def __init__(
        self,
        *,
        memory: CoPilotMemory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.memory = memory or CoPilotMemory()
        self._now = now or (lambda: datetime.now(UTC))

    def detect_new_events(self, events: list[CampaignEvent]) -> list[CampaignEvent]:
        return [event for event in events if event.event_id not in self.memory.seen_event_ids]

    def detect(self, events: list[CampaignEvent]) -> list[CampaignEvent]:
        return self.detect_new_events(events)

    def detect_campaign_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        events.extend(self._assay_events(campaign_state))
        events.extend(self._developability_events(campaign_state))
        events.extend(self._review_events(campaign_state))
        events.extend(self._graph_events(campaign_state))
        events.extend(self._model_events(campaign_state))
        events.extend(self._portfolio_events(campaign_state))
        events.extend(self._integration_events(campaign_state))
        events.extend(self._job_events(campaign_state))
        events.extend(self._budget_events(campaign_state))
        events.extend(self._work_package_events(campaign_state))
        events.extend(self._guardrail_events(campaign_state))
        events.extend(self._evaluation_events(campaign_state))
        return self.detect_new_events(events)

    def _assay_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("assay_results", []):
            if not record.get("imported", False):
                continue
            if str(record.get("result_kind", "")).lower() in _PREDICTION_KINDS:
                continue
            qc_status = str(record.get("qc_status", "")).lower()
            if qc_status == "failed":
                events.append(
                    self._event(
                        campaign_state,
                        source_object_type="assay_result",
                        source_object_id=self._record_id(record, "result_id", "assay"),
                        event_type="guardrail_failure",
                        severity="high",
                        summary="Imported assay result failed QC.",
                        detected_at=self._timestamp(record, "created_at"),
                        detector_event_type="failed_qc_result",
                        metadata=record,
                    )
                )
                continue
            if qc_status != "passed":
                continue
            if str(record.get("assay_match", "")).lower() != "exact":
                continue
            if not self._generated_structure_matches(campaign_state, record):
                continue
            direction = str(record.get("direction", "")).lower()
            if direction not in {"positive", "negative"}:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="assay_result",
                    source_object_id=self._record_id(record, "result_id", "assay"),
                    event_type="assay_result_imported",
                    severity="medium",
                    summary=f"New {direction} QC-passed exact assay result imported.",
                    detected_at=self._timestamp(record, "created_at"),
                    detector_event_type=f"{direction}_qc_passed_exact_assay_result",
                    metadata=record,
                )
            )
        return events

    def _developability_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        return [
            self._event(
                campaign_state,
                source_object_type="developability_finding",
                source_object_id=self._record_id(record, "finding_id", "developability"),
                event_type="developability_risk_changed",
                severity=self._risk_severity(record.get("risk_level", "medium")),
                summary=str(record.get("concern", "Safety or developability concern detected.")),
                detected_at=self._timestamp(record, "created_at"),
                detector_event_type="safety_developability_concern",
                metadata=record,
            )
            for record in campaign_state.get("developability_findings", [])
        ]

    def _review_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("review_decisions", []):
            decision = str(record.get("decision", "")).lower()
            if decision not in {"rejected", "approved"}:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="review_decision",
                    source_object_id=self._record_id(record, "decision_id", "review"),
                    event_type="review_decision_added",
                    severity="high" if decision == "rejected" else "medium",
                    summary=str(record.get("summary", f"Review {decision} recorded.")),
                    detected_at=self._timestamp(record, "created_at"),
                    detector_event_type=(
                        "review_rejection" if decision == "rejected" else "review_approval"
                    ),
                    metadata=record,
                )
            )
        return events

    def _graph_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("graph_reports", []):
            report_type = str(record.get("report_type", "")).lower()
            if report_type == "contradiction":
                event_type: CampaignEventType = "graph_contradiction_detected"
                detector_event_type = "graph_contradiction"
                severity: Severity = "high"
            elif report_type in {"stale_decision", "staleness"}:
                event_type = "stale_decision_detected"
                detector_event_type = "stale_decision"
                severity = "medium"
            else:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="knowledge_graph_report",
                    source_object_id=self._record_id(record, "report_id", "graph"),
                    event_type=event_type,
                    severity=severity,
                    summary=str(record.get("summary", "Knowledge graph report detected.")),
                    detected_at=self._timestamp(record, "created_at"),
                    detector_event_type=detector_event_type,
                    metadata=record,
                )
            )
        return events

    def _model_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        plan_time = self._plan_time(campaign_state)
        events: list[CampaignEvent] = []
        for record in campaign_state.get("model_runs", []):
            completed_at = self._timestamp(record, "completed_at")
            if str(record.get("status", "")).lower() != "retrained" or completed_at <= plan_time:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="model_run",
                    source_object_id=self._record_id(record, "model_run_id", "model"),
                    event_type="model_retrained",
                    severity="medium",
                    summary="Model retrained after the current campaign plan.",
                    detected_at=completed_at,
                    detector_event_type="model_retrained_after_campaign_plan",
                    metadata=record,
                )
            )
        return events

    def _portfolio_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        plan_time = self._plan_time(campaign_state)
        events: list[CampaignEvent] = []
        for record in campaign_state.get("portfolio_changes", []):
            changed_at = self._timestamp(record, "changed_at")
            if changed_at <= plan_time:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="portfolio_change",
                    source_object_id=self._record_id(record, "change_id", "portfolio"),
                    event_type="portfolio_updated",
                    severity="medium",
                    summary=str(record.get("summary", "Portfolio changed after campaign plan.")),
                    detected_at=changed_at,
                    detector_event_type="portfolio_changed_after_campaign_plan",
                    metadata=record,
                )
            )
        return events

    def _integration_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("integration_syncs", []):
            if str(record.get("status", "")).lower() != "completed":
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="integration_sync",
                    source_object_id=self._record_id(record, "sync_id", "sync"),
                    event_type="integration_sync_completed",
                    severity="low",
                    summary="External integration sync completed.",
                    detected_at=self._timestamp(record, "completed_at"),
                    detector_event_type="external_integration_sync_completed",
                    metadata=record,
                )
            )
        return events

    def _job_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("jobs", []):
            status = str(record.get("status", "")).lower()
            if status not in {"failed", "repaired"}:
                continue
            failed = status == "failed"
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="job",
                    source_object_id=self._record_id(record, "job_id", "job"),
                    event_type="job_failed" if failed else "job_repaired",
                    severity="high" if failed else "medium",
                    summary=f"Campaign job {status}.",
                    detected_at=self._timestamp(record, "updated_at"),
                    detector_event_type="failed_job" if failed else "repaired_job",
                    metadata=record,
                )
            )
        return events

    def _budget_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("budgets", []):
            spent = float(record.get("spent_fraction", 0.0))
            threshold = float(record.get("threshold_fraction", 1.0))
            if spent < threshold:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="budget",
                    source_object_id=self._record_id(record, "budget_id", "budget"),
                    event_type="budget_changed",
                    severity="critical" if spent >= 1.0 else "high",
                    summary="Budget threshold exceeded.",
                    detected_at=self._timestamp(record, "updated_at"),
                    detector_event_type="budget_threshold_exceeded",
                    metadata=record,
                )
            )
        return events

    def _work_package_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("work_packages", []):
            if str(record.get("status", "")).lower() != "blocked":
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="campaign_work_package",
                    source_object_id=self._record_id(record, "work_package_id", "work_package"),
                    event_type="external_status_update",
                    severity="high",
                    summary="Campaign work package is blocked.",
                    detected_at=self._timestamp(record, "updated_at"),
                    detector_event_type="campaign_work_package_blocked",
                    metadata=record,
                )
            )
        return events

    def _guardrail_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        return [
            self._event(
                campaign_state,
                source_object_type="guardrail_failure",
                source_object_id=self._record_id(record, "failure_id", "guardrail"),
                event_type="guardrail_failure",
                severity="critical",
                summary=str(record.get("summary", "Guardrail failure detected.")),
                detected_at=self._timestamp(record, "created_at"),
                detector_event_type="guardrail_failure",
                metadata=record,
            )
            for record in campaign_state.get("guardrail_failures", [])
        ]

    def _evaluation_events(self, campaign_state: dict[str, Any]) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for record in campaign_state.get("evaluation_reports", []):
            delta = float(record.get("performance_delta", 0.0))
            threshold = float(record.get("degradation_threshold", 0.0))
            if delta > threshold:
                continue
            events.append(
                self._event(
                    campaign_state,
                    source_object_type="evaluation_report",
                    source_object_id=self._record_id(record, "evaluation_id", "evaluation"),
                    event_type="evaluation_report_created",
                    severity="high",
                    summary="Evaluation performance degradation detected.",
                    detected_at=self._timestamp(record, "created_at"),
                    detector_event_type="evaluation_performance_degradation",
                    metadata=record,
                )
            )
        return events

    def _event(
        self,
        campaign_state: dict[str, Any],
        *,
        source_object_type: str,
        source_object_id: str,
        event_type: CampaignEventType,
        severity: Severity,
        summary: str,
        detected_at: datetime,
        detector_event_type: str,
        metadata: dict[str, Any],
    ) -> CampaignEvent:
        event_id = f"{campaign_state['campaign_id']}:{detector_event_type}:{source_object_id}"
        return CampaignEvent(
            event_id=event_id,
            campaign_id=str(campaign_state["campaign_id"]),
            event_type=event_type,
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            severity=severity,
            summary=summary,
            artifact_ids=[str(item) for item in metadata.get("artifact_ids", [])],
            detected_at=detected_at,
            metadata={
                key: value
                for key, value in {
                    **metadata,
                    "detector_event_type": detector_event_type,
                }.items()
                if key != "canonical_structure"
            },
        )

    def _generated_structure_matches(
        self,
        campaign_state: dict[str, Any],
        record: dict[str, Any],
    ) -> bool:
        molecule_id = record.get("molecule_id")
        generated = campaign_state.get("generated_molecules", {})
        if molecule_id not in generated:
            return True
        expected = generated[molecule_id].get("canonical_structure")
        observed = record.get("canonical_structure")
        return bool(expected and observed and expected == observed)

    def _timestamp(self, record: dict[str, Any], key: str) -> datetime:
        value = (
            record.get(key)
            or record.get("created_at")
            or record.get("updated_at")
            or self._now()
        )
        if not isinstance(value, datetime):
            return self._now()
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=UTC)
        return value

    def _plan_time(self, campaign_state: dict[str, Any]) -> datetime:
        value = campaign_state.get("campaign_plan_updated_at")
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return datetime.min.replace(tzinfo=UTC)

    def _record_id(self, record: dict[str, Any], key: str, fallback: str) -> str:
        return str(record.get(key, record.get("id", fallback)))

    def _risk_severity(self, value: Any) -> Severity:
        text = str(value).lower()
        if text in {"low", "medium", "high", "critical"}:
            return cast(Severity, text)
        return "medium"
