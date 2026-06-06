from __future__ import annotations

from datetime import UTC, datetime

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
