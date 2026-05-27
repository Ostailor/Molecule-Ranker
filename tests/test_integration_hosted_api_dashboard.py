from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker.integrations.schemas import (
    EntityMapping,
    ExternalRecordRef,
    ExternalSystem,
    IntegrationCredentialCreate,
    SyncJob,
)
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.integrations.webhooks import sign_payload
from molecule_ranker.server import create_app


def test_integration_dashboard_pages_require_auth(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    for path in [
        "/dashboard/integrations",
        "/dashboard/integrations/credentials",
        "/dashboard/integrations/sync-jobs",
        "/dashboard/integrations/mappings",
        "/dashboard/integrations/webhooks",
        "/dashboard/integrations/data-contracts",
    ]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_external_system_api_crud_health_and_sync_enqueue(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    headers = _api_login(client, "admin@example.com", "Admin-password-1")
    system = ExternalSystem(
        external_system_id="system-1",
        name="Generic REST",
        system_type="generic_rest",
        vendor="generic",
        base_url="https://example.invalid",
    )

    created = client.post(
        "/integrations/systems",
        headers=headers,
        json={"system": system.model_dump(mode="json")},
    )
    listed = client.get("/integrations/systems", headers=headers)
    detail = client.get("/integrations/systems/system-1", headers=headers)
    patched = client.patch(
        "/integrations/systems/system-1",
        headers=headers,
        json={"name": "Updated REST"},
    )
    health = client.post("/integrations/systems/system-1/health", headers=headers)
    sync = client.post("/integrations/systems/system-1/sync", headers=headers, json={})

    assert created.status_code == 200, created.text
    assert listed.status_code == 200, listed.text
    assert listed.json()["systems"][0]["external_system_id"] == "system-1"
    assert detail.json()["system"]["name"] == "Generic REST"
    assert patched.json()["system"]["name"] == "Updated REST"
    assert health.status_code == 200, health.text
    assert health.json()["health"]["status"] in {"ok", "unconfigured", "degraded"}
    assert sync.status_code == 200, sync.text
    assert sync.json()["job"]["job_type"] == "integration_sync"


def test_credentials_dashboard_redacts_secret_values(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    _web_login(client)
    app.state.platform_database.create_integration_credential(
        IntegrationCredentialCreate(name="Benchling", secret_value="super-secret-token"),
        actor_user_id=None,
    )

    response = client.get("/dashboard/integrations/credentials")

    assert response.status_code == 200, response.text
    assert "Integration credentials" in response.text
    assert "super-secret-token" not in response.text
    assert "external_secret_manager:..." in response.text


def test_sync_job_dashboard_page_works(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    _web_login(client)
    store = IntegrationStore(app.state.platform_database)
    store.create_sync_job(
        SyncJob(
            sync_job_id="sync-dashboard-1",
            external_system_id="ext-1",
            direction="import",
            object_types=["assay_results"],
            mode="dry_run",
            status="succeeded",
            records_seen=3,
        )
    )

    response = client.get("/dashboard/integrations/sync-jobs/sync-dashboard-1")

    assert response.status_code == 200, response.text
    assert "sync-dashboard-1" in response.text
    assert "Records seen" in response.text
    assert "3" in response.text


def test_mapping_approval_requires_permission(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    database = app.state.platform_database
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=viewer.user_id,
        user_id=viewer.user_id,
    )
    IntegrationStore(database).create_mapping(
        EntityMapping(
            mapping_id="mapping-1",
            project_id="project-1",
            internal_entity_type="candidate",
            internal_entity_id="cand-1",
            external_ref=ExternalRecordRef(
                external_system_id="ext-1",
                external_record_type="registry_entry",
                external_record_id="EXT-1",
            ),
            mapping_method="inchi_key",
            mapping_confidence=0.99,
            status="pending_review",
        )
    )
    headers = _api_login(client, "viewer@example.com", "Viewer-password-1")

    response = client.post("/integrations/mappings/mapping-1/approve", headers=headers)

    assert response.status_code == 403


def test_signed_webhook_endpoint_requires_no_login_but_requires_signature(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "webhook-secret-value")
    app = _app(tmp_path)
    client = TestClient(app)
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/integrations/connectors",
        headers=admin_headers,
        json={
            "connector": {
                "connector_id": "webhook-ext",
                "name": "Webhook source",
                "provider": "generic_rest",
                "kind": "webhook",
                "config": {"webhook_signature_env_var": "WEBHOOK_SIGNING_SECRET"},
            }
        },
    )
    assert created.status_code == 200, created.text
    payload = json.dumps({"id": "evt-1", "external_record_id": "record-1"}).encode()

    missing_signature = client.post("/webhooks/webhook-ext", content=payload)
    accepted = client.post(
        "/webhooks/webhook-ext",
        content=payload,
        headers={
            "x-molecule-ranker-signature": sign_payload(payload, "webhook-secret-value"),
            "x-webhook-event-id": "evt-1",
        },
    )

    assert missing_signature.status_code == 400
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True


def _app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _web_login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "Admin-password-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
