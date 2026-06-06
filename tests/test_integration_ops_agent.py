from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agents.integration_ops import (
    IntegrationOpsAgent,
    IntegrationOpsRequest,
)
from molecule_ranker.integrations.schemas import DataContract

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _assay_contract() -> DataContract:
    return DataContract(
        contract_id="contract-assay-v2",
        name="Validated assay result",
        object_type="assay_result",
        version="V2",
        required_fields=[
            "result_id",
            "candidate_id",
            "assay_name",
            "measured_value",
            "measured_unit",
            "source_record_id",
        ],
        field_types={
            "result_id": "string",
            "candidate_id": "string",
            "assay_name": "string",
            "measured_value": "number",
            "measured_unit": "string",
            "source_record_id": "string",
        },
        identifier_fields=["result_id", "candidate_id", "source_record_id"],
    )


def _valid_assay_result() -> dict[str, object]:
    return {
        "result_id": "assay-1",
        "candidate_id": "cand-1",
        "assay_name": "Deterministic fixture assay",
        "measured_value": 42.0,
        "measured_unit": "au",
        "source_record_id": "ext-assay-1",
        "external_system_id": "lims-1",
    }


def test_dry_run_sync_plan_works() -> None:
    agent = IntegrationOpsAgent(now=lambda: NOW)

    result = agent.plan_dry_run_sync(
        IntegrationOpsRequest(
            project_id="project-1",
            external_system_id="lims-1",
            object_types=["assay_result"],
        )
    )

    assert result.status == "planned"
    assert result.mode == "dry_run"
    assert result.external_write_performed is False
    assert result.sync_job.mode == "dry_run"
    assert result.sync_job.status == "dry_run"
    assert result.audit_events[0].metadata["requested_by"] == "IntegrationOpsAgent"


def test_write_sync_approval_required() -> None:
    agent = IntegrationOpsAgent(now=lambda: NOW)

    result = agent.run_write_sync(
        IntegrationOpsRequest(
            project_id="project-1",
            external_system_id="lims-1",
            object_types=["assay_result"],
            requested_external_write=True,
            write_approval_id=None,
            governance_permissions=[],
        )
    )

    assert result.status == "approval_required"
    assert result.external_write_performed is False
    assert "explicit approval" in result.warnings[0]


def test_mapping_conflict_goes_pending_review() -> None:
    agent = IntegrationOpsAgent(now=lambda: NOW)

    result = agent.detect_mapping_conflicts(
        internal_entity={
            "candidate_id": "cand-1",
            "name": "Candidate One",
        },
        external_records=[
            {
                "external_system_id": "registry-1",
                "external_record_id": "ext-1",
                "name": "Candidate One",
            },
            {
                "external_system_id": "registry-1",
                "external_record_id": "ext-2",
                "name": "Candidate One",
            },
        ],
        internal_entity_type="candidate",
        project_id="project-1",
        codex_suggested=True,
    )

    assert result.mapping is not None
    assert result.mapping.status == "pending_review"
    assert result.mapping.mapping_method == "codex_suggested_pending_validation"
    assert result.status == "pending_review"


def test_imported_assay_result_validated() -> None:
    agent = IntegrationOpsAgent(now=lambda: NOW)

    result = agent.import_validated_assay_results(
        IntegrationOpsRequest(
            project_id="project-1",
            external_system_id="lims-1",
            object_types=["assay_result"],
            data_contract=_assay_contract(),
            assay_results=[_valid_assay_result()],
        )
    )

    assert result.status == "succeeded"
    assert result.records_valid == 1
    assert result.records_imported == 1
    assert result.sync_job.rows_valid == 1
    assert result.validation_reports[0].valid is True
    assert result.lineage_records
    assert result.metadata["validation_versions"] == ["V0.6", "V2"]


def test_secret_redaction() -> None:
    agent = IntegrationOpsAgent(now=lambda: NOW)

    result = agent.plan_dry_run_sync(
        IntegrationOpsRequest(
            project_id="project-1",
            external_system_id="lims-1",
            object_types=["assay_result"],
            metadata={
                "authorization": "Bearer sk-secret",
                "nested": {"password": "secret-value"},
                "safe_note": "visible",
            },
        )
    )

    rendered = result.model_dump_json()
    assert "sk-secret" not in rendered
    assert "secret-value" not in rendered
    assert "[REDACTED]" in rendered
    assert result.metadata["safe_note"] == "visible"
