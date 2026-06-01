from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignExecutionEvent,
    CampaignExecutionEventType,
    CampaignPlan,
    CampaignWorkPackage,
    contains_procedural_lab_detail,
)
from molecule_ranker.campaigns.store import CampaignStore
from molecule_ranker.integrations.connectors.base import ExternalConnector
from molecule_ranker.integrations.exporters import (
    ExportPackageResult,
    create_export_package,
)
from molecule_ranker.integrations.schemas import (
    EntityMapping,
    ExternalRecordRef,
    SyncRecord,
)
from molecule_ranker.integrations.store import IntegrationStore

CampaignPackageKind = Literal[
    "campaign_summary",
    "validation_handoff",
    "work_package_list",
]

EXTERNAL_STATUS_TO_WORK_PACKAGE_STATUS = {
    "proposed": "proposed",
    "approved": "approved",
    "ready": "ready",
    "queued": "ready",
    "started": "in_progress",
    "running": "in_progress",
    "in_progress": "in_progress",
    "blocked": "blocked",
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "succeeded": "completed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
}


@dataclass(frozen=True)
class CampaignExternalExportResult:
    package: ExportPackageResult
    payload: dict[str, Any]
    external_refs: list[ExternalRecordRef] = field(default_factory=list)
    connector_result: dict[str, Any] | None = None


def build_campaign_summary_payload(campaign: Campaign, plan: CampaignPlan) -> dict[str, Any]:
    _ensure_plan_matches_campaign(campaign, plan)
    payload = {
        "campaign_id": campaign.campaign_id,
        "campaign_plan_id": plan.campaign_plan_id,
        "project_id": campaign.project_id,
        "program_id": campaign.program_id,
        "name": campaign.name,
        "status": campaign.status,
        "disease_focus": campaign.disease_focus,
        "target_focus": campaign.target_focus,
        "hypothesis_ids": campaign.hypothesis_ids,
        "portfolio_selection_ids": campaign.portfolio_selection_ids,
        "objective_ids": [objective.objective_id for objective in plan.objectives],
        "work_package_ids": [package.work_package_id for package in plan.work_packages],
        "recommended_sequence": plan.recommended_sequence,
        "stage_gate_ids": [str(gate.get("gate_id")) for gate in plan.stage_gates],
        "replan_triggers": plan.replan_triggers,
        "expected_learning_value": plan.expected_learning_value,
        "budget_summary": plan.budget_summary,
        "limitations": _campaign_external_limitations(),
    }
    _validate_campaign_export_payload(payload)
    return payload


def build_validation_handoff_payload(campaign: Campaign, plan: CampaignPlan) -> dict[str, Any]:
    _ensure_plan_matches_campaign(campaign, plan)
    payload = {
        "handoff_type": "campaign_validation",
        "campaign_id": campaign.campaign_id,
        "campaign_plan_id": plan.campaign_plan_id,
        "hypothesis_ids": campaign.hypothesis_ids,
        "portfolio_selection_ids": campaign.portfolio_selection_ids,
        "objectives": [
            {
                "objective_id": objective.objective_id,
                "objective_type": objective.objective_type,
                "linked_hypothesis_ids": objective.linked_hypothesis_ids,
                "linked_candidate_ids": objective.linked_candidate_ids,
                "success_criteria": objective.success_criteria,
                "stop_criteria": objective.stop_criteria,
            }
            for objective in plan.objectives
        ],
        "stage_gates": plan.stage_gates,
        "work_packages": _high_level_work_package_rows(plan.work_packages),
        "limitations": _campaign_external_limitations(),
    }
    _validate_campaign_export_payload(payload)
    return payload


def build_high_level_work_package_payload(
    campaign: Campaign,
    plan: CampaignPlan,
) -> dict[str, Any]:
    _ensure_plan_matches_campaign(campaign, plan)
    payload = {
        "campaign_id": campaign.campaign_id,
        "campaign_plan_id": plan.campaign_plan_id,
        "work_packages": _high_level_work_package_rows(plan.work_packages),
        "dependency_graph": plan.dependency_graph,
        "recommended_sequence": plan.recommended_sequence,
        "limitations": _campaign_external_limitations(),
    }
    _validate_campaign_export_payload(payload)
    return payload


def export_campaign_summary_package(
    campaign: Campaign,
    plan: CampaignPlan,
    output_dir: str | Path,
    *,
    connector: ExternalConnector | None = None,
    external_write: bool = False,
    explicit_permission: bool = False,
    external_system_target: dict[str, Any] | None = None,
) -> CampaignExternalExportResult:
    payload = build_campaign_summary_payload(campaign, plan)
    return _export_campaign_package(
        payload,
        output_dir,
        package_kind="campaign_summary",
        connector=connector,
        external_write=external_write,
        explicit_permission=explicit_permission,
        external_system_target=external_system_target,
    )


def export_validation_handoff_package(
    campaign: Campaign,
    plan: CampaignPlan,
    output_dir: str | Path,
    *,
    connector: ExternalConnector | None = None,
    external_write: bool = False,
    explicit_permission: bool = False,
    external_system_target: dict[str, Any] | None = None,
) -> CampaignExternalExportResult:
    payload = build_validation_handoff_payload(campaign, plan)
    return _export_campaign_package(
        payload,
        output_dir,
        package_kind="validation_handoff",
        connector=connector,
        external_write=external_write,
        explicit_permission=explicit_permission,
        external_system_target=external_system_target,
    )


def export_high_level_work_package_list(
    campaign: Campaign,
    plan: CampaignPlan,
    output_dir: str | Path,
    *,
    connector: ExternalConnector | None = None,
    external_write: bool = False,
    explicit_permission: bool = False,
    external_system_target: dict[str, Any] | None = None,
) -> CampaignExternalExportResult:
    payload = build_high_level_work_package_payload(campaign, plan)
    return _export_campaign_package(
        payload,
        output_dir,
        package_kind="work_package_list",
        connector=connector,
        external_write=external_write,
        explicit_permission=explicit_permission,
        external_system_target=external_system_target,
    )


def link_external_workflow_task(
    integration_store: IntegrationStore,
    *,
    project_id: str,
    work_package_id: str,
    external_ref: ExternalRecordRef,
    created_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EntityMapping:
    return integration_store.create_mapping(
        EntityMapping(
            mapping_id=f"campaign-work-package-map-{uuid4().hex[:16]}",
            project_id=project_id,
            internal_entity_type="campaign_work_package",
            internal_entity_id=work_package_id,
            external_ref=external_ref,
            mapping_method="manual",
            mapping_confidence=1.0,
            status="active",
            created_by=created_by,
            metadata={
                "campaign_integration": True,
                "external_status_is_not_assay_evidence": True,
                **(metadata or {}),
            },
        )
    )


def import_external_status_update(
    campaign_store: CampaignStore,
    *,
    work_package_id: str,
    payload: dict[str, Any],
    external_ref: ExternalRecordRef | None = None,
    actor: str = "external_integration",
) -> CampaignExecutionEvent:
    current = campaign_store.get_work_package(work_package_id)
    external_status = _external_status(payload)
    status = EXTERNAL_STATUS_TO_WORK_PACKAGE_STATUS.get(external_status)
    if status is None:
        raise ValueError(f"Unsupported external campaign status: {external_status}")
    summary = _external_summary(payload, status)
    before = current.model_dump(mode="json")
    campaign_store.update_work_package_status(
        work_package_id,
        status,
        actor=actor,
        rationale=summary,
    )
    event = CampaignExecutionEvent(
        event_id=f"campaign-external-event-{uuid4().hex[:16]}",
        campaign_id=current.campaign_id,
        work_package_id=work_package_id,
        event_type=_event_type_for_status(status),
        actor=actor,
        timestamp=datetime.now(UTC),
        summary=summary,
        before={"status": current.status, "external_status": external_status},
        after={"status": status, "external_status": external_status},
        metadata={
            "external_ref": external_ref.model_dump(mode="json") if external_ref else None,
            "external_payload": _public_external_status_payload(payload),
            "does_not_create_assay_evidence": True,
            "assay_results_must_use_experimental_result_store": True,
            "source": "external_integration",
            "previous_work_package": before,
        },
    )
    campaign_store.add_execution_event(event)
    return event


def ingest_external_completion_event(
    campaign_store: CampaignStore,
    integration_store: IntegrationStore | None,
    *,
    work_package_id: str,
    payload: dict[str, Any],
    external_ref: ExternalRecordRef,
    sync_job_id: str | None = None,
    actor: str = "external_integration",
) -> CampaignExecutionEvent:
    event = import_external_status_update(
        campaign_store,
        work_package_id=work_package_id,
        payload=payload,
        external_ref=external_ref,
        actor=actor,
    )
    if integration_store is not None and sync_job_id is not None:
        integration_store.add_sync_record(
            SyncRecord(
                sync_record_id=f"sync-record-{uuid4().hex[:16]}",
                sync_job_id=sync_job_id,
                external_ref=external_ref,
                internal_entity_type="campaign_work_package",
                internal_entity_id=work_package_id,
                action="updated",
                status="succeeded",
                warnings=[
                    "External campaign status update did not create assay evidence.",
                ],
                metadata={
                    "campaign_execution_event_id": event.event_id,
                    "does_not_create_assay_evidence": True,
                },
            ),
            raw_payload=payload,
        )
    return event


def _export_campaign_package(
    payload: dict[str, Any],
    output_dir: str | Path,
    *,
    package_kind: CampaignPackageKind,
    connector: ExternalConnector | None,
    external_write: bool,
    explicit_permission: bool,
    external_system_target: dict[str, Any] | None,
) -> CampaignExternalExportResult:
    package_type = (
        "validation_handoff_package"
        if package_kind == "validation_handoff"
        else (
            "campaign_work_package_list_package"
            if package_kind == "work_package_list"
            else "campaign_summary_package"
        )
    )
    package = create_export_package(
        package_type,
        payload,
        output_dir,
        external_system_target=external_system_target,
        external_write=external_write,
        explicit_permission=explicit_permission,
    )
    connector_result = None
    external_refs: list[ExternalRecordRef] = []
    if connector is not None and external_write:
        connector_result = _write_connector_record(
            connector,
            package=package,
            payload=payload,
            package_kind=package_kind,
        )
        external_refs = _extract_external_refs(connector_result)
    return CampaignExternalExportResult(
        package=package,
        payload=payload,
        external_refs=external_refs,
        connector_result=connector_result,
    )


def _write_connector_record(
    connector: ExternalConnector,
    *,
    package: ExportPackageResult,
    payload: dict[str, Any],
    package_kind: CampaignPackageKind,
) -> dict[str, Any]:
    record = {
        "id": package.package_id,
        "package_id": package.package_id,
        "package_type": package.package_type,
        "campaign_id": payload.get("campaign_id"),
        "campaign_plan_id": payload.get("campaign_plan_id"),
        "title": f"Campaign {package_kind.replace('_', ' ')}",
        "summary": _connector_summary(payload, package_kind),
        "artifact_ids": [package.package_id],
        "source_record_id": package.package_id,
        "payload": payload,
    }
    if hasattr(connector, "create_notebook_entry"):
        return connector.create_notebook_entry(record)  # type: ignore[attr-defined]
    if hasattr(connector, "export_record"):
        return connector.export_record(record, object_type=package.package_type)  # type: ignore[attr-defined]
    return connector.export_records([record])


def _high_level_work_package_rows(
    work_packages: list[CampaignWorkPackage],
) -> list[dict[str, Any]]:
    rows = []
    for package in work_packages:
        row = {
            "work_package_id": package.work_package_id,
            "campaign_id": package.campaign_id,
            "objective_ids": package.objective_ids,
            "package_type": package.package_type,
            "title": package.title,
            "description": package.description,
            "status": package.status,
            "high_level_activity_category": package.high_level_activity_category,
            "linked_candidate_ids": package.linked_candidate_ids,
            "linked_hypothesis_ids": package.linked_hypothesis_ids,
            "dependencies": package.dependencies,
            "required_approvals": package.required_approvals,
            "estimated_review_hours": package.estimated_review_hours,
            "estimated_compute_units": package.estimated_compute_units,
            "estimated_assay_slots": package.estimated_assay_slots,
            "warnings": package.warnings,
        }
        _validate_campaign_export_payload(row)
        rows.append(row)
    return rows


def _validate_campaign_export_payload(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("protocol", "synthesis", "reagent", "dosing")):
                raise ValueError(
                    "Campaign exports must not include protocols or synthesis details."
                )
            _validate_campaign_export_payload(value)
        return
    if isinstance(payload, list):
        for item in payload:
            _validate_campaign_export_payload(item)
        return
    if isinstance(payload, str) and contains_procedural_lab_detail(payload):
        raise ValueError("Campaign exports must not include protocols or synthesis details.")


def _ensure_plan_matches_campaign(campaign: Campaign, plan: CampaignPlan) -> None:
    if plan.campaign_id != campaign.campaign_id:
        raise ValueError("Campaign plan does not belong to campaign.")


def _campaign_external_limitations() -> list[str]:
    return [
        "Campaign package is research-management guidance only.",
        "No procedural experimental, chemistry-route, administration, or clinical guidance "
        "is included.",
        "Selected candidates are not proven active, safe, effective, synthesizable, or "
        "clinically useful.",
        "External status updates do not create assay evidence unless result data is imported "
        "through ExperimentalResultStore.",
    ]


def _external_status(payload: dict[str, Any]) -> str:
    raw = payload.get("status") or payload.get("external_status") or payload.get("state")
    if raw is None:
        raise ValueError("External status update must include status.")
    return str(raw).strip().lower()


def _external_summary(payload: dict[str, Any], status: str) -> str:
    summary = str(payload.get("summary") or payload.get("message") or "").strip()
    if summary:
        return summary
    external_id = payload.get("external_task_id") or payload.get("id") or "external task"
    return f"External campaign work package status for {external_id} mapped to {status}."


def _event_type_for_status(status: str) -> CampaignExecutionEventType:
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "blocked"
    if status == "cancelled":
        return "cancelled"
    if status == "in_progress":
        return "started"
    if status == "approved":
        return "approved"
    return "review_decision_added"


def _public_external_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if str(key).lower() not in {"api_key", "authorization", "password", "secret", "token"}
    }


def _connector_summary(payload: dict[str, Any], package_kind: CampaignPackageKind) -> str:
    campaign_id = payload.get("campaign_id")
    count = len(payload.get("work_packages") or payload.get("work_package_ids") or [])
    return f"{package_kind} for campaign {campaign_id}; work packages: {count}."


def _extract_external_refs(value: Any) -> list[ExternalRecordRef]:
    refs: list[ExternalRecordRef] = []
    _collect_external_refs(value, refs)
    return refs


def _collect_external_refs(value: Any, refs: list[ExternalRecordRef]) -> None:
    if isinstance(value, ExternalRecordRef):
        refs.append(value)
        return
    if isinstance(value, dict):
        raw = value.get("external_ref")
        if isinstance(raw, ExternalRecordRef):
            refs.append(raw)
        elif isinstance(raw, dict):
            refs.append(ExternalRecordRef.model_validate(raw))
        for item in value.values():
            _collect_external_refs(item, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_external_refs(item, refs)


__all__ = [
    "CampaignExternalExportResult",
    "CampaignPackageKind",
    "build_campaign_summary_payload",
    "build_high_level_work_package_payload",
    "build_validation_handoff_payload",
    "export_campaign_summary_package",
    "export_high_level_work_package_list",
    "export_validation_handoff_package",
    "ingest_external_completion_event",
    "import_external_status_update",
    "link_external_workflow_task",
]
