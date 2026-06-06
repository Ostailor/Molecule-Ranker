from __future__ import annotations

import json
from datetime import UTC, datetime

from molecule_ranker.e2e.result_bundle import (
    EndToEndResultBundleGenerator,
    ResultBundleInput,
)
from molecule_ranker.e2e.schemas import (
    EndToEndValidationResult,
    EndToEndWorkflow,
    WorkflowLineageRecord,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _workflow() -> EndToEndWorkflow:
    return EndToEndWorkflow(
        workflow_id="workflow-1",
        name="Integration sync loop",
        workflow_type="integration_sync_loop",
        disease_name=None,
        project_id="project-1",
        campaign_id="campaign-1",
        mode="dry_run",
        requested_by="user-1",
        autonomy_level="execute_with_approval",
        status="succeeded",
        created_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        metadata={},
    )


def _lineage() -> list[WorkflowLineageRecord]:
    return [
        WorkflowLineageRecord(
            lineage_id="lineage-1",
            workflow_id="workflow-1",
            source_object_type="external_record",
            source_object_id="ext-assay-1",
            target_object_type="assay_result",
            target_object_id="assay-1",
            relation_type="imported_from",
            artifact_ids=["artifact-raw"],
            external_record_refs=[
                {
                    "external_system_id": "lims-1",
                    "external_record_id": "ext-assay-1",
                }
            ],
            created_at=NOW,
            metadata={"sync_job_id": "sync-1"},
        )
    ]


def _validation() -> EndToEndValidationResult:
    return EndToEndValidationResult(
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


def _bundle_input() -> ResultBundleInput:
    return ResultBundleInput(
        workflow=_workflow(),
        artifacts=[
            {
                "artifact_id": "artifact-raw",
                "artifact_type": "integration_payload",
                "path": "payload.json",
            }
        ],
        candidate_summary={"count": 2},
        generated_molecule_summary={"count": 0, "review_required": True},
        evidence_summary={"source_backed_items": 1},
        developability_summary={"reviewed_items": 0},
        experimental_evidence_summary={"validated_assay_results": 1},
        graph_hypothesis_summary={"graph_facts": 0, "hypotheses": 0},
        portfolio_campaign_summary={"campaign_work_packages": 1},
        integration_lineage_summary={"external_records": 1},
        evaluation_summary={"passed": True},
        codex_summary={"drafted_from": "deterministic_bundle_data"},
        approval_summary={"external_write_approvals": ["approval-1"]},
        guardrail_summary={"passed": True},
        lineage_records=_lineage(),
        validation_result=_validation(),
        next_recommended_actions=["Review mapping queue"],
        limitations=["Dry-run only."],
        metadata={"note": "No dosing or synthesis instructions should appear."},
    )


def test_bundle_generated(tmp_path) -> None:
    generated = EndToEndResultBundleGenerator(now=lambda: NOW).generate(
        _bundle_input(),
        output_dir=tmp_path,
    )

    assert generated.bundle.workflow_id == "workflow-1"
    assert generated.files["json"].name == "e2e_result_bundle.json"
    assert generated.files["markdown"].name == "e2e_result_bundle.md"
    assert generated.files["lineage"].name == "e2e_lineage.json"
    assert generated.files["validation"].name == "e2e_validation.json"
    assert all(path.exists() for path in generated.files.values())


def test_lineage_included(tmp_path) -> None:
    generated = EndToEndResultBundleGenerator(now=lambda: NOW).generate(
        _bundle_input(),
        output_dir=tmp_path,
    )
    lineage_payload = json.loads(generated.files["lineage"].read_text())
    bundle_payload = json.loads(generated.files["json"].read_text())

    assert lineage_payload["lineage_records"][0]["relation_type"] == "imported_from"
    assert bundle_payload["integration_summary"]["lineage_record_count"] == 1
    assert bundle_payload["metadata"]["lineage_records"][0]["target_object_id"] == "assay-1"


def test_limitations_included(tmp_path) -> None:
    generated = EndToEndResultBundleGenerator(now=lambda: NOW).generate(
        _bundle_input(),
        output_dir=tmp_path,
    )
    bundle_payload = json.loads(generated.files["json"].read_text())
    limitation_text = " ".join(bundle_payload["limitations"]).lower()

    assert "not scientific evidence" in limitation_text
    assert "no medical advice" in limitation_text
    assert "dry-run only" in limitation_text


def test_forbidden_text_absent(tmp_path) -> None:
    unsafe_input = _bundle_input().model_copy(
        update={
            "next_recommended_actions": [
                "Do not include dosing, synthesis instructions, or claims of activity."
            ],
            "metadata": {
                "unsafe_note": "This contains lab protocols and efficacy claims."
            },
        }
    )
    generated = EndToEndResultBundleGenerator(now=lambda: NOW).generate(
        unsafe_input,
        output_dir=tmp_path,
    )

    combined = "\n".join(path.read_text() for path in generated.files.values()).lower()
    for forbidden in [
        "lab protocol",
        "lab protocols",
        "synthesis instruction",
        "synthesis instructions",
        "dosing",
        "activity",
        "safety",
        "efficacy",
    ]:
        assert forbidden not in combined
