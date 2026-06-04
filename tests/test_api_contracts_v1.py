from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.contracts.api_contracts import (
    API_CONTRACT_VERSION,
    API_CONTRACTS,
    list_api_contracts,
)
from molecule_ranker.platform.database import project_workspaces
from molecule_ranker.server import create_app


def test_v1_api_contracts_cover_stabilized_routes() -> None:
    contracts = list_api_contracts()
    contract_routes = {contract.route for contract in contracts}

    assert API_CONTRACT_VERSION == "api.v1"
    assert {
        "/api/v1/auth/login",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/artifacts",
        "/api/v1/review/health",
        "/api/v1/experiments/health",
        "/api/v1/active-learning/health",
        "/api/v1/integrations/catalog",
        "/api/v1/jobs/{job_id}",
        "/api/v1/projects/{project_id}/codex/summarize",
        "/api/v1/admin/health",
        "/api/v1/admin/audit",
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/version",
    } <= contract_routes
    assert isinstance(API_CONTRACTS, dict)

    for contract in contracts:
        payload = contract.as_dict()
        assert contract.route.startswith("/api/v1/")
        assert contract.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert contract.version == "v1"
        assert contract.stability_level in {"stable", "beta", "internal"}
        assert isinstance(contract.auth_required, bool)
        assert "permission_required" in payload
        assert payload["request_schema"] is not None
        assert payload["response_schema"] is not None
        assert payload["error_schema"] is not None


def test_v1_aliases_expose_health_project_and_auth_routes(tmp_path: Path) -> None:
    client = TestClient(_hosted_app(tmp_path))

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 200
    assert client.get("/api/v1/version").json()["api_contract_version"] == API_CONTRACT_VERSION
    assert client.get("/api/v1/projects").status_code == 401

    token_payload = _login_v1(client)
    headers = {"Authorization": f"Bearer {token_payload['access_token']}"}
    created = client.post(
        "/api/v1/projects",
        json={"workspace_id": "workspace-v1", "name": "V1 workspace"},
        headers=headers,
    )

    assert created.status_code == 200
    assert created.json()["workspace_id"] == "workspace-v1"
    app_state = cast(Any, client.app).state
    with app_state.platform_database.engine.connect() as connection:
        registered = (
            connection.execute(
                select(project_workspaces).where(
                    project_workspaces.c.project_id == "workspace-v1"
                )
            )
            .mappings()
            .first()
        )
    assert registered is not None
    assert registered["name"] == "V1 workspace"
    projects = client.get("/api/v1/projects", headers=headers)
    assert projects.status_code == 200
    assert projects.json()["projects"][0]["workspace_id"] == "workspace-v1"


def test_v1_openapi_schema_includes_tags_and_response_models(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))

    schema = client.get("/openapi.json").json()

    assert {
        "/api/v1/health",
        "/api/v1/version",
        "/api/v1/projects",
        "/api/v1/auth/login",
        "/api/v1/active-learning/health",
    } <= set(schema["paths"])
    tag_names = {tag["name"] for tag in schema["tags"]}
    assert {"v1-health", "auth", "projects", "v1-active-learning"} <= tag_names

    health_response = schema["paths"]["/api/v1/health"]["get"]["responses"]["200"]
    version_response = schema["paths"]["/api/v1/version"]["get"]["responses"]["200"]
    assert "$ref" in health_response["content"]["application/json"]["schema"]
    assert "$ref" in version_response["content"]["application/json"]["schema"]


def test_api_export_openapi_cli_writes_v1_schema(tmp_path: Path) -> None:
    output = tmp_path / "openapi-v1.json"

    result = CliRunner().invoke(app, ["api", "export-openapi", "--output", str(output)])

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["info"]["version"] == "2.3.0"
    assert "/api/v1/health" in payload["paths"]
    assert "/api/v1/projects" in payload["paths"]


def _hosted_app(tmp_path: Path) -> Any:
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-32-characters",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _login_v1(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert response.status_code == 200
    return response.json()
