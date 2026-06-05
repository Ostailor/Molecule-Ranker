from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from molecule_ranker.copilot.schemas import (
    CampaignCoPilotSession,
    CampaignEvent,
    CampaignEventType,
    Severity,
)

EventWriter = Callable[[CampaignEvent], None]
AuditWriter = Callable[[dict[str, Any]], None]

_SECRET_KEYS = ("secret", "token", "password", "api_key", "credential")
_PREDICTION_KINDS = {"model_prediction", "prediction", "predicted", "in_silico_prediction"}


class CampaignMonitor:
    def __init__(
        self,
        *,
        campaign_store: Any | None = None,
        experimental_result_store: Any | None = None,
        review_workspace_store: Any | None = None,
        knowledge_graph_store: Any | None = None,
        job_store: Any | None = None,
        integration_store: Any | None = None,
        evaluation_store: Any | None = None,
        artifact_registry: Any | None = None,
        actor_id: str | None = None,
        project_ids: set[str] | None = None,
        now: Callable[[], datetime] | None = None,
        sessions: list[CampaignCoPilotSession] | None = None,
        events: list[CampaignEvent] | None = None,
    ) -> None:
        self.campaign_store = campaign_store
        self.experimental_result_store = experimental_result_store
        self.review_workspace_store = review_workspace_store
        self.knowledge_graph_store = knowledge_graph_store
        self.job_store = job_store
        self.integration_store = integration_store
        self.evaluation_store = evaluation_store
        self.artifact_registry = artifact_registry
        self.actor_id = actor_id or "copilot"
        self.project_ids = project_ids
        self._now = now or (lambda: datetime.now(UTC))
        self._sessions = sessions or []
        self._events = events or []

    def active_sessions(self) -> list[CampaignCoPilotSession]:
        return [session for session in self._sessions if session.status == "active"]

    def events_for_campaign(self, campaign_id: str) -> list[CampaignEvent]:
        return [event for event in self._events if event.campaign_id == campaign_id]

    def poll_active_campaigns(
        self,
        *,
        since: datetime | None = None,
        event_writer: EventWriter | None = None,
        audit_writer: AuditWriter | None = None,
    ) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        for campaign in self._active_campaign_records():
            if not self._campaign_authorized(campaign):
                continue
            campaign_events = self._poll_campaign(campaign, since=since)
            events.extend(campaign_events)
            if audit_writer is not None:
                audit_writer(
                    {
                        "actor_id": self.actor_id,
                        "campaign_id": campaign["campaign_id"],
                        "project_id": campaign.get("project_id"),
                        "event_count": len(campaign_events),
                        "audited_at": self._now(),
                    }
                )
        if event_writer is not None:
            for event in events:
                event_writer(event)
        return events

    def _active_campaign_records(self) -> list[dict[str, Any]]:
        if self.campaign_store is None:
            return [
                {
                    "campaign_id": session.campaign_id,
                    "project_id": session.project_id,
                    "program_id": session.program_id,
                    "status": session.status,
                    "updated_at": session.last_check_at or session.started_at,
                    "metadata": session.metadata,
                }
                for session in self.active_sessions()
            ]
        campaigns = self.campaign_store.list_active_campaigns(
            actor_id=self.actor_id,
            project_ids=self.project_ids,
        )
        return [dict(campaign) for campaign in campaigns]

    def _campaign_authorized(self, campaign: dict[str, Any]) -> bool:
        if self.project_ids is None:
            return True
        project_id = campaign.get("project_id")
        return project_id in self.project_ids

    def _poll_campaign(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        events: list[CampaignEvent] = []
        events.extend(self._campaign_change_events(campaign, since=since))
        events.extend(self._assay_import_events(campaign, since=since))
        events.extend(self._review_decision_events(campaign, since=since))
        events.extend(self._graph_report_events(campaign, since=since))
        events.extend(self._job_status_events(campaign, since=since))
        events.extend(self._integration_sync_events(campaign, since=since))
        events.extend(self._evaluation_report_events(campaign, since=since))
        events.extend(self._artifact_events(campaign, since=since))
        return events

    def _campaign_change_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.campaign_store is None or not hasattr(self.campaign_store, "list_recent_changes"):
            return []
        records = self.campaign_store.list_recent_changes(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        return [
            self._event(
                campaign,
                source_object_type="campaign_change",
                source_object_id=str(record.get("change_id", record.get("id", "change"))),
                event_type=self._campaign_change_event_type(record),
                severity=self._severity(record.get("severity", "medium")),
                summary=str(record.get("summary", "Campaign state changed.")),
                artifact_ids=self._authorized_artifact_ids(
                    campaign, record.get("artifact_ids", [])
                ),
                detected_at=self._timestamp(record, "changed_at"),
                metadata=record.get("metadata", {}),
            )
            for record in records
        ]

    def _assay_import_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.experimental_result_store is None:
            return []
        records = self.experimental_result_store.list_new_imports(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        events: list[CampaignEvent] = []
        for record in records:
            metadata = dict(record.get("metadata", {}))
            result_kind = str(metadata.get("result_kind", metadata.get("outcome_type", ""))).lower()
            if result_kind in _PREDICTION_KINDS:
                continue
            events.append(
                self._event(
                    campaign,
                    source_object_type="assay_import",
                    source_object_id=str(record.get("import_id", record.get("id", "assay_import"))),
                    event_type="assay_result_imported",
                    severity=self._severity(record.get("severity", "medium")),
                    summary=str(record.get("summary", "Assay result import detected.")),
                    artifact_ids=self._authorized_artifact_ids(
                        campaign, record.get("artifact_ids", [])
                    ),
                    detected_at=self._timestamp(record, "imported_at"),
                    metadata=metadata,
                )
            )
        return events

    def _review_decision_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.review_workspace_store is None:
            return []
        records = self.review_workspace_store.list_recent_decisions(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        return [
            self._event(
                campaign,
                source_object_type="review_decision",
                source_object_id=str(record.get("decision_id", record.get("id", "decision"))),
                event_type="review_decision_added",
                severity=self._severity(record.get("severity", "medium")),
                summary=str(record.get("summary", "Review decision added.")),
                artifact_ids=self._authorized_artifact_ids(
                    campaign, record.get("artifact_ids", [])
                ),
                detected_at=self._timestamp(record, "decided_at"),
                metadata=record.get("metadata", {}),
            )
            for record in records
        ]

    def _graph_report_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.knowledge_graph_store is None:
            return []
        records = self.knowledge_graph_store.list_recent_reports(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        events: list[CampaignEvent] = []
        for record in records:
            report_type = str(record.get("report_type", "")).lower()
            event_type: CampaignEventType = (
                "stale_decision_detected"
                if report_type == "staleness"
                else "graph_contradiction_detected"
            )
            events.append(
                self._event(
                    campaign,
                    source_object_type="knowledge_graph_report",
                    source_object_id=str(record.get("report_id", record.get("id", "graph_report"))),
                    event_type=event_type,
                    severity=self._severity(record.get("severity", "high")),
                    summary=str(record.get("summary", "Knowledge graph report detected.")),
                    artifact_ids=self._authorized_artifact_ids(
                        campaign, record.get("artifact_ids", [])
                    ),
                    detected_at=self._timestamp(record, "created_at"),
                    metadata=record.get("metadata", {}),
                )
            )
        return events

    def _job_status_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.job_store is None:
            return []
        records = self.job_store.list_recent_statuses(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        events: list[CampaignEvent] = []
        for record in records:
            status = str(record.get("status", "")).lower()
            if status not in {"failed", "repaired"}:
                continue
            event_type: CampaignEventType = "job_failed" if status == "failed" else "job_repaired"
            events.append(
                self._event(
                    campaign,
                    source_object_type="job",
                    source_object_id=str(record.get("job_id", record.get("id", "job"))),
                    event_type=event_type,
                    severity=self._severity(
                        record.get(
                            "severity",
                            "high" if status == "failed" else "medium",
                        )
                    ),
                    summary=str(record.get("summary", f"Job {status}.")),
                    artifact_ids=self._authorized_artifact_ids(
                        campaign, record.get("artifact_ids", [])
                    ),
                    detected_at=self._timestamp(record, "updated_at"),
                    metadata=record.get("metadata", {}),
                )
            )
        return events

    def _integration_sync_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.integration_store is None:
            return []
        records = self.integration_store.list_recent_syncs(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        return [
            self._event(
                campaign,
                source_object_type="integration_sync",
                source_object_id=str(record.get("sync_id", record.get("id", "sync"))),
                event_type="integration_sync_completed",
                severity=self._severity(record.get("severity", "low")),
                summary=str(record.get("summary", "Integration sync completed.")),
                artifact_ids=self._authorized_artifact_ids(
                    campaign, record.get("artifact_ids", [])
                ),
                detected_at=self._timestamp(record, "completed_at"),
                metadata=record.get("metadata", {}),
            )
            for record in records
        ]

    def _evaluation_report_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.evaluation_store is None:
            return []
        records = self.evaluation_store.list_recent_reports(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        return [
            self._event(
                campaign,
                source_object_type="evaluation_report",
                source_object_id=str(record.get("evaluation_id", record.get("id", "evaluation"))),
                event_type="evaluation_report_created",
                severity=self._severity(record.get("severity", "medium")),
                summary=str(record.get("summary", "Evaluation report created.")),
                artifact_ids=self._authorized_artifact_ids(
                    campaign, record.get("artifact_ids", [])
                ),
                detected_at=self._timestamp(record, "created_at"),
                metadata=record.get("metadata", {}),
            )
            for record in records
        ]

    def _artifact_events(
        self,
        campaign: dict[str, Any],
        *,
        since: datetime | None,
    ) -> list[CampaignEvent]:
        if self.artifact_registry is None or not hasattr(
            self.artifact_registry, "list_recent_artifacts"
        ):
            return []
        records = self.artifact_registry.list_recent_artifacts(
            campaign_id=campaign["campaign_id"],
            since=since,
            actor_id=self.actor_id,
            project_id=campaign.get("project_id"),
        )
        events: list[CampaignEvent] = []
        for record in records:
            artifact_id = str(record.get("artifact_id", record.get("id", "")))
            if not artifact_id or not self._can_read_artifact(campaign, artifact_id):
                continue
            summary_record = self.artifact_registry.get_artifact_summary(
                artifact_id=artifact_id,
                actor_id=self.actor_id,
                project_id=campaign.get("project_id"),
            )
            events.append(
                self._event(
                    campaign,
                    source_object_type=str(record.get("artifact_type", "artifact")),
                    source_object_id=artifact_id,
                    event_type="external_status_update",
                    severity=self._severity(record.get("severity", "info")),
                    summary=str(
                        summary_record.get(
                            "summary",
                            record.get("summary", "Artifact changed."),
                        )
                    ),
                    artifact_ids=[artifact_id],
                    detected_at=self._timestamp(record, "created_at"),
                    metadata=record.get("metadata", {}),
                )
            )
        return events

    def _event(
        self,
        campaign: dict[str, Any],
        *,
        source_object_type: str,
        source_object_id: str,
        event_type: CampaignEventType,
        severity: Severity,
        summary: str,
        artifact_ids: list[str],
        detected_at: datetime,
        metadata: dict[str, Any],
    ) -> CampaignEvent:
        event_id = f"{campaign['campaign_id']}:{event_type}:{source_object_id}"
        return CampaignEvent(
            event_id=event_id,
            campaign_id=str(campaign["campaign_id"]),
            event_type=event_type,
            source_object_type=source_object_type,
            source_object_id=source_object_id,
            severity=severity,
            summary=self._sanitize_text(summary),
            artifact_ids=artifact_ids,
            detected_at=detected_at,
            metadata=self._sanitize_metadata(
                {
                    **metadata,
                    "project_id": campaign.get("project_id"),
                    "program_id": campaign.get("program_id"),
                }
            ),
        )

    def _authorized_artifact_ids(
        self,
        campaign: dict[str, Any],
        artifact_ids: Any,
    ) -> list[str]:
        if not isinstance(artifact_ids, list):
            return []
        return [
            artifact_id
            for artifact_id in map(str, artifact_ids)
            if self._can_read_artifact(campaign, artifact_id)
        ]

    def _can_read_artifact(self, campaign: dict[str, Any], artifact_id: str) -> bool:
        if self.artifact_registry is None or not hasattr(
            self.artifact_registry, "can_read_artifact"
        ):
            return True
        return bool(
            self.artifact_registry.can_read_artifact(
                artifact_id=artifact_id,
                actor_id=self.actor_id,
                project_id=campaign.get("project_id"),
            )
        )

    def _timestamp(self, record: dict[str, Any], key: str) -> datetime:
        value = record.get(key) or record.get("detected_at") or self._now()
        if not isinstance(value, datetime):
            return self._now()
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=UTC)
        return value

    def _campaign_change_event_type(self, record: dict[str, Any]) -> CampaignEventType:
        change_type = str(record.get("change_type", "portfolio_updated"))
        allowed = {
            "hypothesis_status_changed",
            "portfolio_updated",
            "budget_changed",
            "stage_gate_decision",
            "external_status_update",
        }
        return cast(
            CampaignEventType,
            change_type if change_type in allowed else "portfolio_updated",
        )

    def _severity(self, value: Any) -> Severity:
        text = str(value).lower()
        if text in {"info", "low", "medium", "high", "critical"}:
            return cast(Severity, text)
        return "info"

    def _sanitize_metadata(self, value: Any) -> dict[str, Any]:
        sanitized = self._sanitize_value(value)
        return sanitized if isinstance(sanitized, dict) else {}

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, inner_value in value.items():
                key_text = str(key)
                if self._is_secret_key(key_text):
                    continue
                result[key_text] = self._sanitize_value(inner_value)
            return result
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value

    def _sanitize_text(self, value: str) -> str:
        lowered = value.lower()
        if any(marker in lowered for marker in _SECRET_KEYS):
            return "[redacted]"
        return value

    def _is_secret_key(self, key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in _SECRET_KEYS)
