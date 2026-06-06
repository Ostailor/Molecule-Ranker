from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from molecule_ranker import __version__
from molecule_ranker.cli import app
from molecule_ranker.integrations.operations import (
    EndToEndWorkflowRequest,
    EndToEndWorkflowRunner,
    ExternalLineageTracker,
    ExternalSyncPlanner,
    IntegrationOpsAgent,
    IntegrationRepairAgent,
    WorkflowStateMachine,
)
from molecule_ranker.integrations.schemas import DataContract
from molecule_ranker.server import create_app
from molecule_ranker.v2 import V2_API_ROUTES, V2_ARTIFACT_SCHEMAS


def test_v2_7_version_and_contract_entries() -> None:
    assert __version__ == "2.7.0"
    assert "/api/v2/integrations/operations/dashboard" in V2_API_ROUTES
    assert "end_to_end_result_bundle" in V2_ARTIFACT_SCHEMAS


def test_end_to_end_runner_produces_safe_auditable_bundle(tmp_path: Path) -> None:
    request = EndToEndWorkflowRequest(
        objective="Prioritize a governed research project for inflammatory disease.",
        project_id="project-27",
        mode="mocked",
    )

    bundle = EndToEndWorkflowRunner().run(request, output_dir=tmp_path)

    assert bundle.version == "2.7.0"
    assert bundle.status == "succeeded"
    assert bundle.mode == "mocked"
    assert bundle.workflow_state["current_stage"] == "completed"
    assert bundle.safety_constraints["no_codex_scientific_truth"] is True
    assert bundle.safety_constraints["external_writes_require_approval"] is True
    assert all(not artifact.metadata.get("scientific_truth") for artifact in bundle.artifacts)
    assert all(link.deterministic_validation for link in bundle.lineage_links)
    assert (tmp_path / "end_to_end_result_bundle.json").exists()
    assert (tmp_path / "end_to_end_result_bundle.md").exists()


def test_external_sync_planner_blocks_unapproved_live_writes() -> None:
    planner = ExternalSyncPlanner()
    request = EndToEndWorkflowRequest(
        objective="Sync project status outward.",
        mode="live",
        requested_external_write=True,
    )

    try:
        planner.plan(request)
    except PermissionError as exc:
        assert "explicit approval" in str(exc)
    else:
        raise AssertionError("live external write should require approval")

    approved = planner.plan(
        request.model_copy(
            update={
                "write_approval_id": "approval-1",
                "governance_permissions": ["integration:write"],
            }
        )
    )
    assert approved.sync_mode == "write_approved"
    assert approved.requires_human_approval is True


def test_integration_ops_validation_blocks_invalid_external_records() -> None:
    contract = DataContract(
        contract_id="assay-contract",
        name="Assay result contract",
        object_type="assay_results",
        version="1.0",
        required_fields=["result_id", "source_record_id"],
        field_types={"result_id": "string", "source_record_id": "string"},
        identifier_fields=["source_record_id"],
    )
    request = EndToEndWorkflowRequest(
        objective="Import external assay records safely.",
        mode="dry_run",
        external_records=[{"source_record_id": "EXT-1"}],
        data_contracts={"assay_results": contract},
    )

    result = IntegrationOpsAgent().operate(request)

    assert result.status == "validation_failed"
    assert result.records_failed == 1
    assert result.external_write_performed is False
    assert result.validation_reports[0]["valid"] is False


def test_integration_repair_agent_repairs_workflow_not_scientific_truth() -> None:
    result = IntegrationOpsAgent().operate(
        EndToEndWorkflowRequest(
            objective="Import malformed external records.",
            mode="dry_run",
            external_records=[{"source_record_id": "EXT-1"}],
            data_contracts={
                "assay_results": DataContract(
                    contract_id="contract-1",
                    name="Assay contract",
                    object_type="assay_results",
                    version="1",
                    required_fields=["result_id"],
                )
            },
        )
    )

    repair = IntegrationRepairAgent().diagnose_and_plan(result)

    assert repair.status == "repair_planned"
    assert repair.blocked_scientific_repair is False
    assert repair.actions[0]["action"] == "repair_external_contract_mapping"


def test_lineage_tracker_rejects_unvalidated_external_truth() -> None:
    tracker = ExternalLineageTracker()

    try:
        tracker.link_external_record(
            artifact_id="artifact-1",
            external_system_id="ext-1",
            external_record_id="EXT-1",
            deterministic_validation=False,
        )
    except ValueError as exc:
        assert "deterministic validation" in str(exc)
    else:
        raise AssertionError("unvalidated external lineage should be rejected")


def test_workflow_state_machine_resumes_from_snapshot() -> None:
    machine = WorkflowStateMachine()
    machine.start("wf-1")
    machine.advance("ranking")
    snapshot = machine.snapshot()

    resumed = WorkflowStateMachine.from_snapshot(snapshot)
    resumed.advance("generation")

    assert resumed.snapshot()["completed_stages"] == ["intake", "ranking", "generation"]


def test_v2_end_to_end_cli_writes_bundle(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "v2",
            "end-to-end",
            "--objective",
            "Run governed project workflow.",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["bundle"]["version"] == "2.7.0"
    assert (tmp_path / "end_to_end_result_bundle.json").exists()


def test_hosted_integration_operations_dashboard(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            root_dir=tmp_path,
            hosted_mode=True,
            auth_secret="test-hosted-secret-value-with-at-least-32-chars",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="Admin-password-1",
        )
    )
    login = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "Admin-password-1"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    page = client.get("/dashboard/integrations/operations")
    token = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "Admin-password-1"},
    )
    assert token.status_code == 200
    api = client.get(
        "/integrations/operations/dashboard",
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
    )

    assert page.status_code == 200, page.text
    assert "Integration operations" in page.text
    assert "End-to-end workflow" in page.text
    assert api.status_code == 200, api.text
    assert api.json()["dashboard"]["version"] == "2.7.0"
