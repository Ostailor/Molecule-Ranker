from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from molecule_ranker import __version__
from molecule_ranker.integrations.exporters import create_export_package
from molecule_ranker.integrations.warehouse_models import WAREHOUSE_SCHEMA_VERSION
from molecule_ranker.release import (
    API_CONTRACT_VERSION,
    ARTIFACT_CONTRACT_VERSION,
    DATA_CONTRACT_VERSION,
    WAREHOUSE_CONTRACT_VERSION,
    evaluate_release_readiness,
    release_manifest,
)
from molecule_ranker.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_v14_version_and_contracts_are_declared() -> None:
    manifest = release_manifest()

    assert __version__ == "3.0.0"
    assert manifest["version"] == "3.0.0"
    assert manifest["contracts"] == {
        "api": "api.v1",
        "artifacts": "artifacts.v1",
        "data_contracts": "data-contracts.v1",
        "warehouse": "mr_warehouse_v1.0.0",
    }
    assert WAREHOUSE_SCHEMA_VERSION == WAREHOUSE_CONTRACT_VERSION


def test_v2_release_manifest_covers_all_required_gates() -> None:
    categories = {gate["category"] for gate in release_manifest()["release_gates"]}

    assert categories == {
        "admin_controls",
        "backup_restore",
        "contract",
        "demo",
        "deployment",
        "governance",
        "identity_access",
        "packaging",
        "reliability",
        "runbook",
        "sdk",
        "security",
        "tenant_isolation",
        "training",
        "validation",
    }


def test_v2_release_readiness_evidence_files_exist() -> None:
    report = evaluate_release_readiness(ROOT)

    assert report["ready"] is True
    assert all(gate["status"] == "pass" for gate in report["gates"])


def test_version_endpoint_reports_v1_contracts(tmp_path: Path) -> None:
    client = TestClient(create_app(root_dir=tmp_path))

    payload = client.get("/version").json()

    assert payload["version"] == "3.0.0"
    assert payload["api_contract_version"] == API_CONTRACT_VERSION
    assert payload["artifact_contract_version"] == ARTIFACT_CONTRACT_VERSION
    assert payload["data_contract_version"] == DATA_CONTRACT_VERSION
    assert payload["warehouse_contract_version"] == WAREHOUSE_CONTRACT_VERSION


def test_export_packages_default_to_v1_data_contract(tmp_path: Path) -> None:
    result = create_export_package(
        "candidate_summary_package",
        {"candidates": [], "generated_molecule_hypotheses": []},
        tmp_path,
        formats=["json"],
    )

    assert result.data_contract_version == DATA_CONTRACT_VERSION


def test_synthetic_demo_artifacts_are_labeled_non_evidence() -> None:
    manifest = json.loads((ROOT / "examples/v1.0-demo/demo_project_manifest.json").read_text())
    summary = json.loads((ROOT / "examples/v1.0-demo/synthetic_run_summary.json").read_text())

    assert manifest["artifact_contract_version"] == ARTIFACT_CONTRACT_VERSION
    assert "not biomedical evidence" in manifest["disclaimer"]
    assert manifest["scientific_integrity"] == {
        "contains_assay_results": False,
        "contains_citations": False,
        "contains_generated_validated_actives": False,
        "contains_medical_advice": False,
        "contains_synthesis_instructions": False,
    }
    assert summary["assay_results"] == []
    assert summary["literature_claims"] == []
    assert summary["generated_molecule_hypotheses"] == []
