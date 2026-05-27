from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.integrations.codex_assistant import (
    CodexIntegrationAssistant,
    detect_prohibited_integration_actions,
)
from molecule_ranker.integrations.schemas import ExternalRecordRef, SyncJob, SyncRecord
from molecule_ranker.platform.database import PlatformDatabase, artifact_records, platform_jobs


class FakeCodexProvider:
    def __init__(self, output_json: dict[str, Any], *, status: str = "succeeded") -> None:
        self.output_json = output_json
        self.status = status
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status=self.status,  # type: ignore[arg-type]
            output_text=json.dumps(self.output_json),
            output_json=self.output_json,
            artifacts_read=task.input_artifact_paths,
        )


def test_codex_mapping_suggestion_is_pending_review(tmp_path: Path) -> None:
    provider = FakeCodexProvider(
        {
            "suggested_mappings": [
                {
                    "internal_field": "candidate_id",
                    "external_field": "registry_id",
                    "rationale": "Both fields are present in the provided context.",
                }
            ],
            "artifact_refs": ["schema-sample-artifact"],
            "external_record_refs": ["benchling:entity:REG-1234"],
        }
    )
    assistant = CodexIntegrationAssistant(provider, working_directory=tmp_path)

    artifact = assistant.suggest_schema_mapping(
        external_records=[
            {
                "artifact_id": "schema-sample-artifact",
                "external_ref": {
                    "external_system_id": "benchling",
                    "external_record_type": "entity",
                    "external_record_id": "REG-1234",
                },
                "registry_id": "REG-1234",
                "candidate_id": "cand-1",
            }
        ],
        data_contract={"identifier_fields": ["candidate_id"]},
    )

    assert artifact.status == "succeeded"
    assert artifact.output_json is not None
    suggestion = artifact.output_json["suggested_mappings"][0]
    assert suggestion["status"] == "pending_review"
    assert suggestion["mapping_method"] == "codex_suggested_pending_validation"
    assert provider.tasks[0].metadata["cannot_activate_mappings"] is True
    assert provider.tasks[0].metadata["cannot_write_external_systems"] is True


def test_fake_external_id_flagged(tmp_path: Path) -> None:
    provider = FakeCodexProvider(
        {
            "record_summary": "The record should map to REG-FAKE-999.",
            "artifact_refs": ["schema-sample-artifact"],
            "external_record_refs": ["benchling:entity:REG-FAKE-999"],
        }
    )
    assistant = CodexIntegrationAssistant(provider, working_directory=tmp_path)

    artifact = assistant.summarize_external_record(
        external_ref=ExternalRecordRef(
            external_system_id="benchling",
            external_record_type="entity",
            external_record_id="REG-1234",
        ),
        payload={"artifact_id": "schema-sample-artifact", "registry_id": "REG-1234"},
    )

    assert artifact.status == "guardrail_failed"
    assert any(
        "Unbacked external record identifier: REG-FAKE-999" in warning
        for warning in artifact.guardrail_warnings
    )


def test_sync_failure_explanation_grounded_in_sync_records(tmp_path: Path) -> None:
    provider = FakeCodexProvider(
        {
            "failure_summary": "The failed record did not satisfy the contract.",
            "failed_records": ["sync-record-1 failed validation."],
            "artifact_refs": ["raw-payload-artifact"],
            "external_record_refs": ["benchling:assay_result:ASSAY-RESULT-001"],
        }
    )
    assistant = CodexIntegrationAssistant(provider, working_directory=tmp_path)
    sync_job = SyncJob(
        sync_job_id="sync-job-1",
        external_system_id="benchling",
        project_id="project-1",
        direction="import",
        object_types=["assay_results"],
        status="failed",
        error_summary="validation failed",
    )
    sync_record = SyncRecord(
        sync_record_id="sync-record-1",
        sync_job_id="sync-job-1",
        external_ref=ExternalRecordRef(
            external_system_id="benchling",
            external_record_type="assay_result",
            external_record_id="ASSAY-RESULT-001",
            retrieved_at=datetime.now(UTC),
        ),
        action="failed",
        status="failed",
        validation_errors=["missing candidate_id"],
        raw_payload_artifact_id="raw-payload-artifact",
    )

    artifact = assistant.explain_sync_failure(sync_job=sync_job, sync_records=[sync_record])

    assert provider.tasks[0].task_type == "explain_sync_failure"
    assert artifact.status == "succeeded"
    assert artifact.sync_record_ids == ["sync-record-1"]
    assert artifact.metadata["sync_record_ids"] == ["sync-record-1"]
    assert "benchling:assay_result:ASSAY-RESULT-001" in artifact.external_record_refs
    assert artifact.output_json is not None
    assert artifact.output_json["sync_record_ids"] == ["sync-record-1"]


def test_codex_cannot_enqueue_sync_automatically(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    provider = FakeCodexProvider(
        {
            "summary": "Run an integration sync now.",
            "enqueue_sync": True,
            "recommended_command": "molecule-ranker integration sync enqueue connector-1",
            "artifact_refs": ["export-preview-artifact"],
            "external_record_refs": [],
        }
    )
    assistant = CodexIntegrationAssistant(
        provider,
        working_directory=tmp_path,
        database=database,
        org_id="default",
        project_id="project-1",
    )

    artifact = assistant.draft_export_summary(
        export_preview={"artifact_id": "export-preview-artifact", "records": []}
    )

    assert artifact.status == "guardrail_failed"
    assert any("enqueue" in warning.lower() for warning in artifact.guardrail_warnings)
    with database.engine.connect() as connection:
        queued_jobs = connection.execute(
            select(func.count()).select_from(platform_jobs)
        ).scalar_one()
        stored_artifacts = connection.execute(select(artifact_records)).mappings().all()
    assert queued_jobs == 0
    assert len(stored_artifacts) == 1
    assert stored_artifacts[0]["artifact_type"] == "codex_backbone"


def test_prohibited_integration_action_detector() -> None:
    warnings = detect_prohibited_integration_actions(
        '{"activate_mapping": true, "EvidenceItem": {"id": "e1"}}'
    )

    assert any("activate" in warning for warning in warnings)
    assert any("EvidenceItem" in warning for warning in warnings)
