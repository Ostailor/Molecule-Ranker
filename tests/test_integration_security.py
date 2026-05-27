from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.integrations.codex_assistant import CodexIntegrationAssistant
from molecule_ranker.integrations.connectors import GenericFileConnector, GenericRESTConnector
from molecule_ranker.integrations.connectors.base import ConnectorError
from molecule_ranker.integrations.connectors.warehouse import GenericWarehouseConnector
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.exporters import create_export_package
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    ExternalRecordRef,
    IntegrationCredential,
    IntegrationCredentialRef,
    SyncJob,
    SyncRecord,
)
from molecule_ranker.integrations.store import (
    IntegrationStore,
    integration_credentials,
    sync_records,
)
from molecule_ranker.integrations.warehouse_models import normalize_export_rows
from molecule_ranker.integrations.webhooks import (
    WebhookError,
    WebhookIngestionConfig,
    WebhookIngestionService,
    sign_payload,
)
from molecule_ranker.platform.database import PlatformDatabase, artifact_records
from molecule_ranker.platform.schemas import UserAccount


class FakeCodexProvider:
    def __init__(self, output_json: dict[str, Any]) -> None:
        self.output_json = output_json
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=json.dumps(self.output_json),
            output_json=self.output_json,
            artifacts_read=task.input_artifact_paths,
        )


def test_sql_injection_attempt_rejected() -> None:
    connector = GenericWarehouseConnector(_warehouse_config(), query_executor=lambda _q, _p: [])

    with pytest.raises(ConnectorError, match="single statement|Unsafe SQL"):
        connector.run_query_readonly(
            "select * from assay_results where project_id = :project_id; drop table users",
            {"project_id": "project-1"},
        )


def test_unsafe_sql_without_allowlist_rejected() -> None:
    connector = GenericWarehouseConnector(
        _warehouse_config(
            {
                "hosted_mode": True,
                "query_allowlist": {
                    "safe_assay_results": (
                        "select * from assay_results where project_id = :project_id"
                    )
                },
            }
        ),
        query_executor=lambda _q, _p: [],
    )

    with pytest.raises(ConnectorError, match="allowlist"):
        connector.run_query_readonly(
            "select * from assay_results where project_id = :project_id",
            {"project_id": "project-1"},
        )
    with pytest.raises(ConnectorError, match="allowlist"):
        connector.run_query_readonly(
            "select * from assay_results where org_id = :org_id",
            {"org_id": "org-1"},
            query_name="safe_assay_results",
        )


def test_unsafe_file_path_rejected(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    connector = GenericFileConnector(_file_config(tmp_path))

    with pytest.raises(ConnectorError, match="Path traversal"):
        connector.import_file("../outside.csv")
    with pytest.raises(ConnectorError, match="Path traversal"):
        GenericFileConnector(_file_config(tmp_path, inbox_dir="../outside")).scan_inbox()


def test_fake_webhook_rejected_and_replay_protected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_SECURITY_SECRET", "webhook-secret-value")
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    service = WebhookIngestionService(database)
    payload = _webhook_payload()
    config = WebhookIngestionConfig(
        external_system_id="ext-webhook",
        secret_env_var="WEBHOOK_SECURITY_SECRET",
        org_id="org-1",
        project_id="project-1",
    )

    with pytest.raises(WebhookError, match="signature"):
        service.ingest(
            raw_payload=payload,
            headers={"x-molecule-ranker-signature": "sha256=bad"},
            config=config,
        )

    headers = {
        "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
        "x-webhook-event-id": "evt-security-1",
    }
    service.ingest(raw_payload=payload, headers=headers, config=config)
    with pytest.raises(WebhookError, match="replay"):
        service.ingest(raw_payload=payload, headers=headers, config=config)


def test_unauthorized_sync_rejected(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    viewer = UserAccount(user_id="user-viewer", email="viewer@example.test", is_admin=False)
    store = IntegrationStore(database, user=viewer, org_id="org-1", project_id="project-1")

    with pytest.raises(PermissionError, match="integration:sync"):
        store.create_sync_job(
            SyncJob(
                sync_job_id="sync-unauthorized",
                external_system_id="ext-rest",
                project_id="project-1",
                direction="import",
                object_types=["assay_results"],
                mode="dry_run",
                status="queued",
            ),
            org_id="org-1",
        )


def test_secret_redaction_and_no_plaintext_credentials_in_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHLING_SECURITY_TOKEN", "benchling-secret-value")
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    store = IntegrationStore(database, org_id="org-1", project_id="project-1")

    store.create_credential_reference(
        IntegrationCredential(
            credential_id="cred-security",
            external_system_id="ext-benchling",
            credential_type="api_key",
            secret_ref="env:BENCHLING_SECURITY_TOKEN",
            metadata={"note": "token=benchling-secret-value"},
        ),
        org_id="org-1",
        project_id="project-1",
    )

    with database.engine.connect() as connection:
        row = connection.execute(select(integration_credentials)).mappings().one()
    serialized = json.dumps(dict(row), default=str, sort_keys=True)
    assert "benchling-secret-value" not in serialized
    assert "[REDACTED]" in serialized
    assert redact_secret_values("Authorization: benchling-secret-value") == (
        "Authorization=[REDACTED]"
    )


def test_no_credentials_in_codex_prompts_or_context(tmp_path: Path) -> None:
    provider = FakeCodexProvider(
        {
            "record_summary": "Uses record REG-1 only.",
            "artifact_refs": ["artifact-1"],
            "external_record_refs": ["benchling:entity:REG-1"],
        }
    )
    assistant = CodexIntegrationAssistant(provider, working_directory=tmp_path)

    artifact = assistant.summarize_external_record(
        external_ref=ExternalRecordRef(
            external_system_id="benchling",
            external_record_type="entity",
            external_record_id="REG-1",
        ),
        payload={"artifact_id": "artifact-1", "api_key": "benchling-secret-value"},
    )

    assert artifact.status == "succeeded"
    task = provider.tasks[0]
    assert "benchling-secret-value" not in task.prompt
    assert task.input_artifact_paths
    context_text = Path(task.input_artifact_paths[0]).read_text()
    assert "benchling-secret-value" not in context_text
    assert "[REDACTED]" in context_text


def test_cache_file_export_excluded() -> None:
    rows = normalize_export_rows(
        "mr_artifacts",
        [
            {
                "org_id": "org-1",
                "artifact_id": "artifact-cache",
                "artifact_type": "cache",
                "path": "/tmp/.cache/payload.json",
            },
            {
                "org_id": "org-1",
                "artifact_id": "artifact-safe",
                "artifact_type": "report",
                "path": "/tmp/report.md",
            },
        ],
    )

    assert [row["artifact_id"] for row in rows] == ["artifact-safe"]


def test_write_operations_require_write_enabled_and_permission(tmp_path: Path) -> None:
    read_only = GenericRESTConnector(_rest_config(tmp_path), http_client=FakeRESTClient({}))
    with pytest.raises(ConnectorError, match="blocked by default"):
        read_only.export_record({"id": "res-export", "candidate_id": "cand-1"})

    config = _rest_config(tmp_path).model_copy(
        update={"mode": "write_enabled", "allow_writes": True, "explicit_write_permission": False}
    )
    missing_permission = GenericRESTConnector(config, http_client=FakeRESTClient({}))
    with pytest.raises(ConnectorError, match="blocked by default"):
        missing_permission.export_record({"id": "res-export", "candidate_id": "cand-1"})


def test_codex_cannot_write_or_activate_mappings(tmp_path: Path) -> None:
    provider = FakeCodexProvider(
        {
            "suggested_mappings": [
                {
                    "internal_field": "candidate_id",
                    "external_field": "registry_id",
                    "status": "active",
                }
            ],
            "external_write": True,
            "artifact_refs": ["artifact-1"],
            "external_record_refs": ["benchling:entity:REG-1"],
        }
    )
    assistant = CodexIntegrationAssistant(provider, working_directory=tmp_path)

    artifact = assistant.suggest_schema_mapping(
        external_records=[
            {
                "artifact_id": "artifact-1",
                "external_ref": {
                    "external_system_id": "benchling",
                    "external_record_type": "entity",
                    "external_record_id": "REG-1",
                },
                "registry_id": "REG-1",
                "candidate_id": "cand-1",
            }
        ]
    )

    assert artifact.status == "guardrail_failed"
    assert artifact.output_json is not None
    suggestion = artifact.output_json["suggested_mappings"][0]
    assert suggestion["status"] == "pending_review"
    assert suggestion["mapping_method"] == "codex_suggested_pending_validation"
    assert any(
        "write" in warning.lower() and "external" in warning.lower()
        for warning in artifact.guardrail_warnings
    )


def test_external_raw_payload_stored_as_hashed_artifact_with_access_control(
    tmp_path: Path,
) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    store = IntegrationStore(database, org_id="org-1", project_id="project-1")
    store.create_sync_job(
        SyncJob(
            sync_job_id="sync-raw",
            external_system_id="ext-assay",
            project_id="project-1",
            direction="import",
            object_types=["assay_results"],
            mode="dry_run",
            status="queued",
        ),
        org_id="org-1",
    )
    record = store.add_sync_record(
        SyncRecord(
            sync_record_id="sync-record-raw",
            sync_job_id="sync-raw",
            external_ref=ExternalRecordRef(
                external_system_id="ext-assay",
                external_record_type="assay_result",
                external_record_id="AR-1",
            ),
            action="imported",
            status="succeeded",
        ),
        raw_payload={"source_record_id": "AR-1", "token": "payload-secret-value"},
    )

    with database.engine.connect() as connection:
        sync_row = connection.execute(
            select(sync_records).where(sync_records.c.sync_record_id == "sync-record-raw")
        ).mappings().one()
        artifact_row = connection.execute(
            select(artifact_records).where(
                artifact_records.c.artifact_id == record.raw_payload_artifact_id
            )
        ).mappings().one()
    assert "payload-secret-value" not in json.dumps(dict(sync_row), default=str)
    assert artifact_row["sha256"] == record.metadata["raw_payload_artifact"]["sha256"]
    assert artifact_row["project_id"] == "project-1"

    viewer = UserAccount(user_id="user-viewer", email="viewer@example.test", is_admin=False)
    protected_store = IntegrationStore(
        database,
        user=viewer,
        org_id="org-1",
        project_id="project-1",
    )
    with pytest.raises(PermissionError, match="integration:read"):
        protected_store.list_sync_records(sync_job_id="sync-raw")


def test_no_lab_protocols_synthesis_or_dosing_in_export_packages(tmp_path: Path) -> None:
    result = create_export_package(
        "validation_handoff_package",
        {
            "handoff_id": "handoff-1",
            "summary": (
                "Contains protocol, synthesis route, reagents, reaction conditions, "
                "and dosing 10 mg/kg."
            ),
            "lab_protocol": "step-by-step protocol",
            "synthesis_instruction": "make compound",
            "dosing": "10 mg/kg",
        },
        tmp_path / "handoff-package",
    )

    exported = "\n".join(
        path.read_text(errors="replace")
        for path in Path(result.output_dir).glob("*")
        if path.is_file()
    ).lower()
    assert "protocol" not in exported
    assert "synthesis" not in exported
    assert "dosing" not in exported
    assert "mg/kg" not in exported


class FakeRESTClient:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes

    def request(self, method: str, url: str, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(self.routes.get((method, url), {"id": "res-export"}))


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ConnectorError(f"HTTP {self.status_code}")


def _warehouse_config(extra: dict[str, Any] | None = None) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="warehouse-security",
        name="Warehouse",
        provider="postgresql",
        kind="data_warehouse",
        mode="read_only",
        config={"connection_url": "sqlite:///:memory:", **(extra or {})},
    )


def _file_config(tmp_path: Path, *, inbox_dir: str = "inbox") -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="file-security",
        name="File connector",
        provider="generic_csv_sftp",
        kind="csv_sftp",
        mode="dry_run",
        config={
            "root_dir": str(tmp_path),
            "inbox_dir": inbox_dir,
            "processed_dir": "processed",
            "failed_dir": "failed",
            "outbox_dir": "outbox",
        },
    )


def _rest_config(tmp_path: Path) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="rest-security",
        name="REST",
        provider="generic_rest",
        kind="generic_rest",
        mode="read_only",
        base_url="https://lims.example",
        credential_ref=IntegrationCredentialRef(
            credential_id="cred-rest",
            backend="env",
            key_ref="REST_SECURITY_TOKEN",
        ),
        config={
            "endpoints": {"export_record": "/api/results"},
            "artifact_dir": str(tmp_path / "artifacts"),
        },
    )


def _webhook_payload() -> bytes:
    return json.dumps(
        {
            "id": "evt-security-1",
            "event_type": "assay_result.created",
            "external_record_id": "AR-1",
        },
        sort_keys=True,
    ).encode()
