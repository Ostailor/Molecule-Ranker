from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.server import create_app
from molecule_ranker.v2 import (
    V2_CONTRACT_VERSION,
    V2_SCHEMA_VERSION,
    V2CompatibilityMatrix,
    export_v2_release_manifest,
    validate_v2_artifact_payload,
    validate_v2_release_contracts,
)
from molecule_ranker.v2.release_contracts import (
    V2_API_ROUTES,
    V2_ARTIFACT_SCHEMAS,
    V2_CLI_COMMAND_GROUPS,
    V2_RELEASE_CONTRACTS,
)


def test_v2_release_contracts_freeze_required_surfaces() -> None:
    contract_ids = {contract.contract_id for contract in V2_RELEASE_CONTRACTS}

    assert V2_SCHEMA_VERSION == "2.7"
    assert V2_CONTRACT_VERSION == "v2.7.0"
    assert {
        "api_routes",
        "artifact_schemas",
        "cli_command_groups",
        "database_schema_version",
        "model_card_schema",
        "generated_molecule_schema",
        "evidence_item_schema",
        "review_workspace_schema",
        "campaign_schema",
        "evaluation_schema",
        "integration_sync_schema",
        "codex_task_result_schema",
        "knowledge_graph_schema",
    } <= contract_ids
    assert all(contract.schema_version == V2_SCHEMA_VERSION for contract in V2_RELEASE_CONTRACTS)
    assert all(
        contract.contract_version == V2_CONTRACT_VERSION for contract in V2_RELEASE_CONTRACTS
    )
    assert all(contract.breaking_changes_documented for contract in V2_RELEASE_CONTRACTS)


def test_v2_api_contract_exposes_v2_and_deprecates_v1() -> None:
    report = validate_v2_release_contracts()

    assert report["valid"] is True
    assert "/api/v2/version" in V2_API_ROUTES
    assert all(route.startswith("/api/v2/") for route in V2_API_ROUTES)
    assert report["compatibility"]["api.v1"]["status"] == "supported_deprecated"
    assert "deprecation" in report["compatibility"]["api.v1"]["notes"].lower()


def test_v2_artifact_payload_requires_schema_and_contract_versions() -> None:
    valid_payload = {
        "artifact_type": "generated_molecule",
        "schema_version": V2_SCHEMA_VERSION,
        "contract_version": V2_CONTRACT_VERSION,
        "generated_molecule_id": "gm-1",
        "smiles": "CCO",
        "generation_method": "synthetic-test",
        "hypothesis_only": True,
        "evidence_boundary": "not_experimental_evidence",
    }
    invalid_payload = {
        "artifact_type": "generated_molecule",
        "generated_molecule_id": "gm-1",
    }

    assert validate_v2_artifact_payload(valid_payload, "generated_molecule").valid is True
    invalid = validate_v2_artifact_payload(invalid_payload, "generated_molecule")
    assert invalid.valid is False
    assert "schema_version must be 2.7" in invalid.errors
    assert "contract_version must be v2.7.0" in invalid.errors


def test_v2_compatibility_matrix_reports_v1_migration_or_clear_failure() -> None:
    matrix = V2CompatibilityMatrix.default()

    migrated = matrix.evaluate_artifact(
        {
            "artifact_type": "generated_candidates",
            "schema_version": "1.0",
            "artifact_contract_version": "1.0",
            "success": True,
            "generation_enabled": True,
            "generated_count": 0,
        }
    )
    unsupported = matrix.evaluate_artifact(
        {
            "artifact_type": "unknown_legacy",
            "schema_version": "0.8",
            "artifact_contract_version": "0.8",
        }
    )

    assert migrated.status == "migration_available"
    assert migrated.target_contract_version == V2_CONTRACT_VERSION
    assert migrated.migration_path
    assert unsupported.status == "unsupported"
    assert "No V2 migration path" in unsupported.notes


def test_v2_manifest_export_and_cli_validation_work(tmp_path: Path) -> None:
    manifest = export_v2_release_manifest()
    output = tmp_path / "v2-contracts.json"

    assert manifest["contract_version"] == V2_CONTRACT_VERSION
    assert "api_routes" in manifest["contracts"]
    assert V2_ARTIFACT_SCHEMAS["generated_molecule"].required_fields
    assert "v2" in V2_CLI_COMMAND_GROUPS

    result = CliRunner().invoke(
        app,
        ["v2", "validate-contracts", "--json", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["contract_version"] == V2_CONTRACT_VERSION
    assert output.exists()
    assert json.loads(output.read_text())["contract_version"] == V2_CONTRACT_VERSION


def test_v2_api_contract_export_cli_writes_v2_schema(tmp_path: Path) -> None:
    output = tmp_path / "openapi-v2.json"

    result = CliRunner().invoke(
        app,
        ["v2", "export-api-contract", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["info"]["version"] == "2.7.0"
    assert "/api/v2/version" in payload["paths"]
    assert "/api/v2/projects" in payload["paths"]


def test_v2_openapi_schema_exposes_version_route(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))

    version = client.get("/api/v2/version")
    schema = client.get("/openapi.json").json()

    assert version.status_code == 200
    assert version.json()["api_contract_version"] == "api.v2"
    assert version.json()["artifact_contract_version"] == V2_CONTRACT_VERSION
    assert "/api/v2/version" in schema["paths"]
