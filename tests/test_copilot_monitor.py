from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.monitor import CampaignMonitor

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 6, 4, 11, 0, tzinfo=UTC)


class SyntheticCampaignStore:
    def __init__(self) -> None:
        self.active_campaigns = [
            {
                "campaign_id": "camp-1",
                "project_id": "project-1",
                "program_id": "program-1",
                "status": "active",
                "updated_at": NOW,
            },
            {
                "campaign_id": "camp-2",
                "project_id": "project-2",
                "program_id": "program-1",
                "status": "active",
                "updated_at": NOW,
            },
        ]
        self.changes = {
            "camp-1": [
                {
                    "change_id": "change-1",
                    "change_type": "portfolio_updated",
                    "summary": "Portfolio priorities changed.",
                    "severity": "medium",
                    "artifact_ids": ["artifact-ok", "artifact-secret"],
                    "metadata": {"api_token": "secret-token", "visible": "ok"},
                    "changed_at": NOW,
                }
            ]
        }

    def list_active_campaigns(
        self,
        *,
        actor_id: str,
        project_ids: set[str] | None,
    ) -> list[dict[str, Any]]:
        return [
            campaign
            for campaign in self.active_campaigns
            if project_ids is None or campaign["project_id"] in project_ids
        ]

    def list_recent_changes(
        self,
        *,
        campaign_id: str,
        since: datetime | None,
        actor_id: str,
        project_id: str | None,
    ) -> list[dict[str, Any]]:
        return self.changes.get(campaign_id, [])


class SyntheticExperimentalResultStore:
    def list_new_imports(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "import_id": "assay-1",
                "summary": "Assay result import completed.",
                "artifact_ids": ["artifact-ok"],
                "imported_at": NOW,
                "metadata": {"result_kind": "measured"},
            },
            {
                "import_id": "prediction-1",
                "summary": "Predicted model score imported.",
                "artifact_ids": ["artifact-ok"],
                "imported_at": NOW,
                "metadata": {"result_kind": "model_prediction"},
            },
        ]


class SyntheticReviewWorkspaceStore:
    def list_recent_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "decision_id": "review-1",
                "summary": "Reviewer requested follow-up planning.",
                "artifact_ids": [],
                "decided_at": NOW,
                "metadata": {},
            }
        ]


class SyntheticKnowledgeGraphStore:
    def list_recent_reports(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "report_id": "graph-1",
                "report_type": "contradiction",
                "summary": "Graph contradiction detected.",
                "artifact_ids": ["artifact-ok"],
                "created_at": NOW,
                "metadata": {},
            },
            {
                "report_id": "graph-2",
                "report_type": "staleness",
                "summary": "Decision is stale.",
                "artifact_ids": [],
                "created_at": NOW,
                "metadata": {},
            },
        ]


class SyntheticJobStore:
    def list_recent_statuses(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "job_id": "job-1",
                "status": "failed",
                "summary": "Evaluation job failed.",
                "artifact_ids": [],
                "updated_at": NOW,
                "metadata": {},
            },
            {
                "job_id": "job-2",
                "status": "repaired",
                "summary": "Repair workflow completed.",
                "artifact_ids": [],
                "updated_at": NOW,
                "metadata": {},
            },
        ]


class SyntheticIntegrationStore:
    def list_recent_syncs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "sync_id": "sync-1",
                "summary": "External tracker sync completed.",
                "artifact_ids": [],
                "completed_at": NOW,
                "metadata": {"secret": "hidden"},
            }
        ]


class SyntheticEvaluationStore:
    def list_recent_reports(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "evaluation_id": "eval-1",
                "summary": "Evaluation report created.",
                "artifact_ids": ["artifact-ok"],
                "created_at": NOW,
                "metadata": {},
            }
        ]


class SyntheticArtifactRegistry:
    def __init__(self) -> None:
        self.read_attempts: list[str] = []
        self.unauthorized_read_attempts: list[str] = []

    def list_recent_artifacts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "artifact_id": "artifact-ok",
                "artifact_type": "campaign_report",
                "summary": "Campaign artifact changed.",
                "created_at": NOW,
                "metadata": {"password": "secret-password"},
            },
            {
                "artifact_id": "artifact-denied",
                "artifact_type": "restricted",
                "summary": "Restricted artifact.",
                "created_at": NOW,
                "metadata": {},
            },
        ]

    def can_read_artifact(
        self,
        *,
        artifact_id: str,
        actor_id: str,
        project_id: str | None,
    ) -> bool:
        return artifact_id != "artifact-denied" and artifact_id != "artifact-secret"

    def get_artifact_summary(
        self,
        *,
        artifact_id: str,
        actor_id: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        self.read_attempts.append(artifact_id)
        if artifact_id in {"artifact-denied", "artifact-secret"}:
            self.unauthorized_read_attempts.append(artifact_id)
        return {"artifact_id": artifact_id, "summary": f"Artifact {artifact_id}"}


def _monitor(artifact_registry: SyntheticArtifactRegistry) -> CampaignMonitor:
    return CampaignMonitor(
        campaign_store=SyntheticCampaignStore(),
        experimental_result_store=SyntheticExperimentalResultStore(),
        review_workspace_store=SyntheticReviewWorkspaceStore(),
        knowledge_graph_store=SyntheticKnowledgeGraphStore(),
        job_store=SyntheticJobStore(),
        integration_store=SyntheticIntegrationStore(),
        evaluation_store=SyntheticEvaluationStore(),
        artifact_registry=artifact_registry,
        actor_id="user-1",
        project_ids={"project-1"},
        now=lambda: NOW,
    )


def test_campaign_monitor_emits_events_from_synthetic_stores():
    artifact_registry = SyntheticArtifactRegistry()

    events = _monitor(artifact_registry).poll_active_campaigns(since=SINCE)

    event_types = {event.event_type for event in events}
    assert event_types == {
        "assay_result_imported",
        "review_decision_added",
        "graph_contradiction_detected",
        "stale_decision_detected",
        "job_failed",
        "job_repaired",
        "integration_sync_completed",
        "evaluation_report_created",
        "portfolio_updated",
        "external_status_update",
    }
    assert {event.campaign_id for event in events} == {"camp-1"}
    assert "prediction-1" not in {event.source_object_id for event in events}


def test_campaign_monitor_respects_artifact_authorization_and_redacts_secrets():
    artifact_registry = SyntheticArtifactRegistry()

    events = _monitor(artifact_registry).poll_active_campaigns(since=SINCE)

    assert "artifact-denied" not in {
        artifact_id for event in events for artifact_id in event.artifact_ids
    }
    assert "artifact-secret" not in {
        artifact_id for event in events for artifact_id in event.artifact_ids
    }
    assert artifact_registry.unauthorized_read_attempts == []
    assert "artifact-denied" not in artifact_registry.read_attempts
    serialized = " ".join(event.model_dump_json() for event in events)
    assert "secret-token" not in serialized
    assert "secret-password" not in serialized
    assert "hidden" not in serialized


def test_campaign_monitor_can_write_events_and_audit_without_other_store_side_effects():
    artifact_registry = SyntheticArtifactRegistry()
    written_events: list[str] = []
    audit_records: list[dict[str, Any]] = []
    monitor = _monitor(artifact_registry)

    events = monitor.poll_active_campaigns(
        since=SINCE,
        event_writer=lambda event: written_events.append(event.event_id),
        audit_writer=lambda record: audit_records.append(record),
    )

    assert written_events == [event.event_id for event in events]
    assert audit_records
    assert all(record["actor_id"] == "user-1" for record in audit_records)
