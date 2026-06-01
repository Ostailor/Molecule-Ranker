from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.campaigns import (
    Campaign,
    CampaignBudget,
    CampaignObjective,
    CampaignPlan,
    CampaignStore,
    CampaignWorkPackage,
    export_campaign_summary_package,
    export_high_level_work_package_list,
    export_validation_handoff_package,
    import_external_status_update,
    ingest_external_completion_event,
    link_external_workflow_task,
)
from molecule_ranker.integrations.connectors import BenchlingConnector, GenericRESTConnector
from molecule_ranker.integrations.connectors.base import ConnectorError
from molecule_ranker.integrations.exporters import ExportPermissionError
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    ExternalRecordRef,
    IntegrationCredentialRef,
    SyncJob,
)
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.platform import PlatformDatabase


def test_campaign_summary_export_is_dry_run_by_default_with_generic_rest(
    tmp_path: Path,
) -> None:
    campaign, plan = _campaign_plan()
    client = FakeRESTClient({})
    connector = GenericRESTConnector(_generic_config(tmp_path), http_client=client)

    result = export_campaign_summary_package(
        campaign,
        plan,
        tmp_path / "campaign-summary",
        connector=connector,
    )

    assert result.package.package_type == "campaign_summary_package"
    assert result.package.external_write_ready is False
    assert result.external_refs == []
    assert client.requests == []
    package_text = Path(result.package.output_dir, "package.md").read_text()
    assert "research-management handoffs" in package_text
    assert "protocol" not in result.payload


def test_campaign_export_write_requires_explicit_permission(tmp_path: Path) -> None:
    campaign, plan = _campaign_plan()

    with pytest.raises(ExportPermissionError):
        export_high_level_work_package_list(
            campaign,
            plan,
            tmp_path / "work-packages",
            external_write=True,
            explicit_permission=False,
        )


def test_campaign_work_package_export_rejects_protocol_text(tmp_path: Path) -> None:
    campaign, plan = _campaign_plan()
    bad_package = plan.work_packages[0].model_copy(update={"description": "Run this protocol."})
    bad_plan = plan.model_copy(update={"work_packages": [bad_package]})

    with pytest.raises(ValueError, match="protocols or synthesis"):
        export_high_level_work_package_list(campaign, bad_plan, tmp_path / "bad")


def test_campaign_summary_export_writes_to_generic_rest_when_enabled(
    tmp_path: Path,
) -> None:
    campaign, plan = _campaign_plan()
    client = FakeRESTClient(
        {
            ("POST", "https://workflow.example/api/campaigns"): {
                "id": "external-campaign-package-1",
                "url": "https://workflow.example/tasks/external-campaign-package-1",
            }
        }
    )
    connector = GenericRESTConnector(
        _generic_config(
            tmp_path,
            mode="write_enabled",
            allow_writes=True,
            explicit_write_permission=True,
        ),
        http_client=client,
    )

    result = export_campaign_summary_package(
        campaign,
        plan,
        tmp_path / "campaign-summary",
        connector=connector,
        external_write=True,
        explicit_permission=True,
    )

    assert result.package.external_write_ready is True
    assert result.external_refs[0].external_record_id == "external-campaign-package-1"
    assert result.external_refs[0].external_record_type == "campaign_summary_package"
    assert client.requests[-1]["method"] == "POST"
    assert client.requests[-1]["json"]["payload"]["campaign_id"] == campaign.campaign_id


def test_validation_handoff_export_writes_benchling_notebook_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    campaign, plan = _campaign_plan()
    client = FakeRESTClient(
        {
            ("POST", "https://benchling.example/api/v2/entries"): {
                "id": "entry-1",
                "webURL": "https://benchling.example/entry-1",
            }
        }
    )
    connector = BenchlingConnector(
        _benchling_config(mode="write_enabled", allow_writes=True, explicit_write_permission=True),
        http_client=client,
    )

    result = export_validation_handoff_package(
        campaign,
        plan,
        tmp_path / "validation-handoff",
        connector=connector,
        external_write=True,
        explicit_permission=True,
    )

    assert result.package.package_type == "validation_handoff_package"
    assert result.external_refs[0].external_record_id == "entry-1"
    assert result.external_refs[0].external_record_type == "notebook_entry"
    assert client.requests[-1]["json"]["fields"]["summary"].startswith("validation_handoff")


def test_link_external_workflow_task_to_campaign_work_package(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    integration_store = IntegrationStore(database, project_id="project-1")
    external_ref = ExternalRecordRef(
        external_system_id="generic-rest-test",
        external_record_type="workflow_task",
        external_record_id="task-123",
        retrieved_at=datetime.now(UTC),
    )

    mapping = link_external_workflow_task(
        integration_store,
        project_id="project-1",
        work_package_id="wp-1",
        external_ref=external_ref,
        created_by="admin-user",
    )

    assert mapping.internal_entity_type == "campaign_work_package"
    assert mapping.internal_entity_id == "wp-1"
    assert mapping.external_ref.external_record_id == "task-123"


def test_import_external_status_update_validates_and_does_not_create_evidence(
    tmp_path: Path,
) -> None:
    store = _campaign_store(tmp_path)
    external_ref = ExternalRecordRef(
        external_system_id="generic-rest-test",
        external_record_type="workflow_task",
        external_record_id="task-123",
        retrieved_at=datetime.now(UTC),
    )

    event = import_external_status_update(
        store,
        work_package_id="wp-1",
        payload={"status": "done", "summary": "External task completed."},
        external_ref=external_ref,
    )

    assert event.event_type == "completed"
    assert event.metadata["does_not_create_assay_evidence"] is True
    assert store.get_work_package("wp-1").status == "completed"
    assert any(
        item.event_id == event.event_id and item.event_type == "completed"
        for item in store.list_execution_events("campaign-1")
    )

    with pytest.raises(ValueError, match="Unsupported external campaign status"):
        import_external_status_update(
            store,
            work_package_id="wp-1",
            payload={"status": "experimentally_active"},
        )


def test_ingest_external_failure_event_adds_campaign_event_and_sync_record(
    tmp_path: Path,
) -> None:
    store = _campaign_store(tmp_path)
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    integration_store = IntegrationStore(database, project_id="project-1")
    integration_store.create_sync_job(
        SyncJob(
            sync_job_id="sync-1",
            external_system_id="generic-rest-test",
            project_id="project-1",
            direction="import",
            object_types=["campaign_work_package_status"],
            mode="dry_run",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    external_ref = ExternalRecordRef(
        external_system_id="generic-rest-test",
        external_record_type="workflow_task",
        external_record_id="task-123",
        retrieved_at=datetime.now(UTC),
    )

    event = ingest_external_completion_event(
        store,
        integration_store,
        work_package_id="wp-1",
        payload={"status": "failed", "summary": "External task failed QC."},
        external_ref=external_ref,
        sync_job_id="sync-1",
    )

    assert event.event_type == "failed"
    records = integration_store.list_sync_records(sync_job_id="sync-1")
    assert records[0].internal_entity_type == "campaign_work_package"
    assert records[0].metadata["does_not_create_assay_evidence"] is True


def test_generic_rest_write_still_blocked_without_connector_write_mode(tmp_path: Path) -> None:
    campaign, plan = _campaign_plan()
    connector = GenericRESTConnector(_generic_config(tmp_path), http_client=FakeRESTClient({}))

    with pytest.raises(ConnectorError, match="blocked by default"):
        export_campaign_summary_package(
            campaign,
            plan,
            tmp_path / "blocked",
            connector=connector,
            external_write=True,
            explicit_permission=True,
        )


class FakeRESTClient:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        key = (method, url)
        if key not in self.routes:
            return FakeResponse({"message": "not found"}, status_code=404)
        return FakeResponse(self.routes[key])


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ConnectorError(f"HTTP {self.status_code}")


def _campaign_plan() -> tuple[Campaign, CampaignPlan]:
    now = datetime.now(UTC)
    campaign = Campaign(
        campaign_id="campaign-1",
        project_id="project-1",
        program_id="program-1",
        name="Campaign 1",
        description="Planning artifact.",
        disease_focus=["Parkinson disease"],
        target_focus=["MAOB"],
        hypothesis_ids=["hypothesis-1"],
        portfolio_selection_ids=["selection-1"],
        status="under_review",
        created_at=now,
        updated_at=now,
        metadata={},
    )
    objective = CampaignObjective(
        objective_id="objective-1",
        campaign_id=campaign.campaign_id,
        name="Resolve uncertainty",
        objective_type="learn_from_uncertainty",
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-1"],
        success_criteria=["Decision-ready review summary."],
        stop_criteria=["Critical risk remains unresolved."],
        priority_weight=0.8,
        metadata={},
    )
    work_package = CampaignWorkPackage(
        work_package_id="wp-1",
        campaign_id=campaign.campaign_id,
        objective_ids=[objective.objective_id],
        package_type="expert_review",
        title="Expert review",
        description="High-level review of evidence and uncertainty.",
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="review",
        dependencies=[],
        required_approvals=["campaign:approve"],
        estimated_cost=None,
        cost_units="relative_units",
        estimated_review_hours=1.0,
        estimated_compute_units=0.0,
        estimated_assay_slots=0,
        status="ready",
        blocking_reasons=[],
        warnings=[],
        metadata={},
    )
    budget = CampaignBudget(
        budget_id="budget-1",
        campaign_id=campaign.campaign_id,
        max_total_cost=None,
        cost_units="relative_units",
        max_assay_slots=1,
        max_review_hours=2.0,
        max_compute_units=2.0,
        max_codex_tasks=1,
        max_external_sync_jobs=1,
        reserved_budget={},
        metadata={},
    )
    plan = CampaignPlan(
        campaign_plan_id="plan-1",
        campaign_id=campaign.campaign_id,
        objectives=[objective],
        work_packages=[work_package],
        budget=budget,
        stage_gates=[
            {
                "gate_id": "gate-1",
                "gate_type": "campaign_approval",
                "campaign_id": campaign.campaign_id,
                "approval_status": "pending",
                "required_permissions": ["campaign:approve"],
            }
        ],
        dependency_graph={"nodes": ["wp-1"], "edges": []},
        expected_learning_value=0.6,
        risk_summary={},
        uncertainty_summary={},
        budget_summary={"review_hours": 1.0},
        recommended_sequence=["wp-1"],
        replan_triggers=["external_sync_update"],
        human_approval_required=True,
        warnings=[],
        created_at=now,
        metadata={},
    )
    return campaign, plan


def _campaign_store(tmp_path: Path) -> CampaignStore:
    campaign, plan = _campaign_plan()
    store = CampaignStore(tmp_path / "campaigns.sqlite")
    store.create_campaign(campaign)
    store.save_campaign_plan(plan)
    return store


def _generic_config(
    tmp_path: Path,
    *,
    mode: str = "read_only",
    allow_writes: bool = False,
    explicit_write_permission: bool = False,
) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="generic-rest-test",
        name="Generic REST",
        provider="generic_rest",
        kind="generic_rest",
        mode=mode,  # type: ignore[arg-type]
        base_url="https://workflow.example",
        credential_ref=None,
        config={
            "auth": {"method": "none"},
            "endpoints": {"export_record": "/api/campaigns"},
            "response_paths": {"record_id": ["id"], "record_url": ["url"]},
            "artifact_dir": str(tmp_path / "artifacts"),
        },
        allow_writes=allow_writes,
        explicit_write_permission=explicit_write_permission,
    )


def _benchling_config(
    *,
    mode: str = "read_only",
    allow_writes: bool = False,
    explicit_write_permission: bool = False,
) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="benchling-test",
        name="Benchling",
        provider="benchling",
        kind="eln_lims",
        mode=mode,  # type: ignore[arg-type]
        base_url="https://benchling.example",
        credential_ref=IntegrationCredentialRef(
            credential_id="cred-benchling",
            backend="env",
            key_ref="BENCHLING_API_KEY",
        ),
        config={"benchling_notebook_folder_id": "folder-1"},
        allow_writes=allow_writes,
        explicit_write_permission=explicit_write_permission,
    )
