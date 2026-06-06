from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agents.integration_repair import (
    IntegrationFailureReport,
    IntegrationRepairAgent,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _agent() -> IntegrationRepairAgent:
    return IntegrationRepairAgent(now=lambda: NOW)


def test_credential_missing_requests_update_without_changing_credentials() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="credential_missing",
            sync_job_id="sync-1",
            external_system_id="lims-1",
            summary="Credential reference is missing.",
        )
    )

    assert plan.requires_human_approval is True
    assert plan.actions[0].action_type == "request_missing_input"
    assert plan.actions[0].metadata["repair_action"] == "request_credential_update"
    assert plan.actions[0].metadata["automatic_credential_change"] is False
    assert "No automatic credential changes." in plan.scientific_guardrails


def test_data_contract_mismatch_regenerates_contract_from_sample_and_revalidates() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="data_contract_mismatch",
            sync_job_id="sync-1",
            external_system_id="warehouse-1",
            summary="Source fields no longer match the contract.",
            sample_payload_artifact_id="payload-sample-1",
        )
    )

    action_names = [action.metadata["repair_action"] for action in plan.actions]
    assert action_names == [
        "regenerate_data_contract_from_sample",
        "revalidate_data_contract",
    ]
    assert all(action.side_effect_level != "external_write" for action in plan.actions)
    assert "No bypassing data contracts." in plan.scientific_guardrails


def test_mapping_conflict_creates_mapping_review_not_codex_approval() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="mapping_conflict",
            sync_job_id="sync-1",
            external_system_id="registry-1",
            summary="Two registry records match the same candidate.",
            mapping_id="mapping-1",
        )
    )

    assert plan.actions[0].action_type == "create_issue_report"
    assert plan.actions[0].metadata["repair_action"] == "create_mapping_review"
    assert plan.actions[0].metadata["codex_can_approve_mapping"] is False
    assert plan.requires_human_approval is True


def test_webhook_signature_failure_quarantines_payload_and_creates_support_bundle() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="webhook_signature_failure",
            sync_job_id="sync-1",
            external_system_id="webhook-1",
            summary="Webhook signature verification failed.",
            payload_artifact_id="payload-1",
        )
    )

    action_names = [action.metadata["repair_action"] for action in plan.actions]
    assert action_names == ["quarantine_payload", "create_support_bundle"]
    assert plan.actions[0].action_type == "quarantine_artifact"
    assert plan.actions[0].requires_approval is True
    assert plan.actions[1].action_type == "create_issue_report"


def test_unsafe_write_attempt_requires_admin_approval_and_no_retry() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="unsafe_write_attempt",
            sync_job_id="sync-1",
            external_system_id="eln-1",
            summary="Write attempted without governance approval.",
            idempotent=False,
            approved=False,
        )
    )

    assert plan.actions[0].action_type == "request_human_approval"
    assert plan.actions[0].metadata["repair_action"] == "request_admin_approval"
    assert all(action.action_type != "rerun_tool" for action in plan.actions)
    assert plan.metadata["automatic_write_retry_allowed"] is False


def test_idempotent_approved_write_can_retry_with_approval_marker() -> None:
    plan = _agent().plan_repair(
        IntegrationFailureReport(
            failure_type="partial_sync_failure",
            sync_job_id="sync-1",
            external_system_id="eln-1",
            summary="Approved idempotent write sync partially failed.",
            idempotent=True,
            approved=True,
            approval_id="approval-1",
        )
    )

    action_names = [action.metadata["repair_action"] for action in plan.actions]
    assert "mark_partial_sync" in action_names
    assert "retry_idempotent_approved_write" in action_names
    retry = next(
        action
        for action in plan.actions
        if action.metadata["repair_action"] == "retry_idempotent_approved_write"
    )
    assert retry.side_effect_level == "external_write"
    assert retry.requires_approval is True
    assert retry.metadata["approval_id"] == "approval-1"
