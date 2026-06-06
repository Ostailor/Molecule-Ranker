from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.e2e.lineage import ExternalLineageTracker
from molecule_ranker.e2e.schemas import EndToEndResultBundle

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _bundle() -> EndToEndResultBundle:
    return EndToEndResultBundle(
        bundle_id="bundle-1",
        workflow_id="workflow-1",
        project_id="project-1",
        disease_name=None,
        result_summary="Workflow audit bundle, not scientific evidence.",
        key_artifact_ids=[],
        candidate_summary={},
        generated_summary={},
        evidence_summary={},
        review_summary={},
        campaign_summary={},
        evaluation_summary={},
        integration_summary={},
        limitations=["This bundle is not scientific evidence."],
        created_at=NOW,
        metadata={},
    )


def test_import_lineage_links_assay_result_to_source_external_record() -> None:
    tracker = ExternalLineageTracker(workflow_id="workflow-1", now=lambda: NOW)

    record = tracker.record_import(
        internal_object_type="assay_result",
        internal_object_id="assay-1",
        external_system_id="lims-1",
        external_record_id="ext-assay-1",
        artifact_ids=["artifact-raw-payload"],
        sync_job_id="sync-1",
    )

    assert record.relation_type == "imported_from"
    assert record.source_object_type == "external_record"
    assert record.source_object_id == "ext-assay-1"
    assert record.target_object_type == "assay_result"
    assert record.target_object_id == "assay-1"
    assert record.external_record_refs[0]["external_system_id"] == "lims-1"
    assert record.metadata["sync_job_id"] == "sync-1"


def test_export_lineage_requires_approval_id_for_external_write_artifact() -> None:
    tracker = ExternalLineageTracker(workflow_id="workflow-1", now=lambda: NOW)

    with pytest.raises(ValueError, match="approval ID"):
        tracker.record_export(
            source_object_type="campaign_work_package",
            source_object_id="work-package-1",
            external_system_id="eln-1",
            external_record_id="task-1",
            artifact_id="artifact-export-package",
        )

    record = tracker.record_export(
        source_object_type="campaign_work_package",
        source_object_id="work-package-1",
        external_system_id="eln-1",
        external_record_id="task-1",
        artifact_id="artifact-export-package",
        approval_id="approval-1",
    )

    assert record.relation_type == "exported_to"
    assert record.artifact_ids == ["artifact-export-package"]
    assert record.metadata["approval_id"] == "approval-1"
    assert record.metadata["external_write_artifact"] is True


def test_pending_mapping_lineage_stays_pending_review() -> None:
    tracker = ExternalLineageTracker(workflow_id="workflow-1", now=lambda: NOW)

    record = tracker.record_pending_mapping(
        internal_object_type="candidate",
        internal_object_id="candidate-1",
        external_system_id="registry-1",
        external_record_id="compound-1",
        codex_suggested=True,
    )

    assert record.relation_type == "pending_mapping"
    assert record.metadata["mapping_status"] == "pending_review"
    assert record.metadata["codex_suggested"] is True
    assert record.metadata["codex_can_approve_mapping"] is False


def test_approval_lineage_is_included_in_bundle() -> None:
    tracker = ExternalLineageTracker(workflow_id="workflow-1", now=lambda: NOW)
    tracker.record_approved_mapping(
        internal_object_type="review_item",
        internal_object_id="review-1",
        external_system_id="eln-1",
        external_record_id="task-1",
        approval_id="approval-1",
        artifact_ids=["artifact-review"],
    )

    bundle = tracker.include_in_bundle(_bundle())

    assert tracker.records[0].relation_type == "approved_mapping"
    assert tracker.records[0].metadata["approval_id"] == "approval-1"
    assert bundle.integration_summary["lineage_record_count"] == 1
    assert bundle.metadata["lineage_records"][0]["relation_type"] == "approved_mapping"
