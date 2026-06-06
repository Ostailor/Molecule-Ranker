from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.agent_repair.schemas import (
    FailureCategory,
    FailureDiagnosis,
    Repairability,
    RepairAction,
    RepairPlan,
)

IntegrationFailureType = Literal[
    "credential_missing",
    "credential_expired",
    "health_check_failed",
    "data_contract_mismatch",
    "mapping_conflict",
    "webhook_signature_failure",
    "duplicate_external_record",
    "external_system_timeout",
    "partial_sync_failure",
    "unsafe_write_attempt",
    "payload_too_large",
    "schema_drift",
]

INTEGRATION_REPAIR_GUARDRAILS = [
    "No automatic credential changes.",
    "No automatic write retry unless idempotent and approved.",
    "No fabrication of external records.",
    "No bypassing data contracts.",
]


class IntegrationRepairModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class IntegrationFailureReport(IntegrationRepairModel):
    failure_type: IntegrationFailureType
    sync_job_id: str
    external_system_id: str
    summary: str
    mapping_id: str | None = None
    payload_artifact_id: str | None = None
    sample_payload_artifact_id: str | None = None
    duplicate_record_id: str | None = None
    idempotent: bool = False
    approved: bool = False
    approval_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationRepairAgent:
    """Plans integration-specific repairs through V2.4 repair schemas."""

    name = "IntegrationRepairAgent"

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def plan_repair(self, failure: IntegrationFailureReport) -> RepairPlan:
        diagnosis = self._diagnosis(failure)
        actions = self._actions(failure, diagnosis)
        automatic_write_retry_allowed = any(
            action.metadata.get("repair_action") == "retry_idempotent_approved_write"
            for action in actions
        )
        return RepairPlan(
            repair_plan_id=f"integration-repair-plan-{uuid4().hex[:12]}",
            diagnosis_id=diagnosis.diagnosis_id,
            session_id=None,
            plan_summary=f"Integration repair plan for {failure.failure_type}.",
            actions=actions,
            expected_artifacts=self._expected_artifacts(actions),
            rollback_plan=[],
            requires_human_approval=any(action.requires_approval for action in actions),
            scientific_guardrails=list(INTEGRATION_REPAIR_GUARDRAILS),
            validated=True,
            validation_errors=[],
            created_by="deterministic",
            created_at=self._now(),
            metadata={
                "repair_domain": "integration",
                "failure_type": failure.failure_type,
                "sync_job_id": failure.sync_job_id,
                "external_system_id": failure.external_system_id,
                "automatic_credential_change_allowed": False,
                "automatic_write_retry_allowed": automatic_write_retry_allowed,
            },
        )

    def plan(self, failure: IntegrationFailureReport) -> RepairPlan:
        return self.plan_repair(failure)

    def _diagnosis(self, failure: IntegrationFailureReport) -> FailureDiagnosis:
        category, repairability, recoverable = self._diagnosis_classification(failure)
        return FailureDiagnosis(
            diagnosis_id=f"integration-failure-{uuid4().hex[:12]}",
            failure_object_type="integration_sync",
            failure_object_id=failure.sync_job_id,
            failure_category=category,
            root_cause_summary=failure.summary,
            evidence=[
                {
                    "source": "integration_failure_report",
                    "failure_type": failure.failure_type,
                    "sync_job_id": failure.sync_job_id,
                    "external_system_id": failure.external_system_id,
                }
            ],
            recoverable=recoverable,
            repairability=repairability,
            confidence=0.9,
            warnings=[],
            created_at=self._now(),
            metadata={
                "integration_failure_type": failure.failure_type,
                "deterministic_integration_repair": True,
            },
        )

    def _diagnosis_classification(
        self, failure: IntegrationFailureReport
    ) -> tuple[FailureCategory, Repairability, bool]:
        if failure.failure_type in {"credential_missing", "credential_expired"}:
            return "missing_input", "human_input_required", True
        if failure.failure_type in {"data_contract_mismatch", "schema_drift"}:
            return "validation_failed", "automatic_safe", True
        if failure.failure_type == "mapping_conflict":
            return "inconsistent_artifacts", "human_input_required", True
        if failure.failure_type in {
            "health_check_failed",
            "external_system_timeout",
            "partial_sync_failure",
        }:
            return "external_unavailable", "automatic_with_limits", True
        if failure.failure_type == "unsafe_write_attempt":
            return "policy_blocked", "approval_required", True
        if failure.failure_type in {
            "webhook_signature_failure",
            "payload_too_large",
            "duplicate_external_record",
        }:
            return "guardrail_failed", "approval_required", True
        return "unknown", "human_input_required", False

    def _actions(
        self,
        failure: IntegrationFailureReport,
        diagnosis: FailureDiagnosis,
    ) -> list[RepairAction]:
        if failure.failure_type in {"credential_missing", "credential_expired"}:
            return [self._request_credential_update(failure, diagnosis)]
        if failure.failure_type == "health_check_failed":
            return [self._run_health_check_later(failure, diagnosis)]
        if failure.failure_type in {"data_contract_mismatch", "schema_drift"}:
            return [
                self._regenerate_data_contract(failure, diagnosis),
                self._revalidate_data_contract(failure, diagnosis),
            ]
        if failure.failure_type == "mapping_conflict":
            return [self._create_mapping_review(failure, diagnosis)]
        if failure.failure_type in {"webhook_signature_failure", "payload_too_large"}:
            return [
                self._quarantine_payload(failure, diagnosis),
                self._create_support_bundle(failure, diagnosis),
            ]
        if failure.failure_type == "duplicate_external_record":
            return [
                self._quarantine_payload(failure, diagnosis),
                self._create_support_bundle(failure, diagnosis),
            ]
        if failure.failure_type == "external_system_timeout":
            return [self._retry_read_only_sync(failure, diagnosis)]
        if failure.failure_type == "partial_sync_failure":
            actions = [self._mark_partial_sync(failure, diagnosis)]
            if failure.idempotent and failure.approved and failure.approval_id:
                actions.append(self._retry_idempotent_approved_write(failure, diagnosis))
            else:
                actions.append(self._retry_read_only_sync(failure, diagnosis))
            return actions
        if failure.failure_type == "unsafe_write_attempt":
            return [self._request_admin_approval(failure, diagnosis)]
        return [self._create_support_bundle(failure, diagnosis)]

    def _request_credential_update(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="request_missing_input",
            target_object_type="integration_credential",
            expected_effect="Request a credential reference update from an authorized human.",
            side_effect_level="none",
            requires_approval=True,
            risk_level="medium",
            repair_action="request_credential_update",
            metadata={"automatic_credential_change": False},
        )

    def _run_health_check_later(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="retry_external_read",
            target_object_type="integration_health",
            expected_effect="Schedule a later read-only health check.",
            side_effect_level="external_read",
            requires_approval=False,
            risk_level="low",
            repair_action="run_health_check_later",
        )

    def _regenerate_data_contract(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="regenerate_artifact",
            target_object_type="data_contract",
            target_object_id=failure.sample_payload_artifact_id or failure.sync_job_id,
            expected_effect="Regenerate the data contract from a quarantined sample payload.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="low",
            repair_action="regenerate_data_contract_from_sample",
            metadata={"sample_payload_artifact_id": failure.sample_payload_artifact_id},
        )

    def _revalidate_data_contract(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="revalidate_artifact",
            target_object_type="data_contract",
            expected_effect="Revalidate the contract before any sync can affect scoring.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
            repair_action="revalidate_data_contract",
            metadata={"bypass_data_contract": False},
        )

    def _create_mapping_review(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="create_issue_report",
            target_object_type="mapping_review",
            target_object_id=failure.mapping_id or failure.sync_job_id,
            expected_effect="Create a human mapping review item.",
            side_effect_level="artifact_write",
            requires_approval=True,
            risk_level="medium",
            repair_action="create_mapping_review",
            metadata={"codex_can_approve_mapping": False},
        )

    def _quarantine_payload(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="quarantine_artifact",
            target_object_type="integration_payload",
            target_object_id=failure.payload_artifact_id
            or failure.duplicate_record_id
            or failure.sync_job_id,
            expected_effect="Quarantine unsafe or duplicate integration payload.",
            side_effect_level="artifact_write",
            requires_approval=True,
            risk_level="medium",
            repair_action="quarantine_payload",
            metadata={"fabricates_external_record": False},
        )

    def _retry_read_only_sync(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="retry_external_read",
            target_object_type="integration_sync",
            expected_effect="Retry sync in read-only mode.",
            side_effect_level="external_read",
            requires_approval=False,
            risk_level="low",
            repair_action="retry_read_only_sync",
            metadata={"external_write_retry": False},
        )

    def _mark_partial_sync(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="mark_skipped",
            target_object_type="integration_sync",
            expected_effect="Mark the sync as partial and keep failed records visible.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="low",
            repair_action="mark_partial_sync",
            metadata={"hide_failed_records": False},
        )

    def _request_admin_approval(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="request_human_approval",
            target_object_type="integration_sync",
            expected_effect="Request admin approval before any external write retry.",
            side_effect_level="none",
            requires_approval=True,
            risk_level="high",
            repair_action="request_admin_approval",
            metadata={"automatic_write_retry": False},
        )

    def _retry_idempotent_approved_write(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="rerun_tool",
            target_object_type="integration_sync",
            expected_effect="Retry an idempotent write sync using the existing approval.",
            side_effect_level="external_write",
            requires_approval=True,
            approval_reason="External write retry requires existing human approval.",
            risk_level="high",
            repair_action="retry_idempotent_approved_write",
            metadata={
                "idempotent": True,
                "approval_id": failure.approval_id,
                "codex_approved_write": False,
            },
        )

    def _create_support_bundle(
        self, failure: IntegrationFailureReport, diagnosis: FailureDiagnosis
    ) -> RepairAction:
        return self._action(
            failure,
            diagnosis,
            action_type="create_issue_report",
            target_object_type="support_bundle",
            expected_effect="Create an integration support bundle for administrator review.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="low",
            repair_action="create_support_bundle",
            metadata={"contains_secret_values": False},
        )

    def _action(
        self,
        failure: IntegrationFailureReport,
        diagnosis: FailureDiagnosis,
        *,
        action_type: str,
        target_object_type: str,
        expected_effect: str,
        side_effect_level: str,
        requires_approval: bool,
        risk_level: str,
        repair_action: str,
        target_object_id: str | None = None,
        approval_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RepairAction:
        return RepairAction(
            repair_action_id=f"integration-repair-action-{uuid4().hex[:12]}",
            action_type=action_type,  # type: ignore[arg-type]
            target_object_type=target_object_type,
            target_object_id=target_object_id or failure.sync_job_id,
            tool_name=None,
            tool_args={},
            expected_effect=expected_effect,
            side_effect_level=side_effect_level,  # type: ignore[arg-type]
            requires_approval=requires_approval,
            approval_reason=approval_reason
            or (
                "Human approval required by integration repair policy."
                if requires_approval
                else None
            ),
            risk_level=risk_level,  # type: ignore[arg-type]
            metadata={
                "repair_action": repair_action,
                "failure_type": failure.failure_type,
                "diagnosis_id": diagnosis.diagnosis_id,
                "external_system_id": failure.external_system_id,
                **(metadata or {}),
            },
        )

    def _expected_artifacts(self, actions: list[RepairAction]) -> list[str]:
        artifacts = []
        for action in actions:
            repair_action = action.metadata.get("repair_action")
            if repair_action in {
                "regenerate_data_contract_from_sample",
                "create_mapping_review",
                "create_support_bundle",
                "quarantine_payload",
                "mark_partial_sync",
            }:
                artifacts.append(str(repair_action))
        return sorted(set(artifacts))


__all__ = [
    "IntegrationFailureReport",
    "IntegrationFailureType",
    "IntegrationRepairAgent",
]
