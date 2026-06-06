from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.e2e.schemas import EndToEndWorkflow, EndToEndWorkflowStep
from molecule_ranker.e2e.state_machine import WorkflowStateMachine, WorkflowStateTransitionError

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_successful_workflow_transitions_and_records_lineage() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(),
        steps=[
            _step(0, "disease_resolution", output_artifact_ids=["disease-artifact"]),
            _step(1, "report_bundle", output_artifact_ids=["bundle-artifact"]),
        ],
        now=lambda: NOW,
    )

    machine.start_workflow()
    machine.start_step("step-0")
    machine.complete_step("step-0")
    machine.start_step("step-1")
    machine.complete_step("step-1")
    machine.complete_workflow()

    assert machine.workflow.status == "succeeded"
    assert [step.status for step in machine.steps] == ["succeeded", "succeeded"]
    assert {record.target_object_id for record in machine.lineage_records} == {
        "disease-artifact",
        "bundle-artifact",
    }
    assert [event["event_type"] for event in machine.audit_events] == [
        "workflow_started",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "workflow_succeeded",
    ]


def test_required_failure_stops_downstream_required_steps() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(),
        steps=[
            _step(0, "target_discovery", required=True),
            _step(1, "molecule_retrieval", required=True),
            _step(2, "codex_summary", required=False),
        ],
        now=lambda: NOW,
    )

    machine.start_workflow()
    machine.start_step("step-0")
    machine.fail_step("step-0", reason="Required target discovery failed.")

    assert machine.workflow.status == "failed"
    assert machine.step_by_id("step-1").status == "skipped"
    assert "blocked by required step failure" in machine.step_by_id("step-1").warnings[0]
    assert machine.step_by_id("step-2").status == "pending"
    with pytest.raises(WorkflowStateTransitionError, match="Workflow is failed"):
        machine.start_step("step-1")


def test_optional_failure_continues_with_warning() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(),
        steps=[
            _step(0, "literature_retrieval", required=False),
            _step(1, "graph_build", required=True),
        ],
        now=lambda: NOW,
    )

    machine.start_workflow()
    machine.start_step("step-0")
    machine.fail_step("step-0", reason="Optional literature source unavailable.")
    machine.start_step("step-1")

    assert machine.workflow.status == "running"
    assert machine.step_by_id("step-0").status == "failed"
    assert "Optional step failed" in machine.step_by_id("step-0").warnings[0]
    assert machine.step_by_id("step-1").status == "running"


def test_approval_gate_pauses_and_resumes_workflow() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(mode="write_approved_live"),
        steps=[
            _step(
                0,
                "integration_sync",
                metadata={"external_write": True},
                external_system_ids=["eln-1"],
            )
        ],
        now=lambda: NOW,
    )

    machine.start_workflow()
    machine.start_step("step-0")

    assert machine.workflow.status == "awaiting_approval"
    assert machine.step_by_id("step-0").status == "awaiting_approval"

    machine.approve_step("step-0", approved_by="reviewer-1", approval_id="approval-1")
    machine.complete_step("step-0")

    assert machine.workflow.status == "running"
    assert machine.step_by_id("step-0").status == "succeeded"
    assert any(record.relation_type == "approved_for" for record in machine.lineage_records)


def test_cancelled_workflow_stops_execution() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(),
        steps=[_step(0, "disease_resolution")],
        now=lambda: NOW,
    )

    machine.start_workflow()
    machine.cancel_workflow(reason="Operator cancelled run.")

    assert machine.workflow.status == "cancelled"
    with pytest.raises(WorkflowStateTransitionError, match="cancelled"):
        machine.start_step("step-0")


def test_resumed_workflow_preserves_previous_artifact_lineage() -> None:
    machine = WorkflowStateMachine(
        workflow=_workflow(),
        steps=[_step(0, "disease_resolution", output_artifact_ids=["disease-artifact"])],
        now=lambda: NOW,
    )
    machine.start_workflow()
    machine.start_step("step-0")
    machine.complete_step("step-0")
    machine.fail_workflow(reason="Downstream service failed.")

    resumed = WorkflowStateMachine.from_snapshot(machine.snapshot(), now=lambda: NOW)
    resumed.resume_workflow(reason="Repair completed.")

    assert resumed.workflow.status == "running"
    assert resumed.lineage_records[0].target_object_id == "disease-artifact"
    assert any(event["event_type"] == "workflow_resumed" for event in resumed.audit_events)


def _workflow(mode: str = "dry_run") -> EndToEndWorkflow:
    return EndToEndWorkflow(
        workflow_id="workflow-1",
        name="Test workflow",
        workflow_type="full_discovery_loop",
        disease_name="Example disease",
        project_id="project-1",
        campaign_id=None,
        mode=mode,  # type: ignore[arg-type]
        requested_by="user-1",
        autonomy_level="execute_with_approval",
        status="planned",
        created_at=NOW,
        started_at=None,
        completed_at=None,
        metadata={},
    )


def _step(
    index: int,
    step_type: str,
    *,
    required: bool = True,
    output_artifact_ids: list[str] | None = None,
    external_system_ids: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> EndToEndWorkflowStep:
    return EndToEndWorkflowStep(
        step_id=f"step-{index}",
        workflow_id="workflow-1",
        step_index=index,
        step_name=step_type.replace("_", " ").title(),
        step_type=step_type,  # type: ignore[arg-type]
        required=required,
        tool_name=step_type,
        input_artifact_ids=[],
        output_artifact_ids=output_artifact_ids or [],
        external_system_ids=external_system_ids or [],
        status="pending",
        started_at=None,
        completed_at=None,
        warnings=[],
        metadata=metadata or {},
    )
