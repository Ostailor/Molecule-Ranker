from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.e2e.external_sync_planner import (
    ExternalSyncPlanner,
    ExternalSyncPlannerRequest,
)
from molecule_ranker.integrations.schemas import DataContract

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _contract() -> DataContract:
    return DataContract(
        contract_id="contract-assay",
        name="Assay result contract",
        object_type="assay_result",
        version="V2",
        required_fields=["result_id", "candidate_id", "source_record_id"],
        identifier_fields=["result_id", "candidate_id", "source_record_id"],
        field_types={
            "result_id": "string",
            "candidate_id": "string",
            "source_record_id": "string",
        },
    )


def test_write_sync_defaults_to_dry_run() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["lims-1"],
            direction="export",
            object_types=["assay_result"],
            requested_mode="write",
            data_contracts={"assay_result": _contract()},
            mapping_status={"assay_result": "active"},
            credential_refs={"lims-1": "cred-lims"},
            external_system_health={"lims-1": "ok"},
            user_permissions=["integration:write"],
            governance_policies={"external_write_requires_approval": True},
            planned_records=[
                {
                    "result_id": "assay-1",
                    "candidate_id": "cand-1",
                    "source_record_id": "ext-1",
                }
            ],
        )
    )

    assert plan.mode == "dry_run"
    assert plan.dry_run is True
    assert "external_write" in plan.approval_requirements
    assert plan.risk_summary["write_sync_defaulted_to_dry_run"] is True


def test_unknown_mappings_block_write_sync() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["registry-1"],
            direction="export",
            object_types=["candidate"],
            requested_mode="write",
            mapping_status={"candidate": "unknown"},
            credential_refs={"registry-1": "cred-registry"},
            external_system_health={"registry-1": "ok"},
            user_permissions=["integration:write"],
            governance_policies={"external_write_approval_id": "approval-1"},
        )
    )

    assert plan.mode == "dry_run"
    assert plan.required_mappings == ["candidate"]
    assert plan.risk_summary["blocked_write"] is True
    assert "unknown mappings block write sync" in " ".join(plan.risk_summary["reasons"])


def test_credentials_are_checked_by_reference_only() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["lims-1"],
            direction="import",
            object_types=["assay_result"],
            requested_mode="read_only",
            data_contracts={"assay_result": _contract()},
            mapping_status={"assay_result": "active"},
            credential_refs={"lims-1": "env:LIMS_TOKEN"},
            external_system_health={"lims-1": "ok"},
            planned_records=[
                {
                    "result_id": "assay-1",
                    "candidate_id": "cand-1",
                    "source_record_id": "ext-1",
                }
            ],
        )
    )

    assert plan.mode == "read_only"
    assert plan.metadata["credential_checks"]["lims-1"]["checked_by_reference_only"] is True
    assert "LIMS_TOKEN" not in plan.model_dump_json()


def test_external_system_health_required() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["lims-1"],
            direction="import",
            object_types=["assay_result"],
            requested_mode="read_only",
            data_contracts={"assay_result": _contract()},
            credential_refs={"lims-1": "cred-lims"},
            external_system_health={"lims-1": "degraded"},
        )
    )

    assert plan.mode == "dry_run"
    assert plan.dry_run is True
    assert plan.risk_summary["health_required"] is True
    assert "lims-1" in plan.risk_summary["unhealthy_external_system_ids"]


def test_data_contracts_validated_before_sync() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["lims-1"],
            direction="import",
            object_types=["assay_result"],
            requested_mode="read_only",
            data_contracts={"assay_result": _contract()},
            mapping_status={"assay_result": "active"},
            credential_refs={"lims-1": "cred-lims"},
            external_system_health={"lims-1": "ok"},
            planned_records=[{"result_id": "assay-1"}],
        )
    )

    assert plan.mode == "dry_run"
    assert plan.risk_summary["contract_validation_passed"] is False
    assert "assay_result" in plan.risk_summary["invalid_contract_object_types"]


def test_codex_invented_mappings_are_not_allowed() -> None:
    planner = ExternalSyncPlanner(now=lambda: NOW)

    plan = planner.plan(
        ExternalSyncPlannerRequest(
            project_id="project-1",
            external_system_ids=["registry-1"],
            direction="export",
            object_types=["candidate"],
            requested_mode="write",
            mapping_status={"candidate": "codex_suggested"},
            credential_refs={"registry-1": "cred-registry"},
            external_system_health={"registry-1": "ok"},
            user_permissions=["integration:write"],
            governance_policies={"external_write_approval_id": "approval-1"},
        )
    )

    assert plan.mode == "dry_run"
    assert plan.required_mappings == ["candidate"]
    assert plan.risk_summary["codex_invented_mappings_blocked"] is True
