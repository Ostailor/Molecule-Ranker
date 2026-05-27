from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from molecule_ranker.integrations.connectors.base import ExternalConnector
from molecule_ranker.integrations.schemas import ConnectorConfig, ExternalRecordRef
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.sync import SyncRequest
from molecule_ranker.integrations.worker import (
    IntegrationWorker,
    enqueue_integration_sync_job,
    recommend_safe_connector_task,
)
from molecule_ranker.platform.database import PlatformDatabase, artifact_records, platform_jobs


class FakeAssayConnector(ExternalConnector):
    connector_name = "fake-assay"
    system_type = "assay_provider"
    provider = "generic"

    def import_assay_results(self):
        return [
            {
                "external_ref": ExternalRecordRef(
                    external_system_id=self.config.connector_id,
                    external_record_type="assay_result",
                    external_record_id="assay-result-1",
                    metadata={"source": "mock"},
                ),
                "payload": {
                    "candidate_id": "cand-1",
                    "source_record_id": "assay-result-1",
                    "outcome": "active",
                    "value": 42.0,
                    "unit": "nM",
                },
            }
        ]


class FailingAssayConnector(ExternalConnector):
    connector_name = "failing-assay"

    def import_assay_results(self):
        raise RuntimeError("mock connector failure")


def test_enqueue_integration_sync_job(tmp_path: Path) -> None:
    database, user = _database_with_integration_user(tmp_path)
    connector = _connector()

    job = enqueue_integration_sync_job(
        database=database,
        connector=connector,
        request=SyncRequest(org_id="default", project_id="project-1"),
        requested_by=user,
    )

    assert job.job_type == "integration_sync"
    assert job.status == "queued"
    assert job.config_snapshot["connector_id"] == connector.connector_id
    assert job.config_snapshot["sync_request"]["project_id"] == "project-1"


def test_integration_worker_processes_mocked_connector(tmp_path: Path) -> None:
    database, user = _database_with_integration_user(tmp_path)
    connector = _connector()
    job = enqueue_integration_sync_job(
        database=database,
        connector=connector,
        request=SyncRequest(org_id="default", project_id="project-1"),
        requested_by=user,
    )

    finished = IntegrationWorker(
        database=database,
        connector_factory=lambda _config: FakeAssayConnector(connector),
    ).run_next()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "succeeded"
    sync_jobs = IntegrationStore(database, user=user, project_id="project-1").list_sync_jobs()
    assert len(sync_jobs) == 1
    assert sync_jobs[0].status == "succeeded"
    assert sync_jobs[0].records_seen == 1
    assert sync_jobs[0].records_imported == 1


def test_integration_worker_registers_sync_artifacts_on_platform_job(tmp_path: Path) -> None:
    database, user = _database_with_integration_user(tmp_path)
    connector = _connector()
    enqueue_integration_sync_job(
        database=database,
        connector=connector,
        request=SyncRequest(org_id="default", project_id="project-1"),
        requested_by=user,
    )

    finished = IntegrationWorker(
        database=database,
        connector_factory=lambda _config: FakeAssayConnector(connector),
    ).run_next()

    assert finished is not None
    assert finished.result_artifact_ids
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records)).mappings().fetchall()
    assert {row["artifact_id"] for row in rows} == set(finished.result_artifact_ids)
    assert rows[0]["artifact_type"] == "integration_raw_payload"


def test_integration_worker_surfaces_sync_errors_in_job_details(tmp_path: Path) -> None:
    database, user = _database_with_integration_user(tmp_path)
    connector = _connector()
    enqueue_integration_sync_job(
        database=database,
        connector=connector,
        request=SyncRequest(org_id="default", project_id="project-1"),
        requested_by=user,
    )

    finished = IntegrationWorker(
        database=database,
        connector_factory=lambda _config: FailingAssayConnector(connector),
    ).run_next()

    assert finished is not None
    assert finished.status == "failed"
    assert "mock connector failure" in (finished.error_summary or "")


def test_unauthorized_user_cannot_enqueue_integration_sync(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=viewer.user_id,
        user_id=viewer.user_id,
    )

    try:
        enqueue_integration_sync_job(
            database=database,
            connector=_connector(),
            request=SyncRequest(org_id="default", project_id="project-1"),
            requested_by=viewer,
        )
    except PermissionError as exc:
        assert "integration:sync" in str(exc)
    else:
        raise AssertionError("Expected integration sync enqueue to be blocked.")


def test_codex_recommendation_does_not_run_sync_automatically(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")

    recommendation = recommend_safe_connector_task(
        connector_id="connector-1",
        task_type="integration_sync",
        reason="Review imported assay payloads before running a sync.",
        object_types=["assay_results"],
    )

    assert recommendation["status"] == "recommendation_only"
    assert recommendation["connector_execution"] == "not_run"
    with database.engine.connect() as connection:
        queued_jobs = connection.execute(
            select(func.count()).select_from(platform_jobs)
        ).scalar_one()
    assert queued_jobs == 0


def _database_with_integration_user(tmp_path: Path):
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="scientist@example.com", password="Scientist-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    return database, user


def _connector() -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="connector-1",
        name="Mock assay connector",
        provider="generic_rest",
        kind="assay_result_provider",
        mode="dry_run",
        direction="import",
        sandbox=True,
    )
