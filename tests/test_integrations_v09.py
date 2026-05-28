from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select

from molecule_ranker import __version__
from molecule_ranker.integrations import (
    ConnectorConfig,
    DataContract,
    ExternalIdMapping,
    ExternalRecordEnvelope,
    ExternalRecordProvenance,
    IntegrationCredentialCreate,
    MappingSuggestionRequest,
    validate_data_contract,
    validate_mapping_suggestions,
)
from molecule_ranker.platform.database import (
    PlatformDatabase,
    integration_credentials,
    integration_provenance_records,
)
from molecule_ranker.server import create_app


def test_version_is_v10() -> None:
    assert __version__ == "1.1.0"


def test_connector_defaults_are_dry_run_and_block_implicit_writes() -> None:
    connector = ConnectorConfig(
        name="Benchling",
        provider="benchling",
        kind="eln_lims",
    )

    assert connector.mode == "dry_run"
    assert connector.allow_writes is False

    with pytest.raises(ValidationError):
        ConnectorConfig(
            name="Unsafe export",
            provider="postgresql",
            kind="data_warehouse",
            direction="export",
            mode="dry_run",
        )

    with pytest.raises(ValidationError):
        ConnectorConfig(
            name="Secret in config",
            provider="generic_rest",
            kind="generic_rest",
            config={"api_key": "plaintext"},
        )


def test_platform_database_initializes_integration_tables_and_hashes_credentials(
    tmp_path: Path,
) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    credential = database.create_integration_credential(
        request=IntegrationCredentialCreate(
            name="Benchling token",
            secret_value="benchling-secret-value",
        )
    )

    table_names = set(inspect(database.engine).get_table_names())
    with database.engine.connect() as connection:
        row = connection.execute(select(integration_credentials)).mappings().one()

    assert "integration_connectors" in table_names
    assert "integration_sync_jobs" in table_names
    assert credential.backend == "platform_hash"
    combined = json.dumps(dict(row), default=str)
    assert "benchling-secret-value" not in combined
    assert row["secret_hash"]
    assert row["secret_salt"]


def test_data_contract_and_mapping_suggestions_are_deterministically_validated() -> None:
    record = ExternalRecordEnvelope(
        record_type="compound",
        payload={"external_id": "CMP-1", "compound_name": "Rasagiline"},
        provenance=ExternalRecordProvenance(
            source_system="benchling",
            source_record_id="rec-1",
            sync_job_id="sync-1",
            raw_metadata={"entity": "compound"},
        ),
    )
    contract = DataContract(
        contract_id="contract-compound-v1",
        name="compound",
        object_type="compound",
        version="1",
        required_fields=["compound_name"],
        field_types={"compound_name": "string"},
    )
    report = validate_data_contract([record], contract)
    suggestion = ExternalIdMapping(
        connector_id="int-1",
        internal_id="candidate-1",
        external_id="CMP-1",
        source_system="benchling",
        source_record_id="rec-1",
        mapping_method="codex_suggested",
        confidence=0.7,
    )

    mapping_report = validate_mapping_suggestions(
        MappingSuggestionRequest(
            connector_id="int-1",
            source_system="benchling",
            suggestions=[suggestion],
            observed_records=[record],
        )
    )

    assert report.valid is True
    assert mapping_report.accepted[0].status == "confirmed"
    assert mapping_report.accepted[0].validation_evidence["deterministic_match"] is True


def test_integration_api_keeps_secrets_out_and_preserves_webhook_provenance(
    tmp_path: Path,
) -> None:
    app = create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret=_secret(),
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )
    client = TestClient(app)
    headers = _login(client, "admin@example.com", "Admin-password-1")
    credential = client.post(
        "/integrations/credentials",
        headers=headers,
        json={"name": "Benchling", "secret_value": "secret-benchling-token"},
    )
    assert credential.status_code == 200, credential.text
    assert "secret-benchling-token" not in credential.text

    connector = client.post(
        "/integrations/connectors",
        headers=headers,
        json={
            "connector": {
                "connector_id": "int-benchling",
                "name": "Benchling",
                "provider": "benchling",
                "kind": "eln_lims",
                "credential_ref": credential.json()["credential"],
            }
        },
    )
    assert connector.status_code == 200, connector.text
    assert connector.json()["connector"]["mode"] == "dry_run"
    assert "credential" not in connector.text.lower()

    ingest = client.post(
        "/integrations/connectors/int-benchling/webhooks/ingest",
        headers=headers,
        json={
            "source_system": "benchling",
            "event_type": "compound.updated",
            "source_record_id": "bnch-rec-1",
            "payload": {"compound_name": "Rasagiline"},
            "raw_metadata": {"webhook_id": "wh-1"},
        },
    )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["sync_job"]["status"] == "dry_run"

    platform_database = app.state.platform_database
    with platform_database.engine.connect() as connection:
        row = connection.execute(select(integration_provenance_records)).mappings().one()
    assert row["source_system"] == "benchling"
    assert row["source_record_id"] == "bnch-rec-1"
    assert row["sync_job_id"] == ingest.json()["sync_job"]["sync_job_id"]


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _secret() -> str:
    return "test-hosted-secret-value-with-at-least-32-chars"
