from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.integrations.schemas import DataContract
from molecule_ranker.integrations.validation import validate_data_contract

SyncDirection = Literal["import", "export", "bidirectional"]
SyncPlanMode = Literal["dry_run", "read_only", "write_approved"]
RequestedSyncMode = Literal["dry_run", "read_only", "write"]
MappingState = Literal[
    "active",
    "pending_review",
    "unknown",
    "codex_suggested",
    "rejected",
]
HealthState = Literal["ok", "degraded", "unconfigured", "blocked"]


class ExternalSyncPlannerModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ExternalSyncPlannerRequest(ExternalSyncPlannerModel):
    project_id: str | None = None
    campaign_id: str | None = None
    external_system_ids: list[str] = Field(default_factory=list)
    direction: SyncDirection = "import"
    object_types: list[str] = Field(default_factory=lambda: ["assay_result"])
    requested_mode: RequestedSyncMode = "dry_run"
    planned_records: list[dict[str, Any]] = Field(default_factory=list)
    data_contracts: dict[str, DataContract] = Field(default_factory=dict)
    mapping_status: dict[str, MappingState] = Field(default_factory=dict)
    credential_refs: dict[str, str | None] = Field(default_factory=dict)
    external_system_health: dict[str, HealthState] = Field(default_factory=dict)
    user_permissions: list[str] = Field(default_factory=list)
    governance_policies: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncPlan(ExternalSyncPlannerModel):
    sync_plan_id: str = Field(default_factory=lambda: f"sync-plan-{uuid4().hex[:16]}")
    project_id: str | None = None
    external_system_ids: list[str]
    direction: SyncDirection
    object_types: list[str]
    mode: SyncPlanMode
    planned_records: list[dict[str, Any]]
    required_mappings: list[str]
    expected_artifacts: list[str]
    approval_requirements: list[str]
    risk_summary: dict[str, Any]
    dry_run: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalSyncPlanner:
    """Plans governed external syncs without resolving secrets or inventing mappings."""

    _WRITE_PERMISSION = "integration:write"

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def plan(self, request: ExternalSyncPlannerRequest) -> SyncPlan:
        risk_reasons: list[str] = []
        required_mappings = self._required_mappings(request)
        credential_checks = self._credential_checks(request)
        unhealthy_systems = self._unhealthy_systems(request)
        missing_health = [
            system_id
            for system_id in request.external_system_ids
            if system_id not in request.external_system_health
        ]
        contract_summary = self._contract_summary(request)
        approval_requirements = self._approval_requirements(request)

        requested_write = request.requested_mode == "write"
        mode: SyncPlanMode
        if requested_write:
            mode = "dry_run"
            risk_reasons.append("write sync defaults to dry-run")
        elif request.requested_mode == "read_only":
            mode = "read_only"
        else:
            mode = "dry_run"

        if required_mappings:
            mode = "dry_run"
            risk_reasons.append("unknown mappings block write sync")

        if unhealthy_systems or missing_health:
            mode = "dry_run"
            risk_reasons.append("external system health required")

        if not contract_summary["contract_validation_passed"]:
            mode = "dry_run"
            risk_reasons.append("data contracts must validate before sync")

        if not self._credentials_available(credential_checks):
            mode = "dry_run"
            risk_reasons.append("credentials must be available by reference")

        if requested_write and self._write_authorized(request) and not risk_reasons:
            mode = "write_approved"

        codex_blocked = any(
            request.mapping_status.get(object_type) == "codex_suggested"
            for object_type in request.object_types
        )
        risk_summary = {
            "write_sync_defaulted_to_dry_run": requested_write and mode == "dry_run",
            "blocked_write": requested_write and mode == "dry_run",
            "health_required": bool(unhealthy_systems or missing_health),
            "unhealthy_external_system_ids": unhealthy_systems + missing_health,
            "contract_validation_passed": contract_summary["contract_validation_passed"],
            "invalid_contract_object_types": contract_summary["invalid_contract_object_types"],
            "codex_invented_mappings_blocked": codex_blocked,
            "credentials_checked_by_reference_only": True,
            "reasons": risk_reasons,
        }

        expected_artifacts = [
            f"sync_plan_{request.direction}",
            "data_contract_validation_report",
            "mapping_review_queue" if required_mappings else "mapping_resolution_summary",
        ]
        if mode == "write_approved":
            expected_artifacts.append("approved_external_sync_receipt")

        return SyncPlan(
            project_id=request.project_id,
            external_system_ids=list(request.external_system_ids),
            direction=request.direction,
            object_types=list(request.object_types),
            mode=mode,
            planned_records=[self._sanitize_record(record) for record in request.planned_records],
            required_mappings=required_mappings,
            expected_artifacts=expected_artifacts,
            approval_requirements=approval_requirements,
            risk_summary=risk_summary,
            dry_run=mode == "dry_run",
            metadata={
                "campaign_id": request.campaign_id,
                "credential_checks": credential_checks,
                "contract_reports": contract_summary["contract_reports"],
                "governance_policy_ids": request.governance_policies.get("policy_ids", []),
                "created_at": self._now().isoformat(),
                **self._sanitize_record(request.metadata),
            },
        )

    def _approval_requirements(
        self, request: ExternalSyncPlannerRequest
    ) -> list[str]:
        requirements: list[str] = []
        if request.requested_mode == "write" or request.direction in {"export", "bidirectional"}:
            requirements.append("external_write")
        if self._WRITE_PERMISSION not in request.user_permissions and requirements:
            requirements.append(self._WRITE_PERMISSION)
        if (
            request.governance_policies.get("external_write_requires_approval", True)
            and requirements
            and not request.governance_policies.get("external_write_approval_id")
        ):
            requirements.append("human_approval")
        return sorted(set(requirements))

    def _required_mappings(self, request: ExternalSyncPlannerRequest) -> list[str]:
        required: list[str] = []
        for object_type in request.object_types:
            status = request.mapping_status.get(object_type)
            if status in {None, "unknown", "pending_review", "codex_suggested", "rejected"}:
                required.append(object_type)
        return required

    def _credential_checks(
        self, request: ExternalSyncPlannerRequest
    ) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {}
        for system_id in request.external_system_ids:
            ref = request.credential_refs.get(system_id)
            checks[system_id] = {
                "available": bool(ref),
                "credential_ref_present": bool(ref),
                "checked_by_reference_only": True,
                "credential_ref_id": self._credential_ref_id(ref),
            }
        return checks

    def _credential_ref_id(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ":" in ref:
            prefix, _value = ref.split(":", 1)
            return f"{prefix}:[REDACTED]"
        return ref

    def _credentials_available(self, checks: dict[str, dict[str, Any]]) -> bool:
        return all(check["available"] for check in checks.values())

    def _unhealthy_systems(self, request: ExternalSyncPlannerRequest) -> list[str]:
        return [
            system_id
            for system_id, status in request.external_system_health.items()
            if system_id in request.external_system_ids and status != "ok"
        ]

    def _contract_summary(self, request: ExternalSyncPlannerRequest) -> dict[str, Any]:
        invalid_object_types: list[str] = []
        reports: dict[str, Any] = {}
        for object_type in request.object_types:
            contract = request.data_contracts.get(object_type)
            if contract is None:
                if request.planned_records:
                    invalid_object_types.append(object_type)
                    reports[object_type] = {"valid": False, "reason": "missing_contract"}
                continue
            records = [
                record
                for record in request.planned_records
                if self._record_matches_object_type(record, object_type)
            ] or request.planned_records
            report = validate_data_contract(records, contract) if records else None
            if report is not None:
                reports[object_type] = report.model_dump(mode="json")
                if not report.valid:
                    invalid_object_types.append(object_type)
        return {
            "contract_validation_passed": not invalid_object_types,
            "invalid_contract_object_types": invalid_object_types,
            "contract_reports": reports,
        }

    def _record_matches_object_type(
        self, record: dict[str, Any], object_type: str
    ) -> bool:
        observed_type = record.get("object_type") or record.get("record_type") or object_type
        return str(observed_type) == object_type

    def _write_authorized(self, request: ExternalSyncPlannerRequest) -> bool:
        return (
            self._WRITE_PERMISSION in request.user_permissions
            and bool(request.governance_policies.get("external_write_approval_id"))
        )

    def _sanitize_record(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, raw in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(
                    marker in normalized
                    for marker in ("api_key", "authorization", "password", "secret", "token")
                ):
                    sanitized[key] = "[REDACTED]"
                else:
                    sanitized[key] = self._sanitize_record(raw)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_record(item) for item in value]
        return value


__all__ = [
    "ExternalSyncPlanner",
    "ExternalSyncPlannerRequest",
    "MappingState",
    "RequestedSyncMode",
    "SyncDirection",
    "SyncPlan",
    "SyncPlanMode",
]
