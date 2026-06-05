from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.executor import CoPilotExecutor
from molecule_ranker.copilot.schemas import (
    ActionType,
    CoPilotAction,
    RiskLevel,
    SideEffectLevel,
)

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


class SyntheticRuntimeToolRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, tool_args))
        return {
            "summary": f"{tool_name} completed.",
            "artifact_ids": ["artifact-1"],
            "job_ids": ["job-1"],
        }


class SyntheticApprovalRequester:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def request_approval(self, action: CoPilotAction, reason: str) -> str:
        self.requests.append(f"{action.copilot_action_id}:{reason}")
        return "approval-1"


class SyntheticArtifactValidator:
    def __init__(self) -> None:
        self.validated: list[str] = []

    def validate(self, artifact_ids: list[str], *, action: CoPilotAction) -> list[str]:
        self.validated.extend(artifact_ids)
        return []


class SyntheticGuardrails:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.checked: list[str] = []

    def check(self, action: CoPilotAction) -> list[str]:
        self.checked.append(action.copilot_action_id)
        return ["guardrail failed"] if self.fail else []


class SyntheticRepairWorkflow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def repair(self, action: CoPilotAction, failure_summary: str) -> dict[str, Any]:
        self.calls.append(failure_summary)
        return {"artifact_ids": ["repair-artifact"], "summary": "Repair workflow started."}


def _action(
    action_type: ActionType = "summarize_status",
    *,
    action_id: str = "action-1",
    tool_name: str | None = "summarize_status",
    tool_args: dict[str, Any] | None = None,
    side_effect_level: SideEffectLevel = "none",
    risk_level: RiskLevel = "low",
    requires_approval: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CoPilotAction:
    return CoPilotAction(
        copilot_action_id=action_id,
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type=action_type,
        tool_name=tool_name,
        tool_args=tool_args or {},
        side_effect_level=side_effect_level,
        risk_level=risk_level,
        requires_approval=requires_approval,
        approval_reason=None,
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata=metadata or {},
    )


def test_executor_runs_safe_action_through_runtime_registry_and_validates_artifacts():
    registry = SyntheticRuntimeToolRegistry()
    artifact_validator = SyntheticArtifactValidator()
    guardrails = SyntheticGuardrails()
    audit: list[dict[str, Any]] = []
    executor = CoPilotExecutor(
        runtime_tool_registry=registry,
        artifact_validator=artifact_validator,
        guardrails=guardrails,
        audit_writer=lambda record: audit.append(record),
        now=lambda: NOW,
    )

    result = executor.execute(_action("summarize_status"))

    assert result.status == "succeeded"
    assert result.artifact_ids == ["artifact-1"]
    assert registry.calls == [("summarize_status", {})]
    assert artifact_validator.validated == ["artifact-1"]
    assert guardrails.checked == ["action-1"]
    assert [record["transition"] for record in audit] == ["guardrails_passed", "succeeded"]


def test_executor_requests_approval_for_active_campaign_replan_change():
    approvals = SyntheticApprovalRequester()
    registry = SyntheticRuntimeToolRegistry()
    executor = CoPilotExecutor(
        runtime_tool_registry=registry,
        approval_requester=approvals,
        now=lambda: NOW,
    )
    action = _action(
        "run_campaign_replan",
        tool_name="run_campaign_replan",
        side_effect_level="db_write",
        risk_level="high",
        metadata={"changes_active_plan": True},
    )

    result = executor.execute(action, autonomy_level="execute_with_approval")

    assert result.status == "approval_required"
    assert approvals.requests
    assert registry.calls == []
    assert result.metadata["approval_request_id"] == "approval-1"


def test_executor_blocks_stage_gate_approval_action():
    registry = SyntheticRuntimeToolRegistry()
    executor = CoPilotExecutor(runtime_tool_registry=registry, now=lambda: NOW)
    action = _action(
        "request_approval",
        tool_name="approve_stage_gate",
        metadata={"requested_action_type": "approve_stage_gate"},
    )

    result = executor.execute(action)

    assert result.status == "blocked_by_policy"
    assert "stage gate" in result.summary
    assert registry.calls == []


def test_executor_guardrail_failure_blocks_and_starts_safe_repair():
    registry = SyntheticRuntimeToolRegistry()
    repair = SyntheticRepairWorkflow()
    executor = CoPilotExecutor(
        runtime_tool_registry=registry,
        guardrails=SyntheticGuardrails(fail=True),
        repair_workflow=repair,
        now=lambda: NOW,
    )
    action = _action(
        "run_repair_workflow",
        tool_name="run_repair_workflow",
        metadata={"safe_repair": True},
    )

    result = executor.execute(action)

    assert result.status == "guardrail_failed"
    assert "guardrail failed" in result.summary
    assert result.artifact_ids == ["repair-artifact"]
    assert repair.calls == ["guardrail failed"]
    assert registry.calls == []


def test_executor_blocks_protocol_synthesis_or_dosing_payloads_before_tool_call():
    registry = SyntheticRuntimeToolRegistry()
    executor = CoPilotExecutor(runtime_tool_registry=registry, now=lambda: NOW)
    action = CoPilotAction.model_construct(
        copilot_action_id="action-unsafe",
        campaign_id="camp-1",
        trigger_id="trigger-1",
        action_type="summarize_status",
        tool_name="summarize_status",
        tool_args={"note": "contains dosing details"},
        side_effect_level="none",
        risk_level="low",
        requires_approval=False,
        approval_reason=None,
        status="queued",
        created_at=NOW,
        completed_at=None,
        metadata={},
    )

    result = executor.execute(action)

    assert result.status == "blocked_by_policy"
    assert "protocol/synthesis/dosing" in result.summary
    assert registry.calls == []
