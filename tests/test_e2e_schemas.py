from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.e2e.schemas import (
    EndToEndResultBundle,
    EndToEndValidationResult,
    EndToEndWorkflow,
    EndToEndWorkflowStep,
    WorkflowLineageRecord,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_end_to_end_workflow_accepts_allowed_values() -> None:
    workflow = EndToEndWorkflow(
        workflow_id="workflow-1",
        name="Disease to campaign",
        workflow_type="disease_to_campaign_plan",
        disease_name="Example disease",
        project_id="project-1",
        campaign_id="campaign-1",
        mode="dry_run",
        requested_by="user-1",
        autonomy_level="execute_with_approval",
        status="planned",
        created_at=NOW,
        started_at=None,
        completed_at=None,
        metadata={"source": "test"},
    )

    assert workflow.workflow_type == "disease_to_campaign_plan"
    assert workflow.metadata == {"source": "test"}


def test_end_to_end_workflow_rejects_invalid_literals_and_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        EndToEndWorkflow.model_validate(
            {
                "workflow_id": "workflow-1",
                "name": "Invalid workflow",
                "workflow_type": "unsupported",
                "disease_name": None,
                "project_id": None,
                "campaign_id": None,
                "mode": "dry_run",
                "requested_by": None,
                "autonomy_level": "suggest_only",
                "status": "planned",
                "created_at": NOW,
                "started_at": None,
                "completed_at": None,
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        EndToEndWorkflow(
            workflow_id="workflow-1",
            name="Naive timestamp",
            workflow_type="full_discovery_loop",
            disease_name=None,
            project_id=None,
            campaign_id=None,
            mode="mocked",
            requested_by=None,
            autonomy_level="suggest_only",
            status="planned",
            created_at=datetime(2026, 6, 5, 12),
            started_at=None,
            completed_at=None,
            metadata={},
        )


def test_end_to_end_workflow_step_schema() -> None:
    step = EndToEndWorkflowStep(
        step_id="step-1",
        workflow_id="workflow-1",
        step_index=0,
        step_name="Build graph",
        step_type="graph_build",
        required=True,
        tool_name="build_graph",
        input_artifact_ids=["artifact-in"],
        output_artifact_ids=["artifact-out"],
        external_system_ids=["ext-1"],
        status="pending",
        started_at=None,
        completed_at=None,
        warnings=[],
        metadata={},
    )

    assert step.step_type == "graph_build"
    assert step.output_artifact_ids == ["artifact-out"]

    with pytest.raises(ValidationError):
        EndToEndWorkflowStep.model_validate(
            {
                "step_id": "step-1",
                "workflow_id": "workflow-1",
                "step_index": 0,
                "step_name": "Bad step",
                "step_type": "unsupported",
                "required": True,
                "tool_name": None,
                "input_artifact_ids": [],
                "output_artifact_ids": [],
                "external_system_ids": [],
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "warnings": [],
                "metadata": {},
            }
        )


def test_end_to_end_result_bundle_is_not_scientific_evidence() -> None:
    bundle = EndToEndResultBundle(
        bundle_id="bundle-1",
        workflow_id="workflow-1",
        project_id="project-1",
        disease_name="Example disease",
        result_summary="Workflow completed with reviewable artifacts.",
        key_artifact_ids=["report-1"],
        candidate_summary={"count": 3},
        generated_summary={},
        evidence_summary={"source_backed_artifacts": 2},
        review_summary={},
        campaign_summary={},
        evaluation_summary={},
        integration_summary={},
        limitations=["This bundle is not scientific evidence."],
        created_at=NOW,
        metadata={},
    )

    assert "not scientific evidence" in " ".join(bundle.limitations).lower()

    with pytest.raises(ValidationError, match="not scientific evidence"):
        EndToEndResultBundle(
            bundle_id="bundle-1",
            workflow_id="workflow-1",
            project_id=None,
            disease_name=None,
            result_summary="Scientific evidence record.",
            key_artifact_ids=[],
            candidate_summary={},
            generated_summary={},
            evidence_summary={},
            review_summary={},
            campaign_summary={},
            evaluation_summary={},
            integration_summary={},
            limitations=[],
            created_at=NOW,
            metadata={"scientific_evidence": True},
        )


def test_lineage_and_validation_result_schemas() -> None:
    lineage = WorkflowLineageRecord(
        lineage_id="lineage-1",
        workflow_id="workflow-1",
        source_object_type="step",
        source_object_id="step-1",
        target_object_type="artifact",
        target_object_id="artifact-1",
        relation_type="produced",
        artifact_ids=["artifact-1"],
        external_record_refs=[{"external_system_id": "ext-1", "external_record_id": "EXT-1"}],
        created_at=NOW,
        metadata={},
    )
    validation = EndToEndValidationResult(
        validation_id="validation-1",
        workflow_id="workflow-1",
        passed=True,
        required_artifacts_present=True,
        artifact_contracts_valid=True,
        lineage_complete=True,
        guardrails_passed=True,
        external_sync_validated=True,
        approvals_satisfied=True,
        findings=[],
        warnings=[],
        created_at=NOW,
        metadata={},
    )

    assert lineage.relation_type == "produced"
    assert validation.passed is True

    with pytest.raises(ValidationError):
        WorkflowLineageRecord.model_validate(
            {
                "lineage_id": "lineage-1",
                "workflow_id": "workflow-1",
                "source_object_type": "step",
                "source_object_id": "step-1",
                "target_object_type": "artifact",
                "target_object_id": "artifact-1",
                "relation_type": "unsupported",
                "artifact_ids": [],
                "external_record_refs": [],
                "created_at": NOW,
                "metadata": {},
            }
        )
