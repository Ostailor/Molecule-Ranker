from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from molecule_ranker.e2e.workflow_runner import (
    EndToEndWorkflowRunner,
    EndToEndWorkflowRunnerConfig,
    WorkflowRunRequest,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_mocked_full_discovery_loop_succeeds() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="full_discovery_loop",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        )
    )

    assert result.workflow.status == "succeeded"
    assert result.bundle is not None
    assert result.bundle.workflow_id == result.workflow.workflow_id
    assert result.bundle.metadata["mode"] == "mocked"
    assert result.external_writes_performed == 0
    assert result.step_by_type("project_setup").status == "succeeded"
    assert result.step_by_type("report_bundle").status == "succeeded"
    assert result.lineage_records
    assert result.bundle.metadata["lineage_records"]


def test_dry_run_integration_loop_produces_no_external_writes() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="integration_sync_loop",
            mode="dry_run",
            project_id="project-1",
            requested_by="user-1",
        )
    )

    assert result.workflow.status == "succeeded"
    assert result.external_writes_performed == 0
    assert result.planned_external_writes == 1
    assert result.bundle is not None
    assert result.bundle.integration_summary["external_writes_performed"] == 0
    assert result.step_by_type("integration_sync").metadata["simulated_external_write"] is True


def test_read_only_live_mode_refuses_external_write() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="integration_sync_loop",
            mode="read_only_live",
            project_id="project-1",
            requested_by="user-1",
            requested_external_write=True,
        )
    )

    assert result.workflow.status == "failed"
    assert result.bundle is None
    assert result.external_writes_performed == 0
    assert "read_only_live cannot perform external writes" in result.warnings[0]


def test_write_approved_mode_waits_for_approval() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    pending = runner.run(
        WorkflowRunRequest(
            workflow_type="integration_sync_loop",
            mode="write_approved_live",
            project_id="project-1",
            requested_by="user-1",
            requested_external_write=True,
            approvals=[],
        )
    )

    assert pending.workflow.status == "awaiting_approval"
    assert pending.bundle is None
    assert pending.step_by_type("integration_sync").status == "awaiting_approval"
    assert pending.external_writes_performed == 0

    approved = runner.run(
        WorkflowRunRequest(
            workflow_type="integration_sync_loop",
            mode="write_approved_live",
            project_id="project-1",
            requested_by="user-1",
            requested_external_write=True,
            approvals=["external_write"],
            governance_permissions=["integration:write"],
        )
    )

    assert approved.workflow.status == "succeeded"
    assert approved.external_writes_performed == 1
    assert approved.bundle is not None
    assert approved.bundle.integration_summary["external_writes_performed"] == 1


def test_live_required_data_unavailable_can_mark_partial() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="disease_to_ranked_candidates",
            mode="read_only_live",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
            unavailable_required_data=["literature_retrieval"],
            config=EndToEndWorkflowRunnerConfig(partial_on_live_data_unavailable=True),
        )
    )

    assert result.workflow.status == "partially_succeeded"
    assert result.step_by_type("literature_retrieval").status == "failed"
    assert result.bundle is not None
    assert "live required data unavailable" in " ".join(result.warnings).lower()


def test_antibody_track_workflow_succeeds_with_generation_disabled_by_default() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="disease_to_antibody_candidates",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        )
    )

    assert result.workflow.status == "succeeded"
    assert result.bundle is not None
    assert result.bundle.biologics_summary["existing_antibody_retrieval_supported"] is True
    assert result.bundle.biologics_summary["antibody_generation_enabled"] is False
    assert result.bundle.biologics_summary["review_gate_required"] is True
    assert all(step.step_type != "antibody_generation" for step in result.steps)


def test_antibody_generation_requires_approved_plugin_ids() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    with pytest.raises(ValueError, match="approved plugin ids"):
        runner.run(
            WorkflowRunRequest(
                workflow_type="disease_to_antibody_candidates",
                mode="mocked",
                antibody_generation_enabled=True,
            )
        )

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="disease_to_antibody_candidates",
            mode="mocked",
            antibody_generation_enabled=True,
            approved_antibody_generation_plugin_ids=["approved-biologics-plugin"],
        )
    )

    assert result.workflow.status == "succeeded"
    generation_step = result.step_by_type("antibody_generation")
    assert generation_step.metadata["computational_hypothesis_only"] is True
    assert result.bundle is not None
    assert result.bundle.biologics_summary["antibody_generation_enabled"] is True
    assert result.bundle.biologics_summary["approved_antibody_generation_plugin_ids"] == [
        "approved-biologics-plugin"
    ]


def test_biologics_discovery_loop_mocked_succeeds_with_generation_disabled() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="biologics_discovery_loop",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        )
    )

    assert result.workflow.status == "succeeded"
    assert result.bundle is not None
    assert result.bundle.biologics_summary["workflow_name"] == "biologics_discovery_loop"
    assert result.bundle.biologics_summary["antibody_generation_enabled"] is False
    assert result.bundle.biologics_summary["existing_biologic_candidates_ranked"] == 1
    assert result.bundle.biologics_summary["numbering_included_with_sequence_validation"] is True
    assert all(step.step_type != "antibody_generation" for step in result.steps)
    assert result.steps[-1].step_name == "Biologics result bundle"


def test_biologics_discovery_loop_generated_antibody_warning_present() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="biologics_discovery_loop",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
            antibody_generation_enabled=True,
            approved_antibody_generation_plugin_ids=["approved-antibody-plugin"],
        )
    )

    assert result.workflow.status == "succeeded"
    assert result.bundle is not None
    assert (
        result.bundle.biologics_summary["generated_antibody_warning"]
        == "Generated antibodies are computational hypotheses only."
    )
    generation_step = result.step_by_type("antibody_generation")
    assert generation_step.status == "succeeded"
    assert generation_step.metadata["deterministic_validation_required"] is True
    assert result.bundle.biologics_summary["generated_antibodies_with_direct_evidence"] == 0


def test_biologics_discovery_loop_bundle_has_no_forbidden_operational_text() -> None:
    runner = EndToEndWorkflowRunner(now=lambda: NOW)

    result = runner.run(
        WorkflowRunRequest(
            workflow_type="biologics_discovery_loop",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        )
    )

    assert result.bundle is not None
    payload = result.bundle.model_dump(mode="json")
    assert payload["metadata"]["v3_product_contract"]["product_version"] == "3.0.0"
    payload["metadata"].pop("v3_product_contract")
    serialized = json.dumps(payload, sort_keys=True).lower()
    forbidden = (
        "lab protocol",
        "wet-lab protocol",
        "immunization protocol",
        "expression/purification",
        "purification protocol",
        "synthesis instructions",
        "animal dosing",
        "human dosing",
    )
    assert not any(term in serialized for term in forbidden)
